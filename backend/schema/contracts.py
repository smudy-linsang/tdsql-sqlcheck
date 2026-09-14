# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：CREATE TABLE 完整结构合同校验原语（CP-W02，DETAIL §10.4）。

背景（A 第三轮复核 §1.1 实证）：现有 migrator 对 CREATE TABLE 只核验表存在，
不完整核验表内列与复合索引——Copilot 的 11 张表全是 CREATE TABLE，写错一个列
类型会被静默接受。本模块提供无业务副作用的严格校验工具：

  · parse_create_table(sql)  —— 把单条 CREATE TABLE DDL 解析为 TableContract；
  · verify_contract(cursor, contract)  —— 对 information_schema 做完整校验：
    表存在 / 引擎 / 排序规则、列全集 / 类型及长度精度 / 可空 / 显式或未声明默认、
    主键 / 唯一性 / 索引名 / 列序 / 前缀长度；
  · verify_statements_contracts(cursor, statements)  —— 对一组 DDL 中全部
    CREATE TABLE 逐一校验，返回违反项清单（空=通过）。

A 组新 key 在写台账前和已登记启动路径均调用本模块；B 组复用相同校验原语。
本模块只读 information_schema，不执行任何写操作。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ══════════════════════════════════════════════════════════════════
# DDL 解析（保守子集：本项目迁移文件使用的 CREATE TABLE 形态）
# ══════════════════════════════════════════════════════════════════

_CREATE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?\s*\(",
    re.IGNORECASE,
)
_COLUMN_RE = re.compile(
    r"^\s*`?(\w+)`?\s+([A-Za-z]+(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?(?:\s+UNSIGNED)?)"
    r"(.*)$",
    re.IGNORECASE | re.DOTALL,
)
_PRIMARY_RE = re.compile(r"^\s*PRIMARY\s+KEY\s*\((.+)\)\s*$", re.IGNORECASE | re.DOTALL)
_UNIQUE_RE = re.compile(
    r"^\s*UNIQUE\s+(?:KEY|INDEX)\s+`?(\w+)`?\s*\((.+)\)\s*$", re.IGNORECASE | re.DOTALL)
_INDEX_RE = re.compile(
    r"^\s*(?:INDEX|KEY)\s+`?(\w+)`?\s*\((.+)\)\s*$", re.IGNORECASE | re.DOTALL)
_INDEX_COL_RE = re.compile(
    r"`?(\w+)`?(?:\s*\(\s*(\d+)\s*\))?(?:\s+(ASC|DESC))?", re.IGNORECASE)
_DEFAULT_RE = re.compile(
    r"\bDEFAULT\s+('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|[^\s,]+)", re.IGNORECASE)
_ENGINE_RE = re.compile(r"\bENGINE\s*=\s*(\w+)", re.IGNORECASE)
_COLLATE_RE = re.compile(r"\bCOLLATE\s*=?\s*([A-Za-z0-9_]+)", re.IGNORECASE)
_CHARSET_RE = re.compile(r"\b(?:DEFAULT\s+)?CHARSET\s*=?\s*([A-Za-z0-9_]+)", re.IGNORECASE)


@dataclass
class ColumnContract:
    name: str
    col_type: str            # 规范化小写无空格，如 varchar(128)/int/datetime(6)
    not_null: bool
    has_default: bool        # DDL 是否显式声明 DEFAULT
    default: Optional[str]   # 归一前的文本（None=未声明或 DEFAULT NULL）
    charset: Optional[str] = None   # CHARACTER SET 子句（如 ascii）
    collate: Optional[str] = None


@dataclass
class IndexContract:
    name: str                # PRIMARY 或索引名
    unique: bool
    columns: list[tuple[str, Optional[int]]]  # (列名, 前缀长度)


@dataclass
class TableContract:
    table: str
    columns: list[ColumnContract] = field(default_factory=list)
    indexes: list[IndexContract] = field(default_factory=list)
    engine: str = "innodb"
    table_collate: Optional[str] = None
    table_charset: Optional[str] = None


def _split_top_level(body: str) -> list[str]:
    """按顶层逗号切分 CREATE TABLE 括号体（跳过括号/引号/反引号嵌套）。"""
    parts, cur = [], []
    depth = 0
    in_s = in_d = in_b = False
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c == "\\" and (in_s or in_d):
            cur.append(c)
            if i + 1 < n:
                cur.append(body[i + 1])
            i += 2
            continue
        if in_s:
            cur.append(c); in_s = c != "'"; i += 1; continue
        if in_d:
            cur.append(c); in_d = c != '"'; i += 1; continue
        if in_b:
            cur.append(c); in_b = c != "`"; i += 1; continue
        if c == "'":
            in_s = True
        elif c == '"':
            in_d = True
        elif c == "`":
            in_b = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def _normalize_type(raw: str) -> str:
    return re.sub(r"\s+", "", raw).lower()


def _parse_index_columns(raw: str) -> list[tuple[str, Optional[int]]]:
    cols: list[tuple[str, Optional[int]]] = []
    for m in _INDEX_COL_RE.finditer(raw):
        prefix = int(m.group(2)) if m.group(2) else None
        cols.append((m.group(1), prefix))
    return cols


def parse_create_table(sql: str) -> Optional[TableContract]:
    """解析单条 CREATE TABLE 语句为 TableContract；非 CREATE 返回 None。

    保守解析：无法识别的行不静默忽略——抛出 ValueError 迫使合同显式化，
    避免“解析漏掉一行索引”造成验收缺口（失败关闭方向）。
    """
    m = _CREATE_RE.match(sql)
    if not m:
        return None
    table = m.group(1)
    # 括号体
    open_idx = sql.index("(", m.end() - 1)
    depth, i = 0, open_idx
    close_idx = -1
    in_s = in_d = in_b = False
    while i < len(sql):
        c = sql[i]
        if c == "\\" and (in_s or in_d):
            i += 2
            continue
        if in_s:
            in_s = c != "'"
        elif in_d:
            in_d = c != '"'
        elif in_b:
            in_b = c != "`"
        elif c == "'":
            in_s = True
        elif c == '"':
            in_d = True
        elif c == "`":
            in_b = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                close_idx = i
                break
        i += 1
    if close_idx < 0:
        raise ValueError(f"CREATE TABLE 括号未闭合: {table}")
    body = sql[open_idx + 1:close_idx]
    tail = sql[close_idx + 1:]

    contract = TableContract(table=table)
    em = _ENGINE_RE.search(tail)
    if em:
        contract.engine = em.group(1).lower()
    cm = _COLLATE_RE.search(tail)
    if cm:
        contract.table_collate = cm.group(1).lower()
    chm = _CHARSET_RE.search(tail)
    if chm:
        contract.table_charset = chm.group(1).lower()

    for part in _split_top_level(body):
        pm = _PRIMARY_RE.match(part)
        if pm:
            contract.indexes.append(IndexContract(
                name="PRIMARY", unique=True,
                columns=_parse_index_columns(pm.group(1))))
            continue
        um = _UNIQUE_RE.match(part)
        if um:
            contract.indexes.append(IndexContract(
                name=um.group(1), unique=True,
                columns=_parse_index_columns(um.group(2))))
            continue
        im = _INDEX_RE.match(part)
        if im:
            contract.indexes.append(IndexContract(
                name=im.group(1), unique=False,
                columns=_parse_index_columns(im.group(2))))
            continue
        cm2 = _COLUMN_RE.match(part)
        if cm2:
            name, col_type, rest = cm2.group(1), cm2.group(2), cm2.group(3) or ""
            rest_up = f" {rest.upper()} "
            not_null = " NOT NULL" in rest_up
            dm = _DEFAULT_RE.search(rest)
            has_default = dm is not None
            default_val: Optional[str] = None
            if has_default:
                dv = dm.group(1)
                if dv[:1] in ("'", '"'):
                    default_val = dv[1:-1]
                else:
                    default_val = dv.upper()
                if default_val.upper() == "NULL":
                    default_val = None
            cs_m = re.search(r"CHARACTER\s+SET\s+(\w+)", rest, re.IGNORECASE)
            co_m = re.search(r"COLLATE\s+(\w+)", rest, re.IGNORECASE)
            contract.columns.append(ColumnContract(
                name=name,
                col_type=_normalize_type(col_type),
                not_null=not_null,
                has_default=has_default,
                default=default_val,
                charset=cs_m.group(1).lower() if cs_m else None,
                collate=co_m.group(1).lower() if co_m else None,
            ))
            continue
        # 注释行/空行跳过；其余无法识别的定义行抛错（失败关闭）
        if part.strip():
            raise ValueError(f"无法解析的表定义行 [{table}]: {part[:80]}")
    return contract


# ══════════════════════════════════════════════════════════════════
# 结构验收（只读 information_schema）
# ══════════════════════════════════════════════════════════════════

def _normalize_default_value(value):
    """与 migrator._normalize_default_value 同语义的关键字归一。"""
    if value is None:
        return None
    v = str(value).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    vu = v.upper().rstrip("()")
    if vu in ("CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP()", "NOW"):
        return "CURRENT_TIMESTAMP"
    if vu in ("TRUE", "1"):
        return "TRUE"
    if vu in ("FALSE", "0"):
        return "FALSE"
    if vu == "NULL":
        return None
    return v


def verify_contract(cursor, contract: TableContract) -> list[str]:
    """完整结构验收：返回违反项清单（空=通过）。只读 information_schema。"""
    problems: list[str] = []
    t = contract.table

    cursor.execute(
        "SELECT ENGINE, TABLE_COLLATION FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (t,))
    row = cursor.fetchone()
    if not row:
        return [f"表 {t} 不存在"]
    row = dict(row)
    engine = (row.get("ENGINE") or row.get("engine") or "").lower()
    if engine != contract.engine:
        problems.append(f"表 {t} 引擎不符: 期望 {contract.engine} 实际 {engine}")
    if contract.table_collate:
        coll = (row.get("TABLE_COLLATION") or row.get("table_collation") or "").lower()
        if coll != contract.table_collate:
            problems.append(
                f"表 {t} 排序规则不符: 期望 {contract.table_collate} 实际 {coll}")

    # 列全集 + 类型/可空/默认/字符集
    cursor.execute(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
        "CHARACTER_SET_NAME, COLLATION_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
        (t,))
    actual_cols: dict[str, dict] = {}
    for r in cursor.fetchall():
        r = {k.lower(): v for k, v in dict(r).items()}
        actual_cols[str(r["column_name"])] = r

    expected_names = [c.name for c in contract.columns]
    actual_names = list(actual_cols.keys())
    missing = [c for c in expected_names if c not in actual_cols]
    extra = [c for c in actual_names if c not in expected_names]
    if missing:
        problems.append(f"表 {t} 缺列: {','.join(missing)}")
    if extra:
        problems.append(f"表 {t} 多列: {','.join(extra)}")
    if expected_names != [c for c in actual_names if c in set(expected_names)] and not missing:
        problems.append(f"表 {t} 列顺序与合同不符")

    for cc in contract.columns:
        info = actual_cols.get(cc.name)
        if info is None:
            continue
        actual_type = str(info.get("column_type") or "").replace(" ", "").lower()
        if actual_type != cc.col_type:
            problems.append(
                f"列 {t}.{cc.name} 类型不符: 期望 {cc.col_type} 实际 {actual_type}")
        actual_not_null = str(info.get("is_nullable") or "").upper() == "NO"
        if actual_not_null != cc.not_null:
            problems.append(f"列 {t}.{cc.name} 可空性不符: 期望 "
                            f"{'NOT NULL' if cc.not_null else 'NULL'}")
        actual_default = _normalize_default_value(info.get("column_default"))
        if not cc.has_default or cc.default is None:
            if actual_default is not None:
                problems.append(
                    f"列 {t}.{cc.name} 默认值不符: 期望 NULL/无声明 实际 {info.get('column_default')!r}")
        else:
            exp = _normalize_default_value(cc.default)
            if actual_default != exp:
                problems.append(
                    f"列 {t}.{cc.name} 默认值不符: 期望 {exp!r} 实际 {info.get('column_default')!r}")
        if cc.charset:
            actual_cs = (info.get("character_set_name") or "")
            actual_cs = str(actual_cs).lower() if actual_cs else ""
            if actual_cs != cc.charset:
                problems.append(
                    f"列 {t}.{cc.name} 字符集不符: 期望 {cc.charset} 实际 {actual_cs or '(继承表)'}")

    # 主键 / 唯一性 / 索引名 / 列序 / 前缀长度
    cursor.execute(
        "SELECT INDEX_NAME, NON_UNIQUE, COLUMN_NAME, SUB_PART, SEQ_IN_INDEX "
        "FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
        "ORDER BY INDEX_NAME, SEQ_IN_INDEX", (t,))
    actual_idx: dict[str, dict] = {}
    for r in cursor.fetchall():
        r = {k.lower(): v for k, v in dict(r).items()}
        name = str(r["index_name"])
        entry = actual_idx.setdefault(name, {
            "unique": str(r.get("non_unique")) in ("0", "0.0") or r.get("non_unique") == 0,
            "columns": []})
        entry["columns"].append(
            (str(r["column_name"]),
             int(r["sub_part"]) if r.get("sub_part") is not None else None))

    expected_idx = {ix.name: ix for ix in contract.indexes}
    for name, ix in expected_idx.items():
        actual = actual_idx.get(name)
        if actual is None:
            problems.append(f"表 {t} 缺索引: {name}")
            continue
        if actual["unique"] != ix.unique:
            problems.append(f"索引 {t}.{name} 唯一性不符")
        if actual["columns"] != ix.columns:
            problems.append(
                f"索引 {t}.{name} 列序/前缀不符: 期望 {ix.columns} 实际 {actual['columns']}")
    for name in actual_idx:
        if name not in expected_idx:
            problems.append(f"表 {t} 多索引: {name}")
    return problems


def verify_statements_contracts(cursor, statements: list[str],
                                label: str = "") -> list[str]:
    """对一组 DDL 中全部 CREATE TABLE 逐一完整验收；返回违反项清单。"""
    problems: list[str] = []
    for stmt in statements:
        contract = parse_create_table(stmt)
        if contract is None:
            continue
        for p in verify_contract(cursor, contract):
            problems.append(f"[{label or contract.table}] {p}")
    return problems
