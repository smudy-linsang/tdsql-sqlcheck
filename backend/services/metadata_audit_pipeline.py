# -*- coding: utf-8 -*-
"""在线元数据提取 + 流式审核管道（v1.6.3.5 / DU-2 / FIX-01+02 / D04）。

设计出处：docs/DETAIL-v1.6.3.5-大库在线元数据审核稳定性修复.md §5.3。

从旧同步路由 `sql_audit.extract_and_audit` 抽取的只读提取逻辑，供独立 metadata
worker 子进程调用。关键差异（对比旧路由）：
  · 逐对象 SHOW CREATE 失败/空 DDL/超时**记录定位并终止**（FAILED），不再 warning
    后继续产出"全库完成"假报告；
  · 审核走 `checker.iter_audit_file` 流式（DU-1 的 R035 有界见证索引），
    逐条产出 AuditResult，可边审边写 results.ndjson，不全批驻留内存；
  · SQL 只作文本审核，不执行任何目标库 DDL/DML。
"""

import json
import logging
import re
from typing import Iterator, Optional

from backend.engine.checker import RuleChecker

logger = logging.getLogger("tdsql.metadata_pipeline")

VALID_SCOPES = ("TABLE", "INDEX", "VIEW", "SHARDKEY")

# v1.6.3.6 / BUG-01：TDSQL 分布式/二级分区的底层物理分片子表命名模式。
# 主表 DDL 已包含全部分区/分片定义，子表无需也不能单独提取。
# SIT-A/R2-M-02（Mr.Linsang 决策）：命名模式**不再决定要不要提取**（取消前置过滤，
# 一律先尝试 SHOW CREATE），只在"这次已经失败的提取"里用来分类良性/异常。
# \d+ 要求至少一位数字（不写 \d*）。
_TDSQL_INTERNAL_PARTITION_PATTERN = re.compile(
    r"^(?P<parent>.+?)_tdsql_(?:subp|shard)\d+$", re.IGNORECASE)
# Proxy"对象不存在"错误码（660=Proxy ERROR does not exist；1146=MySQL table doesn't exist）
_OBJ_NOT_EXIST_CODES = ("660", "1146")


def classify_extract_failure(table_name: str, error, instance_type: str,
                             available_tables: set) -> str:
    """把一次**已经失败**的 DDL 提取归类为 'tdsql_internal'（良性物理子表）或
    'extract_failed'（异常：权限/超时/真故障，需人工核实）。

    SIT-A/R2-M-02：本函数**不参与**"要不要提取"的决策——所有对象一律先尝试
    SHOW CREATE，失败之后才调用它。命名模式在此最坏只是把异常错记成良性（计数
    分类错），绝不造成"能读的真业务表被跳过"（漏审）。四个参数全部必传。

    判为良性需四条同时成立：
      a) instance_type == "distributed"（集中式无物理子表）；
      b) 错误是"对象不存在"（Proxy 660 / MySQL 1146）；
      c) 名字匹配 `<父表>_tdsql_subp|shard<数字>`；
      d) 父表在本库枚举清单 available_tables 中。
    """
    if (instance_type or "").lower() != "distributed":
        return "extract_failed"
    # 条件 b：错误码是"对象不存在"（从异常 args[0] 整数或文本 (NNNN, 提取）
    code = ""
    args = getattr(error, "args", None)
    if args and isinstance(args[0], int):
        code = str(args[0])
    else:
        m = re.search(r"\((\d{3,4})\s*,", str(error))
        code = m.group(1) if m else ""
    if code not in _OBJ_NOT_EXIST_CODES:
        return "extract_failed"
    m = _TDSQL_INTERNAL_PARTITION_PATTERN.match(table_name or "")
    if not m:
        return "extract_failed"
    if m.group("parent") not in (available_tables or set()):
        return "extract_failed"
    return "tdsql_internal"


class MetadataExtractError(Exception):
    """提取失败（对象级失败/权限/超时/空 DDL），携带定位信息。"""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def quote_identifier(value: str) -> str:
    """库名/对象名 SQL 标识符转义（反引号包围，内层反引号翻倍），拒绝 NUL。"""
    if not value or "\x00" in value:
        raise ValueError("invalid identifier")
    return "`" + value.replace("`", "``") + "`"


def sanitize_comment(text: str) -> str:
    """SQL 注释文本去除 CR/LF 注入（报告头部实例/库名展示用）。"""
    return str(text or "").replace("\r", " ").replace("\n", " ")


def enumerate_objects(conn, target_db: str) -> list:
    """枚举库下所有表与视图（保持 information_schema 返回的原始顺序）。

    返回 [{name, type}]；type 为 information_schema.TABLE_TYPE（BASE TABLE/VIEW）。
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = %s", (target_db,))
    rows = cursor.fetchall()
    out = []
    for r in rows:
        name = (r.get("TABLE_NAME") or r.get("table_name")) if isinstance(r, dict) else r[0]
        ttype = (r.get("TABLE_TYPE") or r.get("table_type")) if isinstance(r, dict) else r[1]
        if name:
            out.append({"name": name, "type": str(ttype or "").upper()})
    return out


def _select_objects(objects: list, scopes: list) -> list:
    """按 scope 选择可审核对象（TABLE/VIEW）；INDEX/SHARDKEY 随完整表 DDL 一并提取。"""
    want_table = "TABLE" in scopes
    want_view = "VIEW" in scopes
    out = []
    for o in objects:
        t = o["type"].upper()
        if want_table and "VIEW" not in t:
            out.append(o)
        elif want_view and "VIEW" in t:
            out.append(o)
    return out


def _show_create(conn, target_db: str, obj: dict) -> str:
    """SHOW CREATE TABLE/VIEW 单对象；失败/空 DDL 抛 MetadataExtractError。"""
    name, ttype = obj["name"], obj["type"]
    kind = "VIEW" if "VIEW" in ttype else "TABLE"
    cursor = conn.cursor()
    try:
        cursor.execute(f"SHOW CREATE {kind} {quote_identifier(target_db)}.{quote_identifier(name)}")
        row = cursor.fetchone()
    except Exception as e:
        raise MetadataExtractError(
            "EXTRACT_OBJECT_FAILED",
            f"对象 {target_db}.{name}（{kind}）DDL 读取失败: {e}") from e
    if not row:
        raise MetadataExtractError(
            "EXTRACT_OBJECT_FAILED",
            f"对象 {target_db}.{name}（{kind}）DDL 为空（可能并发删表/无权）")
    vals = list(row.values()) if isinstance(row, dict) else list(row)
    ddl = ""
    for v in vals:
        s = str(v or "").strip()
        if "CREATE" in s.upper():
            ddl = s
            break
    if not ddl:
        raise MetadataExtractError(
            "EXTRACT_OBJECT_FAILED",
            f"对象 {target_db}.{name}（{kind}）未返回 CREATE 文本")
    return ddl


def extract_metadata(pool, target_db: str, scopes: list,
                     instance_label: str,
                     instance_type: str) -> tuple:
    """只读提取目标库元数据为 SQL 行列表。

    Args:
        pool: TDSQLConnectionPool（只读连接）
        target_db: 最终实际库名
        scopes: TABLE/INDEX/VIEW/SHARDKEY 子集
        instance_label: 冻结的实例连接名称（写入 SQL 头部注释，需去 CRLF）
        instance_type: 实例类型（centralized/distributed）；集中式不剔除任何"子表"

    Returns:
        (sql_lines: list[str], stats: dict)
    """
    from datetime import datetime
    scopes = [s.upper() for s in (scopes or [])]
    lines = [
        "-- ============================================================================",
        "-- TDSQL 自动拉取的最新在线元数据描述文件",
        f"-- 目标实例: {sanitize_comment(instance_label)}",
        f"-- 目标数据库: {sanitize_comment(target_db)}",
        f"-- 提取日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "-- ============================================================================",
        "",
    ]
    with pool.get_connection() as conn:
        objects = enumerate_objects(conn, target_db)
        enumerated = len(objects)
        available_tables = {o["name"] for o in objects}
        selected_objs = _select_objects(objects, scopes)
        selected = len(selected_objs)
        if selected == 0:
            raise MetadataExtractError(
                "NO_AUDITABLE_OBJECTS",
                f"库 {target_db} 在选择范围 {scopes} 内没有可审核对象"
                f"（枚举到 {enumerated} 个对象）。")
        extracted = 0
        skipped_objects = []
        benign_skipped = 0        # TDSQL 物理子表（预期内、良性）
        abnormal_skipped = 0      # 权限/超时/Proxy 故障等（异常、需要人管）
        for obj in selected_objs:
            kind = "VIEW" if "VIEW" in obj["type"] else "TABLE"
            obj_name = obj["name"]
            # SIT-A/R2-M-02：取消前置过滤——所有选中对象一律先尝试 SHOW CREATE。
            # 命名模式不再决定"要不要提取"；只在"已失败"后用来分类良性/异常。
            try:
                ddl = _show_create(conn, target_db, obj)
            except Exception as e:
                category = classify_extract_failure(
                    obj_name, e, instance_type, available_tables)
                if category == "tdsql_internal":
                    benign_skipped += 1
                    logger.debug("跳过 TDSQL 内部物理子表 %s.%s", target_db, obj_name)
                    # SIT-A/R3-B-01：良性跳过**不逐个写 [SKIPPED] 块**（否则大库文件
                    # 94% 是注释、审核超线性变慢）；计数落 stats，末尾写一行汇总。
                else:
                    abnormal_skipped += 1
                    logger.warning("跳过不可读对象 %s.%s(%s): %s",
                                   target_db, obj_name, kind, e)
                    # 异常跳过保留逐个 [SKIPPED] 块（需溯源的是异常，数量有阈值告警兜底）
                    lines.append("-- ============================================================")
                    lines.append(f"-- [SKIPPED] SQL Object: CREATE {kind}")
                    lines.append(f"-- Object Name: {sanitize_comment(obj_name)}")
                    lines.append(f"-- Skip Reason: {sanitize_comment(str(e))}")
                    lines.append("-- ============================================================")
                    lines.append("")
                skipped_objects.append({"name": obj_name, "type": kind,
                                        "category": category, "reason": str(e)})
                continue
            lines.append(f"-- SQL Object: CREATE {kind}")
            lines.append(f"-- {'View' if kind == 'VIEW' else 'Table'}: {sanitize_comment(obj_name)}")
            lines.append(ddl.rstrip(";") + ";")
            lines.append("")
            extracted += 1
    # SIT-A/R3-B-01：良性跳过在文件末尾写**一行汇总**（明细见 skipped_list 前 50 条）
    if benign_skipped:
        lines.append(f"-- [SKIPPED-SUMMARY] TDSQL 物理分片子表 {benign_skipped} 张已跳过"
                     f"（父表 DDL 已纳管），明细见任务跳过清单")
        lines.append("")
    # 仅当所有选定对象全部失败才判不可审核；部分跳过不阻断（跳过清单已存证）
    if extracted == 0:
        raise MetadataExtractError(
            "NO_AUDITABLE_OBJECTS",
            f"库 {target_db} 选中的 {selected} 个对象全部提取失败，无可审核内容。")
    # M-03：异常跳过（权限/超时/Proxy 故障等非良性）超阈值时打醒目告警
    if abnormal_skipped and (abnormal_skipped > 50
                             or abnormal_skipped > selected * 0.05):
        logger.warning("库 %s 有 %d 个对象提取失败（非良性，占选中 %d 的 %.1f%%），"
                       "请人工核实是否权限/网络问题导致漏审",
                       target_db, abnormal_skipped, selected,
                       abnormal_skipped / selected * 100)
    if skipped_objects:
        logger.warning("库 %s 提取完成：成功 %d，跳过 %d（良性子表 %d / 异常 %d）",
                       target_db, extracted, len(skipped_objects),
                       benign_skipped, abnormal_skipped)
    stats = {"enumerated_objects": enumerated, "selected_objects": selected,
             "extracted_objects": extracted,
             "skipped_objects": len(skipped_objects),
             "skipped_benign": benign_skipped,
             "skipped_abnormal": abnormal_skipped,
             # M-02：skipped_list 在返回处截断（计数全留 + 样例 50 条），
             # 让下游所有写点（进度/manifest）天然不超量
             "skipped_list": skipped_objects[:50]}
    return lines, stats


def audit_streaming(sql_text: str, *, file_path: str = "",
                    rule_overrides: Optional[dict] = None,
                    instance_type: Optional[str] = None) -> Iterator:
    """流式审核（复用 DU-1 的 checker.iter_audit_file）。

    逐条产出 AuditResult；调用方可边审边写 results.ndjson，不全批驻留内存。
    """
    checker = RuleChecker()
    yield from checker.iter_audit_file(
        sql_text, file_path=file_path, rule_overrides=rule_overrides,
        instance_type=instance_type)
