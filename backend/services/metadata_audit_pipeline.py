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

# v1.6.3.6 / BUG-01：TDSQL 分布式/二级分区的底层物理分片子表模式。
# information_schema.TABLES 会枚举出这些物理子表（如 xxx_tdsql_subp190001），
# 但 TDSQL Proxy 不允许对单个物理分片 SHOW CREATE（报 660 Proxy ERROR ... does not exist）。
# 主表 DDL 已包含全部分区/分片定义，子表无需也不能单独提取 → 前置过滤。
_TDSQL_INTERNAL_PARTITION_PATTERN = re.compile(
    r".*_tdsql_(subp|shard)\d*$", re.IGNORECASE)


def is_tdsql_internal_table(table_name: str) -> bool:
    """判定是否为 TDSQL 底层物理分片子表（主表 DDL 已纳管，不可单独 SHOW CREATE）。"""
    return bool(_TDSQL_INTERNAL_PARTITION_PATTERN.match(table_name or ""))


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
                     instance_label: str = "") -> tuple:
    """只读提取目标库元数据为 SQL 行列表。

    Args:
        pool: TDSQLConnectionPool（只读连接）
        target_db: 最终实际库名
        scopes: TABLE/INDEX/VIEW/SHARDKEY 子集
        instance_label: 冻结的实例连接名称（写入 SQL 头部注释，需去 CRLF）

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
        selected_objs = _select_objects(objects, scopes)
        selected = len(selected_objs)
        if selected == 0:
            raise MetadataExtractError(
                "NO_AUDITABLE_OBJECTS",
                f"库 {target_db} 在选择范围 {scopes} 内没有可审核对象"
                f"（枚举到 {enumerated} 个对象）。")
        extracted = 0
        skipped_objects = []
        for obj in selected_objs:
            kind = "VIEW" if "VIEW" in obj["type"] else "TABLE"
            obj_name = obj["name"]
            # 1. TDSQL 内部物理分片子表前置跳过（主表 DDL 已纳管，Proxy 不可读）
            if kind == "TABLE" and is_tdsql_internal_table(obj_name):
                skipped_objects.append({
                    "name": obj_name, "type": kind,
                    "reason": "TDSQL底层物理分片子表，已由父表统一纳管"})
                logger.debug("跳过 TDSQL 内部物理子表 %s.%s", target_db, obj_name)
                continue
            # 2. 单对象 SHOW CREATE 容错：失败仅跳过并留 [SKIPPED] 存证注释，不杀全库
            try:
                ddl = _show_create(conn, target_db, obj)
            except Exception as e:
                logger.warning("跳过不可读对象 %s.%s(%s): %s",
                               target_db, obj_name, kind, e)
                skipped_objects.append({"name": obj_name, "type": kind,
                                        "reason": str(e)})
                lines.append("-- ============================================================")
                lines.append(f"-- [SKIPPED] SQL Object: CREATE {kind}")
                lines.append(f"-- Object Name: {obj_name}")
                lines.append(f"-- Skip Reason: {sanitize_comment(str(e))}")
                lines.append("-- ============================================================")
                lines.append("")
                continue
            lines.append(f"-- SQL Object: CREATE {kind}")
            lines.append(f"-- {'View' if kind == 'VIEW' else 'Table'}: {obj_name}")
            lines.append(ddl.rstrip(";") + ";")
            lines.append("")
            extracted += 1
    # 仅当所有选定对象全部失败才判不可审核；部分跳过不阻断（跳过清单已存证）
    if extracted == 0:
        raise MetadataExtractError(
            "NO_AUDITABLE_OBJECTS",
            f"库 {target_db} 选中的 {selected} 个对象全部提取失败，无可审核内容。")
    if skipped_objects:
        logger.warning("库 %s 提取完成：成功 %d，跳过 %d（详见 skipped_objects）",
                       target_db, extracted, len(skipped_objects))
    stats = {"enumerated_objects": enumerated, "selected_objects": selected,
             "extracted_objects": extracted,
             "skipped_objects": len(skipped_objects),
             "skipped_list": skipped_objects}
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
