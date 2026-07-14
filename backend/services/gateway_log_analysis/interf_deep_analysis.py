#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TDSQL Gateway interf 日志深度分析脚本

功能:
  1. SQL 耗时细分统计（按耗时区间 × SQL 类型交叉统计），输出 HTML + CSV
  2. SQL 去重聚合（归一化 SQL 模式，统计执行次数/耗时/库名/autocommit），输出 CSV
  3. 数据库联动（EXPLAIN 执行计划、表结构、表数据量），输出 CSV
  4. EXPLAIN 安全逻辑：UPDATE/DELETE 自动转写为等价 SELECT，只允许执行 EXPLAIN SELECT
  5. 增强诊断：EXPLAIN 问题标记、索引详情、冗余索引、统计信息更新时间/是否过期、扫描效率
  6. SQL 完整性检测：自动识别 Proxy 截断的 SQL，跳过无效 EXPLAIN

用法:
  # 分析指定 interf 文件（不连数据库）
  python3 interf_deep_analysis.py --files interf_instance_15001.2026-04-01.0 \\
      --name 合约管理 --proxy-ip 10.206.0.21 --port 15002

  # 分析 + 连接数据库获取执行计划和表结构
  python3 interf_deep_analysis.py --files interf_instance_15001.2026-04-01.0 \\
      --name 合约管理 --proxy-ip 10.206.0.21 --port 15002 \\
      --db-host 10.206.0.15 --db-port 15002 --db-user test --db-pass 'Csig12345.....'

  # 从配置文件读取（使用 [gateway_proxies] 配置）
  python3 interf_deep_analysis.py --files interf_instance_15001.2026-04-01.0 --config-index 1

作者: lynx
版本: v1.6
"""

import argparse
import csv
import html as html_mod
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

VERSION = "1.6"

# 统计信息过期阈值（天）
STATS_EXPIRE_DAYS = 15

# ============================================================
# 常量
# ============================================================

# 更细的耗时区间
TIMECOST_BINS = [
    (0, 0.5, "<0.5ms"),
    (0.5, 1, "0.5-1ms"),
    (1, 2, "1-2ms"),
    (2, 5, "2-5ms"),
    (5, 10, "5-10ms"),
    (10, 20, "10-20ms"),
    (20, 50, "20-50ms"),
    (50, 100, "50-100ms"),
    (100, 200, "100-200ms"),
    (200, 500, "200-500ms"),
    (500, 1000, "500ms-1s"),
    (1000, 3000, "1-3s"),
    (3000, 10000, "3-10s"),
    (10000, float("inf"), ">10s"),
]

# SQL 类型关键字映射
SQL_TYPE_KEYWORDS = {
    "SELECT": "SELECT",
    "INSERT": "INSERT",
    "UPDATE": "UPDATE",
    "DELETE": "DELETE",
    "REPLACE": "REPLACE",
    "ALTER": "ALTER",
    "CREATE": "CREATE",
    "DROP": "DROP",
    "TRUNCATE": "TRUNCATE",
    "SET": "SET",
    "SHOW": "SHOW",
    "BEGIN": "BEGIN",
    "COMMIT": "COMMIT",
    "ROLLBACK": "ROLLBACK",
}

# interf 日志中的 sql_type 字段映射
INTERF_SQL_TYPE_MAP = {
    "5": "INSERT", "6": "UPDATE", "7": "DELETE", "8": "SELECT",
    "4": "CREATE", "9": "SHOW", "10": "SET",
    "14": "PREPARE", "22": "BEGIN", "23": "COMMIT",
}

# MySQL 系统库名集合（小写，用于过滤非业务 SQL）
SYSTEM_DATABASES = {
    "mysql", "sys", "information_schema", "performance_schema",
    "sysdb", "__tencentdb__", "test", "query_rewrite",
}

# 非业务 SQL 过滤列表（归一化后匹配，忽略大小写）
NON_BUSINESS_SQL_PATTERNS = {
    "SET autocommit=?",
    "SET NAMES utf8",
    "SET NAMES utf8mb4",
    "set session transaction read write",
    "SET SESSION TRANSACTION READ WRITE",
    "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
    "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ",
    "SELECT @@session.transaction_read_only",
    "SELECT @@session.tx_read_only",
    "SELECT @@tx_isolation",
    "SELECT @@session.tx_isolation",
    "SELECT @@session.transaction_isolation",
    "SELECT ?",
    "COMMIT",
    "ROLLBACK",
    "BEGIN",
}

def is_non_business_sql(normalized_sql, sql_type, db=""):
    """判断是否为非业务 SQL
    
    过滤规则:
      1. 空 SQL
      2. 精确匹配非业务 SQL 模式列表
      3. SET/COMMIT/ROLLBACK/BEGIN/SHOW 等非业务语句
      4. 系统库 SQL（db 为系统库名，或 SQL 中显式引用系统库表）
      5. SELECT @@xxx 系统变量查询
      6. EXPLAIN 语句（用户手动执行的 EXPLAIN，非业务 SQL）
      7. SQL 注释行（以 -- 开头的纯注释）
    """
    if not normalized_sql:
        return True
    s = normalized_sql.strip()
    # 精确匹配过滤列表
    if s in NON_BUSINESS_SQL_PATTERNS or s.upper() in {p.upper() for p in NON_BUSINESS_SQL_PATTERNS}:
        return True
    # SET 类语句全部过滤
    if sql_type == "SET":
        return True
    # COMMIT/ROLLBACK/BEGIN 过滤
    if sql_type in ("COMMIT", "ROLLBACK", "BEGIN"):
        return True
    # SHOW 语句过滤
    if sql_type == "SHOW":
        return True
    # EXPLAIN 语句过滤（用户手动执行的 EXPLAIN，不是业务 SQL）
    if s.upper().startswith("EXPLAIN "):
        return True
    # SQL 注释行过滤（以 -- 开头的纯注释，如 "-- ======" 分隔线）
    if s.startswith("--"):
        return True
    # USE database（纯库名，无空格无关键字）
    if " " not in s and not s.startswith("SELECT") and not s.startswith("INSERT"):
        return True
    # SELECT @@xxx 系统变量查询
    if s.upper().startswith("SELECT @@"):
        return True
    # ── 系统库过滤 ──
    # 1. db 字段直接是系统库
    if db and db.lower() in SYSTEM_DATABASES:
        return True
    # 2. SQL 中显式引用系统库表（如 information_schema.COLUMNS、mysql.innodb_table_stats）
    s_upper = s.upper()
    for sysdb in SYSTEM_DATABASES:
        if f"{sysdb.upper()}." in s_upper:
            return True
    return False


def _h(text):
    """HTML 转义"""
    return html_mod.escape(str(text))


# ============================================================
# interf 日志解析
# ============================================================

def parse_interf_kv(line):
    """解析 interf_instance 的 key=value 对"""
    m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \d+\]\s+\w+\s+(.*)", line)
    if not m:
        return None
    timestamp_str = m.group(1)
    body = m.group(2)
    fields = {"_timestamp": timestamp_str}
    for part in body.split("&"):
        if "=" in part:
            k, _, v = part.partition("=")
            fields[k] = v
    return fields


def detect_sql_type(sql_text, interf_sql_type=None):
    """从 SQL 文本或 interf 的 sql_type 字段检测 SQL 类型"""
    # 优先从 interf 的 sql_type 字段
    if interf_sql_type and interf_sql_type in INTERF_SQL_TYPE_MAP:
        return INTERF_SQL_TYPE_MAP[interf_sql_type]
    if not sql_text:
        return "OTHER"
    # 去掉 TDSQL 透传注释（如 /*sets:allsets*/）和前导空白
    cleaned = re.sub(r"/\*.*?\*/", "", sql_text).strip()
    # URL 解码
    cleaned = cleaned.replace("%20", " ").replace("%0A", " ")
    first_word = cleaned.split()[0].upper() if cleaned.split() else ""
    for kw, typ in SQL_TYPE_KEYWORDS.items():
        if first_word.startswith(kw):
            return typ
    return "OTHER"


def _is_sql_truncated(sql_text):
    """检测 SQL 是否被 Proxy 截断（interf 日志中 SQL 字段有长度限制）
    
    截断特征:
      1. 括号不匹配（左括号 > 右括号）
      2. 单引号不匹配（奇数个单引号，说明字符串值被截断）
      3. SQL 以逗号、字段名片段、不完整的关键字结尾（如 "spare_fld_0" "glbl_affr_" 等）
      4. SELECT 语句没有 FROM（SELECT 列表太长被截断）
      5. INSERT 语句 VALUES 括号不完整
    
    Returns:
        True 表示 SQL 被截断，False 表示完整
    """
    if not sql_text:
        return False
    
    # URL 解码后检测
    s = sql_text.replace("%20", " ").replace("%0A", " ").replace("%3D", "=")
    s = s.replace("%2C", ",").replace("%27", "'").replace("%28", "(").replace("%29", ")")
    s = re.sub(r"/\*.*?\*/", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    
    if not s:
        return False
    
    # 检测1: 括号不匹配
    open_parens = s.count('(')
    close_parens = s.count(')')
    if open_parens > close_parens:
        return True
    
    # 检测2: 单引号不匹配（奇数个 = 字符串被截断）
    # 先去掉转义的单引号 ''
    s_no_escape = s.replace("''", "")
    if s_no_escape.count("'") % 2 != 0:
        return True
    
    # 检测3: SQL 以逗号或不完整标识符结尾（字段列表被截断）
    s_stripped = s.rstrip()
    if s_stripped.endswith(","):
        return True
    # 以下划线结尾（字段名被截断，如 "spare_fld_"）
    if s_stripped.endswith("_"):
        return True
    
    # 检测4: SELECT 语句没有 FROM（列表太长被截断）
    s_upper = s.upper()
    if s_upper.startswith("SELECT") and "FROM" not in s_upper:
        # 排除 SELECT @@xxx 和 SELECT 1 等简单查询
        if not re.match(r"SELECT\s+(@|[0-9?'])", s, re.IGNORECASE):
            return True
    
    # 检测5: INSERT VALUES 不完整
    if "VALUES" in s_upper:
        after_values = s[s_upper.rfind("VALUES"):]
        if after_values.count('(') > after_values.count(')'):
            return True
    
    return False


def normalize_sql(sql_text):
    """将 SQL 中的具体值替换为占位符，提取 SQL 模式
    
    注意: 不替换字段名/表名中的数字（如 spare_fld_01_32, spare_data_750）
    只替换独立的数字值（如 WHERE id=123, LIMIT 10, VALUES(1,2,3)）
    """
    if not sql_text:
        return ""
    s = sql_text[:800]
    # URL 解码
    s = s.replace("%3D", "=").replace("%0A", " ").replace("%20", " ").replace("%2C", ",")
    s = s.replace("%27", "'").replace("%28", "(").replace("%29", ")")
    # 去掉 SQL 注释块（如 /* SQL-01: ... */、TDSQL 透传注释 /*sets:allsets*/ 等）
    s = re.sub(r"/\*.*?\*/", "", s)
    # 替换 IN 列表
    s = re.sub(r"IN\s*\([^)]+\)", "IN (?)", s, flags=re.IGNORECASE)
    # 替换字符串值（单引号包裹的内容）
    s = re.sub(r"'[^']*'", "'?'", s)
    # 替换数字值 — 关键改进:
    #   - 不替换标识符中的数字（字母/下划线后面紧跟的数字，如 fld_01, data_750）
    #   - 只替换独立的数字（不紧跟在字母/下划线/数字后面，也不紧跟字母/下划线）
    #   - 使用负向回顾 + 负向前瞻确保数字不是标识符的一部分
    s = re.sub(r"(?<![a-zA-Z0-9_])\d+(?:\.\d+)?(?![a-zA-Z_])", "?", s)
    # 压缩空白
    s = re.sub(r"\s+", " ", s).strip()
    return s[:500]


def extract_tables_from_sql(sql_text):
    """从 SQL 文本中提取涉及的表名
    
    处理场景:
      - 简单 SELECT/INSERT/UPDATE/DELETE
      - 多表 JOIN（LEFT/RIGHT/INNER/CROSS JOIN）
      - 逗号 JOIN（FROM t1 a, t2 b WHERE ...）
      - 子查询（SELECT ... FROM (subquery) alias）— 递归提取子查询内的表
      - UNION / UNION ALL — 分段提取各 SELECT 的表
      - 表别名 — 只取实际表名，不取别名
    """
    if not sql_text:
        return []
    s = sql_text.replace("%20", " ").replace("%0A", " ").replace("%3D", "=")
    s = s.replace("%2C", ",").replace("%27", "'").replace("%28", "(").replace("%29", ")")
    # 去掉 TDSQL 透传注释
    s = re.sub(r"/\*.*?\*/", "", s)
    s = re.sub(r"\s+", " ", s).strip()

    tables = _extract_tables_recursive(s)
    return _filter_table_names(tables)


def _extract_tables_recursive(sql_clean):
    """递归提取 SQL 中的表名（处理子查询和 UNION）"""
    tables = set()
    s = sql_clean.strip()
    s_upper = s.upper()

    # ── 先处理 UNION / UNION ALL：拆分后分别提取 ──
    # 用简单的括号平衡方式拆分顶层 UNION
    union_parts = _split_union(s)
    if len(union_parts) > 1:
        for part in union_parts:
            tables.update(_extract_tables_from_single_select(part.strip()))
        return tables

    return _extract_tables_from_single_select(s)


def _split_union(sql_text):
    """在顶层（不在括号内）按 UNION 拆分 SQL"""
    parts = []
    depth = 0
    current = []
    tokens = sql_text.split()
    i = 0
    while i < len(tokens):
        token_upper = tokens[i].upper()
        # 统计括号深度
        depth += tokens[i].count('(') - tokens[i].count(')')
        if depth == 0 and token_upper == "UNION":
            # 跳过可能的 ALL
            parts.append(" ".join(current))
            current = []
            if i + 1 < len(tokens) and tokens[i + 1].upper() == "ALL":
                i += 1
        else:
            current.append(tokens[i])
        i += 1
    if current:
        parts.append(" ".join(current))
    return parts


def _extract_tables_from_single_select(sql_clean):
    """从单个 SQL 语句中提取表名（不含 UNION 拆分）"""
    tables = set()
    s = sql_clean.strip()
    s_upper = s.upper()

    # ── 去掉外层 SELECT count(...) FROM (...) total 的包装 ──
    # 检测: select count(xxx) from ( ... ) alias
    m_wrapper = re.match(
        r"SELECT\s+(?:COUNT|SUM|AVG|MIN|MAX)\s*\(.*?\)\s+FROM\s*\(\s*(.+)\s*\)\s*\w*\s*$",
        s, re.IGNORECASE | re.DOTALL
    )
    if m_wrapper:
        inner_sql = m_wrapper.group(1).strip()
        return _extract_tables_recursive(inner_sql)

    # ── INSERT / REPLACE ──
    if s_upper.startswith("INSERT") or s_upper.startswith("REPLACE"):
        m = re.match(r"(?:INSERT|REPLACE)\s+(?:INTO\s+)?`?(\w+)`?", s, re.IGNORECASE)
        if m:
            tables.add(m.group(1))
        # INSERT ... SELECT FROM 子查询
        select_pos = s_upper.find("SELECT")
        if select_pos > 0:
            after_select = s[select_pos:]
            tables.update(_extract_from_and_join(after_select))
        return tables

    # ── UPDATE（支持多表: UPDATE t1 alias, t2 alias SET）──
    if s_upper.startswith("UPDATE"):
        m = re.match(r"UPDATE\s+(.+?)\s+SET\b", s, re.IGNORECASE)
        if m:
            update_clause = m.group(1)
            for part in update_clause.split(","):
                tm = re.match(r"\s*`?(\w+)`?", part.strip())
                if tm:
                    tables.add(tm.group(1))
        tables.update(_extract_from_and_join(s))
        return tables

    # ── DELETE ──
    if s_upper.startswith("DELETE"):
        tables.update(_extract_from_and_join(s))
        return tables

    # ── SELECT 和其他 ──
    tables.update(_extract_from_and_join(s))
    return tables


def _extract_from_and_join(sql_text):
    """从 SQL 的 FROM 和 JOIN 子句中提取表名，正确处理子查询和括号"""
    tables = set()
    s = sql_text

    # 提取 FROM 后面的表名（跳过子查询括号）
    for m in re.finditer(r"\bFROM\s+", s, re.IGNORECASE):
        pos = m.end()
        tables.update(_parse_table_list_at(s, pos))

    # 提取 JOIN 后面的表名
    for m in re.finditer(r"\bJOIN\s+", s, re.IGNORECASE):
        pos = m.end()
        # JOIN 后面只有一个表
        sub = s[pos:].lstrip()
        if sub.startswith("("):
            # JOIN (subquery) — 递归提取子查询
            inner = _extract_parenthesized(sub)
            if inner:
                tables.update(_extract_tables_recursive(inner))
        else:
            tm = re.match(r"`?(\w+)`?", sub)
            if tm:
                tables.add(tm.group(1))

    return tables


def _parse_table_list_at(sql_text, start_pos):
    """从 SQL 的指定位置开始解析表列表（FROM 后面的部分）
    
    正确处理:
      - FROM table1 alias1, table2 alias2
      - FROM (subquery) alias — 递归提取子查询内的表
      - FROM table1 — 简单单表
    """
    tables = set()
    remaining = sql_text[start_pos:].lstrip()

    if remaining.startswith("("):
        # FROM (subquery) alias — 子查询
        inner = _extract_parenthesized(remaining)
        if inner:
            tables.update(_extract_tables_recursive(inner))
        return tables

    # FROM 后面是表列表，用逗号分隔，但要注意不能跨越 WHERE/GROUP/ORDER 等
    # 先截取到终止关键字
    end_match = re.search(
        r"\b(?:WHERE|GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING|ON\s+\w|"
        r"LEFT\s+(?:OUTER\s+)?JOIN|RIGHT\s+(?:OUTER\s+)?JOIN|INNER\s+JOIN|"
        r"CROSS\s+JOIN|JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|UNION)\b",
        remaining, re.IGNORECASE
    )
    if end_match:
        table_clause = remaining[:end_match.start()]
    else:
        table_clause = remaining

    # 按逗号分割表列表
    for part in table_clause.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("("):
            # 逗号分隔中出现子查询
            inner = _extract_parenthesized(part)
            if inner:
                tables.update(_extract_tables_recursive(inner))
        else:
            # 取第一个 word 作为表名（跳过别名）
            tm = re.match(r"`?(\w+)`?", part)
            if tm:
                tables.add(tm.group(1))

    return tables


def _extract_parenthesized(text):
    """提取从文本开头的括号匹配内容（处理嵌套括号）
    
    输入: "(select ... from ...) total WHERE ..."
    输出: "select ... from ..."
    """
    if not text or text[0] != '(':
        return None
    depth = 0
    for i, ch in enumerate(text):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return text[1:i]
    # 未找到匹配的右括号，返回全部（可能是被截断的 SQL）
    return text[1:]


def _filter_table_names(tables):
    """过滤掉 SQL 关键字、系统库和常见别名，返回排序后的表名列表"""
    sql_keywords = {
        "SET", "VALUES", "INTO", "FROM", "WHERE", "AND", "OR", "ON",
        "SELECT", "UPDATE", "INSERT", "DELETE", "REPLACE", "NULL",
        "LIMIT", "ORDER", "GROUP", "HAVING", "UNION", "ALL", "AS",
        "DUAL", "LEFT", "RIGHT", "INNER", "OUTER", "CROSS", "JOIN",
        "NOT", "IN", "EXISTS", "BETWEEN", "LIKE", "IS", "CASE",
        "WHEN", "THEN", "ELSE", "END", "DISTINCT", "COUNT", "SUM",
        "AVG", "MIN", "MAX", "IF", "IFNULL", "COALESCE", "CONCAT",
        "ROW_NUMBER", "OVER", "PARTITION", "BY", "ASC", "DESC",
        "FOR", "LOCK", "SHARE", "MODE", "NOWAIT", "SKIP", "LOCKED",
        "TRUE", "FALSE", "WITH", "RECURSIVE", "LATERAL",
        "INFORMATION_SCHEMA", "MYSQL", "PERFORMANCE_SCHEMA", "SYS",
        "SYSDB", "__TENCENTDB__",
    }
    result = []
    for t in tables:
        # 跳过 SQL 关键字
        if t.upper() in sql_keywords:
            continue
        # 跳过单字符（常见别名如 a, b, c, t, n, s, r 等）
        if len(t) <= 1:
            continue
        # 跳过纯数字
        if t.isdigit():
            continue
        # 跳过常见的表别名模式（1-2个字母的缩写，如 ca, ct, rn, rbi, rci）
        # 但保留以 k 开头的（客户系统表名常以 k 开头如 kapp_xxx）
        # 实际表名通常包含下划线或长度 >= 4
        if len(t) <= 3 and '_' not in t and not t.startswith('k'):
            continue
        result.append(t)
    return sorted(result)


# ============================================================
# interf 日志分析
# ============================================================

def _extract_timestamp_hm(line):
    """从 interf 日志行中快速提取 HH:MM 时间（不做完整解析，性能优先）"""
    # interf 格式: [2026-04-01 14:30:00 12345] ...
    # 固定位置: [0]=`[`, [1:11]=日期, [12:17]=HH:MM
    if len(line) > 17 and line[0] == '[':
        return line[12:17]
    return None


def _get_file_time_range(filepath, sample_lines=20):
    """快速获取 interf 文件的首尾时间范围（HH:MM 格式）
    
    通过读取文件头部和尾部各 sample_lines 行来确定时间范围，
    避免读取整个文件。尾部使用 seek 从文件末尾往回读取。
    
    Returns:
        (first_hm, last_hm) 或 None（无法确定时间范围）
    """
    first_hm = None
    last_hm = None
    
    try:
        # 读取头部，取第一个有效时间戳
        with open(filepath, "r", errors="replace") as f:
            for _ in range(sample_lines):
                line = f.readline()
                if not line:
                    break
                hm = _extract_timestamp_hm(line)
                if hm:
                    first_hm = hm
                    break
        
        # 读取尾部，取最后一个有效时间戳
        # 使用 seek 从文件末尾往回读，避免读取整个文件
        file_size = os.path.getsize(filepath)
        # 每行约 500-2000 字节，读取尾部 64KB 足够取到 sample_lines 行
        tail_size = min(file_size, 65536)
        with open(filepath, "rb") as f:
            f.seek(max(0, file_size - tail_size))
            tail_bytes = f.read()
        tail_lines = tail_bytes.decode("utf-8", errors="replace").split("\n")
        # 从后往前找最后一个有效时间戳
        for line in reversed(tail_lines):
            hm = _extract_timestamp_hm(line)
            if hm:
                last_hm = hm
                break
    except Exception:
        pass
    
    if first_hm and last_hm:
        return (first_hm, last_hm)
    return None


def analyze_interf_files(file_list, time_range=None):
    """分析 interf 文件列表，返回分析结果
    
    Args:
        file_list: interf 日志文件路径列表
        time_range: 时间过滤范围，元组 (start_time, end_time)，如 ("14:00", "16:00")
    
    性能优化（当指定 --time-range 时）:
      1. 文件级预筛选: 读取文件首尾时间戳，与目标时间段无交集的文件直接跳过
      2. 逐行提前终止: 当行时间已超过目标结束时间，停止读取当前文件
    """
    # 耗时区间 × SQL 类型 交叉统计
    bin_type_counts = defaultdict(lambda: Counter())  # {bin_label: Counter({sql_type: count})}
    # SQL 去重聚合
    sql_patterns = {}  # {(pattern, db): {count, tc_sum, tc_max, tc_min, sample_sql, autocommit, sql_type}}
    # 所有 SQL 类型集合
    all_sql_types = set()
    # 全局统计
    total_lines = 0
    total_sql_lines = 0
    date_range = set()
    skipped_files = 0

    for filepath in file_list:
        if not os.path.exists(filepath):
            print(f"  [警告] 文件不存在，跳过: {filepath}", file=sys.stderr)
            continue

        # ── 文件级预筛选（仅当指定 --time-range 时生效）──
        if time_range:
            file_tr = _get_file_time_range(filepath)
            if file_tr:
                file_start, file_end = file_tr
                # 无交集判断: 文件结束时间 < 目标开始时间 or 文件开始时间 >= 目标结束时间
                if file_end < time_range[0] or file_start >= time_range[1]:
                    file_size_mb = os.path.getsize(filepath) / 1024 / 1024
                    print(f"  [跳过] {os.path.basename(filepath)} "
                          f"(文件时间 {file_start}~{file_end}，不在 {time_range[0]}~{time_range[1]} 范围内，"
                          f"节省 {file_size_mb:.0f}MB IO)", file=sys.stderr)
                    skipped_files += 1
                    continue

        print(f"  [分析] {os.path.basename(filepath)} ...", file=sys.stderr)
        # 记录是否已进入过目标时间段（用于提前终止优化）
        entered_range = False

        with open(filepath, "r", errors="replace") as f:
            for line in f:
                total_lines += 1
                fields = parse_interf_kv(line)
                if not fields:
                    continue

                sql = fields.get("sql", "")
                tc_str = fields.get("timecost", "")
                db = fields.get("db", "")
                sql_type_field = fields.get("sql_type", "")
                autocommit = fields.get("autocommit", "")
                timestamp = fields.get("_timestamp", "")
                user = fields.get("user", "")

                if not sql or not tc_str:
                    continue

                # 时间范围过滤 + 提前终止优化
                if time_range and timestamp:
                    # timestamp 格式: "2026-04-01 14:30:00"
                    hm = timestamp[11:16]  # 提取 "HH:MM"
                    if hm < time_range[0]:
                        continue
                    if hm >= time_range[1]:
                        # 已经超过目标结束时间
                        if entered_range:
                            # 之前进入过目标范围，现在超出了，后续行时间只会更大，提前终止
                            print(f"  [优化] 时间 {hm} 已超过 {time_range[1]}，提前结束当前文件（已读 {total_lines:,} 行）", file=sys.stderr)
                            break
                        continue
                    # 进入目标时间段
                    entered_range = True

                try:
                    tc = float(tc_str)
                except ValueError:
                    continue

                total_sql_lines += 1
                if timestamp:
                    date_range.add(timestamp[:10])

                # 检测 SQL 类型
                sql_type = detect_sql_type(sql, sql_type_field)

                # 归一化 SQL 并判断是否为业务 SQL（含系统库过滤）
                pattern = normalize_sql(sql)
                is_biz = pattern and not is_non_business_sql(pattern, sql_type, db)

                # 耗时区间统计（只统计业务 SQL）
                if is_biz:
                    all_sql_types.add(sql_type)
                    for low, high, label in TIMECOST_BINS:
                        if low <= tc < high:
                            bin_type_counts[label][sql_type] += 1
                            break

                # SQL 归一化去重聚合（只统计业务 SQL）
                if is_biz:
                    key = (pattern, db)
                    if key not in sql_patterns:
                        sql_patterns[key] = {
                            "count": 0,
                            "tc_sum": 0.0,
                            "tc_max": 0.0,
                            "tc_min": float("inf"),
                            "sample_sql": sql,
                            "autocommit": autocommit,
                            "sql_type": sql_type,
                            "tables": extract_tables_from_sql(sql),
                            "is_truncated": _is_sql_truncated(sql),
                            "users": set(),
                        }
                    p = sql_patterns[key]
                    p["count"] += 1
                    p["tc_sum"] += tc
                    if tc > p["tc_max"]:
                        p["tc_max"] = tc
                    if tc < p["tc_min"]:
                        p["tc_min"] = tc
                    if user:
                        p["users"].add(user)

    if skipped_files > 0:
        print(f"\n  [统计] 文件级预筛选: 跳过 {skipped_files} 个不在时间段内的文件", file=sys.stderr)

    # 排序日期
    sorted_dates = sorted(date_range)
    date_str = f"{sorted_dates[0]}~{sorted_dates[-1]}" if len(sorted_dates) > 1 else (sorted_dates[0] if sorted_dates else "unknown")

    return {
        "bin_type_counts": bin_type_counts,
        "sql_patterns": sql_patterns,
        "all_sql_types": sorted(all_sql_types),
        "total_lines": total_lines,
        "total_sql_lines": total_sql_lines,
        "date_str": date_str,
    }


# ============================================================
# 数据库联动
# ============================================================

def _filter_mysql_warnings(stderr_text):
    """过滤 MySQL 命令行的无关警告信息（如密码警告），只保留真正的错误"""
    if not stderr_text:
        return ""
    lines = stderr_text.strip().split("\n")
    filtered = [l for l in lines if "Using a password on the command line interface can be insecure" not in l]
    return "\n".join(filtered).strip()


def db_query(host, port, user, password, database, sql):
    """通过 mysql 命令行执行查询"""
    cmd = [
        "mysql", "-h", host, "-P", str(port), "-u", user,
        f"-p{password}", "-N", "--batch", "-c"
    ]
    if database:
        cmd.append(database)
    cmd.extend(["-e", sql])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            err_msg = _filter_mysql_warnings(result.stderr)
            return f"ERROR: {err_msg[:200]}" if err_msg else f"ERROR: mysql 返回码 {result.returncode}"
    except subprocess.TimeoutExpired:
        return "ERROR: 查询超时(30s)"
    except Exception as e:
        return f"ERROR: {str(e)[:200]}"


def db_query_table(host, port, user, password, database, sql):
    """通过 mysql 命令行执行查询，返回带列头的表格输出"""
    cmd = [
        "mysql", "-h", host, "-P", str(port), "-u", user,
        f"-p{password}", "--batch", "-c"
    ]
    if database:
        cmd.append(database)
    cmd.extend(["-e", sql])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            err_msg = _filter_mysql_warnings(result.stderr)
            return f"ERROR: {err_msg[:200]}" if err_msg else f"ERROR: mysql 返回码 {result.returncode}"
    except subprocess.TimeoutExpired:
        return "ERROR: 查询超时(30s)"
    except Exception as e:
        return f"ERROR: {str(e)[:200]}"


def _url_decode_sql(sql_text):
    """对 interf 日志中的 SQL 做 URL 解码和清理"""
    if not sql_text:
        return ""
    s = sql_text
    s = s.replace("%20", " ").replace("%0A", " ").replace("%3D", "=")
    s = s.replace("%27", "'").replace("%28", "(").replace("%29", ")")
    s = s.replace("%2C", ",").replace("%25", "%")
    # 去掉 TDSQL 透传注释（如 /*sets:allsets*/）
    s = re.sub(r"/\*.*?\*/", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _convert_to_select(sql_text):
    """将 UPDATE/DELETE 语句转写为等价的 SELECT 语句

    转写规则:
      UPDATE table SET ... WHERE cond  →  SELECT * FROM table WHERE cond
      DELETE FROM table WHERE cond     →  SELECT * FROM table WHERE cond

    Returns:
        转写后的 SELECT 语句，转写失败返回 None
    """
    s = sql_text.strip()
    s_upper = s.upper()

    # UPDATE table [alias] SET ... WHERE ...  →  SELECT * FROM table [alias] WHERE ...
    if s_upper.startswith("UPDATE"):
        m = re.match(r"UPDATE\s+(.+?)\s+SET\s+", s, re.IGNORECASE | re.DOTALL)
        if m:
            table_part = m.group(1).strip()
            where_pos = s_upper.find(" WHERE ", m.end())
            if where_pos >= 0:
                where_clause = s[where_pos:]
                return f"SELECT * FROM {table_part}{where_clause}"
            else:
                return f"SELECT * FROM {table_part} LIMIT 1"
        return None

    # DELETE FROM table [alias] WHERE ...  →  SELECT * FROM table [alias] WHERE ...
    if s_upper.startswith("DELETE"):
        m = re.match(r"DELETE\s+FROM\s+(.+?)(\s+WHERE\s+.+)?$", s, re.IGNORECASE | re.DOTALL)
        if m:
            table_part = m.group(1).strip()
            where_clause = m.group(2) or " LIMIT 1"
            return f"SELECT * FROM {table_part}{where_clause}"
        # DELETE alias FROM table alias WHERE ...（多表 DELETE）
        m = re.match(r"DELETE\s+\w+\s+FROM\s+(.+?)(\s+WHERE\s+.+)?$", s, re.IGNORECASE | re.DOTALL)
        if m:
            table_part = m.group(1).strip()
            where_clause = m.group(2) or " LIMIT 1"
            return f"SELECT * FROM {table_part}{where_clause}"
        return None

    return None


def _safe_sql_for_explain(sql_text):
    """
    对 SQL 进行安全处理，返回 (explain_sql, skip_reason)
    - skip_reason 为 None 表示可以执行
    - skip_reason 非 None 表示跳过，值为跳过原因

    生产安全规则（最高优先级）:
      1. 最终执行的 SQL 必须且只能是 EXPLAIN SELECT ...
      2. UPDATE/DELETE 先转写为等价 SELECT，再加 EXPLAIN 前缀
      3. 转写后二次校验：确认最终是 EXPLAIN SELECT（不是则跳过）
      4. INSERT/REPLACE/DDL/SET/SHOW 等一律跳过
      5. 含分号的 SQL 直接拒绝（防多语句注入）
    """
    s = sql_text.strip() if sql_text else ""
    if not s:
        return None, "空SQL"

    # 安全检查: 拒绝包含分号的 SQL（防多语句注入）
    s_check = s.rstrip().rstrip(";").strip()
    if ";" in s_check:
        return None, "含分号，跳过（防多语句注入）"
    s = s_check

    # 获取第一个关键字
    first_word = s.split()[0].upper() if s.split() else ""

    if first_word == "SELECT":
        explain_sql = f"EXPLAIN {s}"

    elif first_word in ("UPDATE", "DELETE"):
        select_sql = _convert_to_select(s)
        if not select_sql:
            return None, f"跳过{first_word}（转写SELECT失败）"
        explain_sql = f"EXPLAIN {select_sql}"

    elif first_word in ("INSERT", "REPLACE"):
        return None, f"跳过{first_word}语句"
    elif first_word in ("SET", "SHOW", "USE", "BEGIN", "COMMIT", "ROLLBACK",
                         "CREATE", "ALTER", "DROP", "TRUNCATE", "GRANT", "REVOKE"):
        return None, f"跳过{first_word}语句（不支持EXPLAIN）"
    else:
        return None, f"跳过非DML语句({first_word})"

    # 最终安全校验（生产环境最高优先级）:
    # 确认拼接后的 SQL 严格以 "EXPLAIN SELECT" 开头
    final_check = explain_sql.strip().upper()
    if not final_check.startswith("EXPLAIN SELECT"):
        return None, f"安全校验失败（最终SQL非EXPLAIN SELECT，已拦截）"

    return explain_sql, None


def get_explain(host, port, user, password, database, sql_sample):
    """获取 SQL 的 EXPLAIN 执行计划（格式化输出）
    
    生产安全保障:
      1. URL 解码 + 清理透传注释
      2. UPDATE/DELETE 自动转写为等价 SELECT 再 EXPLAIN
      3. 只允许执行 EXPLAIN SELECT，其他一律拦截
      4. INSERT/REPLACE/DDL 等一律跳过
      5. 含分号的 SQL 直接拒绝（防多语句注入）
    """
    s = _url_decode_sql(sql_sample)
    if not s:
        return "N/A (空SQL)"

    explain_sql, skip_reason = _safe_sql_for_explain(s)
    if skip_reason is not None:
        return f"N/A ({skip_reason})"

    raw = db_query_table(host, port, user, password, database, explain_sql)
    if not raw or raw.startswith("ERROR"):
        return raw
    # 将 tab 分隔的表格转为可读的 key:value 格式
    lines = raw.split("\n")
    if len(lines) < 2:
        return raw
    headers = lines[0].split("\t")
    result_parts = []
    for row_line in lines[1:]:
        cols = row_line.split("\t")
        pairs = []
        for h, c in zip(headers, cols):
            if c and c != "NULL":
                pairs.append(f"{h}={c}")
        result_parts.append(" | ".join(pairs))
    return "; ".join(result_parts)


def get_table_info(host, port, user, password, database, table_name):
    """获取表的 CREATE TABLE 和 information_schema 统计"""
    # SHOW CREATE TABLE — 使用 database.table 格式避免 USE db 的大小写问题
    create_sql = f"SHOW CREATE TABLE `{database}`.`{table_name}`"
    create_result = db_query(host, port, user, password, "", create_sql)
    # 提取第二列（CREATE TABLE 语句）
    if create_result and not create_result.startswith("ERROR") and "\t" in create_result:
        parts = create_result.split("\t", 1)
        if len(parts) >= 2:
            create_result = parts[1]

    # 表数据量 — 使用带列头输出，更可读
    stats_sql = (
        f"SELECT TABLE_ROWS AS '行数(估算)', "
        f"ROUND(DATA_LENGTH / 1024 / 1024, 2) AS '数据大小(MB)', "
        f"ROUND(INDEX_LENGTH / 1024 / 1024, 2) AS '索引大小(MB)', "
        f"ROUND((DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 2) AS '总大小(MB)', "
        f"ENGINE AS '引擎', "
        f"TABLE_COLLATION AS '字符集' "
        f"FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA='{database}' AND TABLE_NAME='{table_name}'"
    )
    stats_raw = db_query_table(host, port, user, password, "", stats_sql)
    # 转为 key:value 格式
    stats_result = stats_raw
    if stats_raw and not stats_raw.startswith("ERROR") and "\n" in stats_raw:
        lines = stats_raw.split("\n")
        if len(lines) >= 2:
            headers = lines[0].split("\t")
            values = lines[1].split("\t")
            pairs = [f"{h}={v}" for h, v in zip(headers, values) if v and v != "NULL"]
            stats_result = " | ".join(pairs)

    return create_result, stats_result


def get_index_details(host, port, user, password, database, table_name):
    """获取表的索引详情（从 information_schema.STATISTICS）

    返回格式: 索引名(列名,列名)[UNIQUE|NONUNIQUE] CARDINALITY=N TYPE=BTREE; ...
    """
    sql = (
        f"SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX, NON_UNIQUE, CARDINALITY, "
        f"INDEX_TYPE, NULLABLE "
        f"FROM information_schema.STATISTICS "
        f"WHERE TABLE_SCHEMA='{database}' AND TABLE_NAME='{table_name}' "
        f"ORDER BY INDEX_NAME, SEQ_IN_INDEX"
    )
    raw = db_query(host, port, user, password, "", sql)
    if not raw or raw.startswith("ERROR"):
        return raw if raw else "N/A"

    indexes = defaultdict(lambda: {"columns": [], "unique": True, "cardinality": 0, "type": "", "nullable": ""})
    for line in raw.split("\n"):
        parts = line.split("\t")
        if len(parts) >= 7:
            idx_name = parts[0]
            col_name = parts[1]
            non_unique = parts[3]
            cardinality = parts[4]
            idx_type = parts[5]
            nullable = parts[6]
            indexes[idx_name]["columns"].append(col_name)
            indexes[idx_name]["unique"] = (non_unique == "0")
            indexes[idx_name]["cardinality"] = cardinality
            indexes[idx_name]["type"] = idx_type
            indexes[idx_name]["nullable"] = nullable

    if not indexes:
        return "N/A (无索引)"

    result_parts = []
    for idx_name, info in indexes.items():
        cols_str = ",".join(info["columns"])
        unique_str = "UNIQUE" if info["unique"] else "NONUNIQUE"
        result_parts.append(
            f"{idx_name}({cols_str})[{unique_str}] CARDINALITY={info['cardinality']} TYPE={info['type']}"
        )
    return "; ".join(result_parts)


def get_redundant_indexes(host, port, user, password, database, table_name):
    """检测冗余索引（从 sys.schema_redundant_indexes）

    返回格式: 冗余索引名→被包含于索引名; ... 或 "无冗余索引"
    """
    sql = (
        f"SELECT redundant_index_name, redundant_index_columns, "
        f"dominant_index_name, dominant_index_columns "
        f"FROM sys.schema_redundant_indexes "
        f"WHERE table_schema='{database}' AND table_name='{table_name}'"
    )
    raw = db_query(host, port, user, password, "", sql)
    if not raw or raw.startswith("ERROR"):
        if raw and "ERROR" in raw:
            return "N/A (sys库不可用)"
        return "无冗余索引"

    result_parts = []
    for line in raw.split("\n"):
        parts = line.split("\t")
        if len(parts) >= 4:
            result_parts.append(
                f"⚠️ {parts[0]}({parts[1]}) → 被 {parts[2]}({parts[3]}) 包含"
            )
    return "; ".join(result_parts) if result_parts else "无冗余索引"


def get_stats_update_info(host, port, user, password, database, table_name):
    """获取表的统计信息更新时间和数据修改时间

    Returns:
        tuple: (stats_update_time_str, data_update_time_str, is_expired, expire_msg)
    """
    # 获取 InnoDB 统计信息更新时间
    stats_sql = (
        f"/*sets:allsets*/SELECT last_update, n_rows "
        f"FROM mysql.innodb_table_stats "
        f"WHERE database_name='{database}' AND table_name='{table_name}'"
    )
    stats_raw = db_query(host, port, user, password, "", stats_sql)

    stats_update_time = ""
    stats_n_rows = ""
    if stats_raw and not stats_raw.startswith("ERROR"):
        earliest_time = None
        total_rows = 0
        for line in stats_raw.split("\n"):
            parts = line.split("\t")
            if len(parts) >= 2:
                t = parts[0].strip()
                n = parts[1].strip()
                try:
                    total_rows += int(n)
                except (ValueError, TypeError):
                    pass
                if t and t != "NULL":
                    if earliest_time is None or t < earliest_time:
                        earliest_time = t
        if earliest_time:
            stats_update_time = earliest_time
        if total_rows > 0:
            stats_n_rows = str(total_rows)

    # 获取表数据修改时间
    data_sql = (
        f"SELECT UPDATE_TIME, CREATE_TIME "
        f"FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA='{database}' AND TABLE_NAME='{table_name}'"
    )
    data_raw = db_query(host, port, user, password, "", data_sql)

    data_update_time = ""
    if data_raw and not data_raw.startswith("ERROR"):
        parts = data_raw.split("\t")
        if len(parts) >= 1:
            data_update_time = parts[0].strip()
            if data_update_time == "NULL" and len(parts) >= 2:
                data_update_time = parts[1].strip()

    # 判断统计信息是否过期
    is_expired = False
    expire_msg = "正常"

    if stats_update_time and stats_update_time != "NULL":
        try:
            stats_dt = datetime.strptime(stats_update_time, "%Y-%m-%d %H:%M:%S")
            now = datetime.now()
            days_diff = (now - stats_dt).days

            if days_diff > STATS_EXPIRE_DAYS:
                is_expired = True
                expire_msg = f"⚠️ 统计信息已 {days_diff} 天未更新(超过{STATS_EXPIRE_DAYS}天)，建议执行 ANALYZE TABLE `{database}`.`{table_name}`"
            else:
                expire_msg = f"正常({days_diff}天前更新)"
        except ValueError:
            expire_msg = f"无法解析时间: {stats_update_time}"
    else:
        expire_msg = "N/A (无统计信息记录)"

    return stats_update_time, data_update_time, is_expired, expire_msg


def extract_explain_issues(explain_text):
    """从 EXPLAIN 执行计划中提取潜在问题标记

    检测项: 全表扫描/索引全扫描/文件排序/临时表/大量行扫描/无索引
    """
    if not explain_text or explain_text.startswith("N/A"):
        return "N/A"

    issues = []

    if re.search(r'\btype=ALL\b', explain_text):
        issues.append("❌ 全表扫描(type=ALL)")
    if re.search(r'\btype=index\b', explain_text):
        issues.append("⚠️ 索引全扫描(type=index)")
    if re.search(r'Using filesort', explain_text, re.IGNORECASE):
        issues.append("⚠️ 文件排序(Using filesort)")
    if re.search(r'Using temporary', explain_text, re.IGNORECASE):
        issues.append("⚠️ 使用临时表(Using temporary)")

    rows_matches = re.findall(r'\brows=(\d+)', explain_text)
    for rows_str in rows_matches:
        rows_val = int(rows_str)
        if rows_val > 100000:
            issues.append(f"❌ 预估扫描行数过大(rows={rows_val})")
        elif rows_val > 10000:
            issues.append(f"⚠️ 预估扫描行数较多(rows={rows_val})")

    if re.search(r'\bkey=NULL\b', explain_text):
        issues.append("❌ 未使用索引(key=NULL)")

    return "; ".join(issues) if issues else "无明显问题"


def calc_scan_efficiency_from_explain(explain_text):
    """从 EXPLAIN 结果中计算扫描效率（rows vs filtered）

    interf 日志没有"平均扫描行数/平均返回行数"，因此从 EXPLAIN 的 rows 和 filtered 推算
    """
    if not explain_text or explain_text.startswith("N/A") or explain_text.startswith("ERROR"):
        return "N/A"

    try:
        rows_matches = re.findall(r'\brows=(\d+)', explain_text)
        filtered_matches = re.findall(r'\bfiltered=([\d.]+)', explain_text)

        if not rows_matches:
            return "N/A (无rows信息)"

        total_rows = sum(int(r) for r in rows_matches)
        if total_rows <= 0:
            return "N/A (rows=0)"

        if filtered_matches:
            # filtered 是百分比，表示经过 WHERE 过滤后保留的比例
            avg_filtered = sum(float(f) for f in filtered_matches) / len(filtered_matches)
            if avg_filtered >= 80:
                return f"filtered={avg_filtered:.1f}% (优秀)"
            elif avg_filtered >= 50:
                return f"filtered={avg_filtered:.1f}% (良好)"
            elif avg_filtered >= 10:
                return f"filtered={avg_filtered:.1f}% (⚠️ 较低，扫描了较多无用行)"
            else:
                return f"filtered={avg_filtered:.1f}% (❌ 极低，建议优化索引或查询条件)"
        else:
            # 没有 filtered 信息，仅报告扫描行数
            if total_rows > 100000:
                return f"rows={total_rows} (❌ 扫描行数过大)"
            elif total_rows > 10000:
                return f"rows={total_rows} (⚠️ 扫描行数较多)"
            else:
                return f"rows={total_rows} (正常)"
    except (ValueError, TypeError):
        return "N/A"


# ============================================================
# 输出: HTML 报告
# ============================================================

def generate_html(result, meta):
    """生成 HTML 报告（仅包含耗时区间 × SQL 类型交叉统计）"""
    buf = []
    buf.append("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>interf 日志深度分析 - {name} ({proxy_ip}:{port})</title>
<style>
:root {{ --bg: #f8f9fa; --card: #fff; --border: #e0e0e0; --text: #212529;
  --primary: #0d6efd; --muted: #6c757d; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
       background: var(--bg); color: var(--text); line-height: 1.6; padding: 20px; }}
.container {{ max-width: 1400px; margin: 0 auto; }}
h1 {{ color: var(--primary); border-bottom: 3px solid var(--primary); padding-bottom: 10px; margin: 20px 0; }}
h2 {{ color: #333; margin: 24px 0 12px; border-left: 4px solid var(--primary); padding-left: 12px; }}
p.meta {{ color: var(--muted); font-size: 0.9em; margin-bottom: 16px; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0 20px; background: var(--card);
        box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-radius: 6px; overflow: hidden; }}
th {{ background: #f1f3f5; color: #333; font-weight: 600; text-align: center; padding: 10px 12px;
     border-bottom: 2px solid var(--border); font-size: 0.9em; white-space: nowrap; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #f0f0f0; font-size: 0.88em; text-align: right; }}
td:first-child {{ text-align: left; font-weight: 600; }}
tr:hover td {{ background: #f8f9ff; }}
.total {{ font-weight: 700; background: #e9ecef !important; }}
.zero {{ color: #ccc; }}
.bar-cell {{ position: relative; }}
.bar {{ position: absolute; left: 0; top: 0; bottom: 0; background: rgba(13,110,253,0.08); z-index: 0; }}
.bar-val {{ position: relative; z-index: 1; }}
footer {{ margin-top: 30px; padding: 15px 0; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.85em; text-align: center; }}
</style>
</head>
<body>
<div class="container">
""".format(**meta))

    buf.append(f'<h1>interf 日志深度分析 - {_h(meta["name"])} ({_h(meta["proxy_ip"])}:{_h(meta["port"])})</h1>\n')
    buf.append(f'<p class="meta">分析时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | '
               f'日期范围: {_h(result["date_str"])} | '
               f'总行数: {result["total_lines"]:,} | '
               f'有效SQL行: {result["total_sql_lines"]:,}</p>\n')

    # === 耗时区间 × SQL 类型 交叉统计表 ===
    buf.append('<h2>SQL 耗时区间 × 类型分布</h2>\n')

    # 列: 核心 SQL 类型
    core_types = ["SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER"]
    # 把存在但不在核心列表中的类型加到后面
    other_types = [t for t in result["all_sql_types"] if t not in core_types]
    col_types = [t for t in core_types if t in result["all_sql_types"]] + other_types

    buf.append('<table>\n<tr><th>耗时区间</th>')
    for t in col_types:
        buf.append(f'<th>{_h(t)}</th>')
    buf.append('<th>合计</th></tr>\n')

    # 计算各列合计（用于底部合计行和柱状比例）
    col_totals = Counter()
    grand_total = 0
    row_totals = {}
    for low, high, label in TIMECOST_BINS:
        counts = result["bin_type_counts"].get(label, Counter())
        row_sum = sum(counts.values())
        row_totals[label] = row_sum
        grand_total += row_sum
        for t in col_types:
            col_totals[t] += counts.get(t, 0)

    max_row_total = max(row_totals.values()) if row_totals else 1

    for low, high, label in TIMECOST_BINS:
        counts = result["bin_type_counts"].get(label, Counter())
        row_sum = row_totals.get(label, 0)
        bar_pct = (row_sum / max_row_total * 100) if max_row_total > 0 else 0
        buf.append(f'<tr><td>{_h(label)}</td>')
        for t in col_types:
            v = counts.get(t, 0)
            cls = ' class="zero"' if v == 0 else ''
            buf.append(f'<td{cls}>{v:,}</td>')
        buf.append(f'<td class="bar-cell"><span class="bar" style="width:{bar_pct:.1f}%"></span>'
                   f'<span class="bar-val">{row_sum:,}</span></td></tr>\n')

    # 合计行
    buf.append('<tr class="total"><td>合计</td>')
    for t in col_types:
        buf.append(f'<td>{col_totals[t]:,}</td>')
    buf.append(f'<td>{grand_total:,}</td></tr>\n')

    # 占比行
    buf.append('<tr class="total"><td>占比</td>')
    for t in col_types:
        pct = (col_totals[t] / grand_total * 100) if grand_total > 0 else 0
        buf.append(f'<td>{pct:.1f}%</td>')
    buf.append('<td>100%</td></tr>\n')
    buf.append('</table>\n')

    buf.append(f'<footer>Generated by interf_deep_analysis.py v{VERSION} | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</footer>\n')
    buf.append('</div>\n</body>\n</html>')
    return "".join(buf)


# ============================================================
# 输出: CSV 文件
# ============================================================

def write_timecost_detail_csv(filepath, result):
    """写入 SQL 耗时细分 CSV"""
    col_types = ["SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER"]
    other_types = [t for t in result["all_sql_types"] if t not in col_types]
    col_types = [t for t in col_types if t in result["all_sql_types"]] + other_types

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["耗时区间"] + col_types + ["合计"])
        for low, high, label in TIMECOST_BINS:
            counts = result["bin_type_counts"].get(label, Counter())
            row = [label] + [counts.get(t, 0) for t in col_types]
            row.append(sum(counts.values()))
            writer.writerow(row)
    print(f"  [输出] {filepath}", file=sys.stderr)


def write_pattern_summary_csv(filepath, result):
    """写入 SQL 去重聚合 CSV"""
    patterns = result["sql_patterns"]
    # 按执行次数降序
    sorted_items = sorted(patterns.items(), key=lambda x: x[1]["count"], reverse=True)

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["序号", "SQL类型", "库名", "执行次数", "平均耗时(ms)", "最大耗时(ms)",
                         "最小耗时(ms)", "autocommit", "执行用户", "SQL完整性", "涉及表", "归一化SQL", "原始SQL"])
        for i, ((pattern, db), info) in enumerate(sorted_items, 1):
            avg_tc = info["tc_sum"] / info["count"] if info["count"] > 0 else 0
            tables = ",".join(info["tables"]) if info["tables"] else ""
            # 原始 SQL: URL 解码后的完整 SQL
            raw_sql = _url_decode_sql(info["sample_sql"])
            sql_integrity = "SQL被截断" if info.get("is_truncated") else "完整"
            users_str = ",".join(sorted(info.get("users", set()))) if info.get("users") else ""
            writer.writerow([
                i, info["sql_type"], db, info["count"],
                f'{avg_tc:.2f}', f'{info["tc_max"]:.2f}', f'{info["tc_min"]:.2f}',
                info["autocommit"], users_str, sql_integrity, tables, pattern, raw_sql
            ])
    print(f"  [输出] {filepath}", file=sys.stderr)


def parse_explain_key_info(explain_str):
    """从格式化的 EXPLAIN 结果中提取索引信息"""
    if not explain_str or explain_str.startswith("N/A") or explain_str.startswith("ERROR"):
        return "N/A", "N/A", "N/A"
    
    # 解析 key=value 格式（可能有多行用 ; 分隔表示多步）
    # 取第一步的信息
    first_step = explain_str.split(";")[0] if ";" in explain_str else explain_str
    
    key_name = ""
    rows = ""
    access_type = ""
    
    for part in first_step.split(" | "):
        part = part.strip()
        if part.startswith("key="):
            key_name = part[4:]
        elif part.startswith("rows="):
            rows = part[5:]
        elif part.startswith("type="):
            access_type = part[5:]
    
    # 判断是否走了索引
    if key_name and key_name not in ("NULL", ""):
        use_index = "是"
    else:
        use_index = "否"
        key_name = "全表扫描" if access_type == "ALL" else ("无" if not key_name or key_name == "NULL" else key_name)
    
    return use_index, key_name, rows if rows else "N/A"


def write_explain_schema_csv(filepath, result, db_config):
    """写入执行计划+表结构 CSV（需要数据库连接）
    
    增强列（与 slow_sql_enrich.py 对齐）:
      EXPLAIN执行计划、EXPLAIN问题标记、涉及表、表数据量、表结构、
      索引详情、冗余索引、统计信息更新时间、统计信息是否过期、扫描效率
    """
    if not db_config:
        print("  [跳过] 未提供数据库连接信息，跳过执行计划和表结构获取", file=sys.stderr)
        return

    host, port, user, password = db_config
    patterns = result["sql_patterns"]
    # 按执行次数降序，取 Top 200
    sorted_items = sorted(patterns.items(), key=lambda x: x[1]["count"], reverse=True)[:200]

    # 收集所有涉及的表（按库分组，只收集完整SQL的表）
    db_tables = defaultdict(set)  # {db: {table1, table2, ...}}
    for (pattern, db), info in sorted_items:
        if db and info["tables"] and not info.get("is_truncated"):
            for t in info["tables"]:
                db_tables[db].add(t)

    # 批量获取表信息（去重，避免重复查询）
    table_info_cache = {}       # {(db, table): (create_table, stats)}
    index_details_cache = {}    # {(db, table): index_details_str}
    redundant_idx_cache = {}    # {(db, table): redundant_idx_str}
    stats_update_cache = {}     # {(db, table): (stats_time, data_time, is_expired, expire_msg)}

    total_tables = sum(len(ts) for ts in db_tables.values())
    done = 0
    for db, tables in db_tables.items():
        for table_name in sorted(tables):
            done += 1
            print(f"  [数据库] 获取表信息 ({done}/{total_tables}): {db}.{table_name}", file=sys.stderr)
            create_result, stats_result = get_table_info(host, port, user, password, db, table_name)
            table_info_cache[(db, table_name)] = (create_result, stats_result)
            index_details_cache[(db, table_name)] = get_index_details(
                host, port, user, password, db, table_name
            )
            redundant_idx_cache[(db, table_name)] = get_redundant_indexes(
                host, port, user, password, db, table_name
            )
            stats_update_cache[(db, table_name)] = get_stats_update_info(
                host, port, user, password, db, table_name
            )

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "序号", "SQL类型", "库名", "执行次数", "平均耗时(ms)",
            "执行用户",
            "SQL完整性", "是否走索引", "索引名称", "扫描行数",
            "归一化SQL", "原始SQL",
            "EXPLAIN执行计划", "EXPLAIN问题标记",
            "涉及表", "表数据量", "表结构",
            "索引详情", "冗余索引",
            "统计信息更新时间", "统计信息是否过期",
            "扫描效率"
        ])

        for i, ((pattern, db), info) in enumerate(sorted_items, 1):
            avg_tc = info["tc_sum"] / info["count"] if info["count"] > 0 else 0

            # URL 解码后的完整原始 SQL
            raw_sql = _url_decode_sql(info["sample_sql"])

            # SQL 完整性检测
            is_truncated = info.get("is_truncated", False)
            sql_integrity = "SQL被截断" if is_truncated else "完整"

            # EXPLAIN（截断的 SQL 跳过）
            explain_result = ""
            if is_truncated:
                explain_result = "N/A (SQL被截断，跳过EXPLAIN)"
                print(f"  [跳过] EXPLAIN ({i}/{len(sorted_items)}): SQL被截断，跳过", file=sys.stderr)
            elif db and info["sample_sql"]:
                print(f"  [数据库] EXPLAIN ({i}/{len(sorted_items)}): {pattern[:60]}...", file=sys.stderr)
                explain_result = get_explain(host, port, user, password, db, info["sample_sql"])

            # 从 EXPLAIN 提取索引信息
            use_index, key_name, scan_rows = parse_explain_key_info(explain_result)

            # EXPLAIN 问题标记
            explain_issues = extract_explain_issues(explain_result)

            # 扫描效率（从 EXPLAIN 的 rows/filtered 推算）
            scan_efficiency = calc_scan_efficiency_from_explain(explain_result)

            # 表信息汇总
            table_stats_list = []
            create_table_list = []
            index_details_parts = []
            redundant_idx_parts = []
            stats_update_parts = []
            stats_expire_parts = []

            for t in info["tables"]:
                key = (db, t)
                # 表数据量 & 表结构
                if key in table_info_cache:
                    create_result, stats_result = table_info_cache[key]
                    table_stats_list.append(f"{t}: {stats_result}")
                    create_table_list.append(f"-- {t} --\n{create_result}")

                # 索引详情
                idx_info = index_details_cache.get(key, "N/A (未查询)")
                index_details_parts.append(f"[{t}] {idx_info}")

                # 冗余索引
                redundant = redundant_idx_cache.get(key, "N/A (未查询)")
                if redundant and redundant != "无冗余索引":
                    redundant_idx_parts.append(f"[{t}] {redundant}")

                # 统计信息更新时间 & 是否过期
                stats_info = stats_update_cache.get(key)
                if stats_info:
                    stats_time, data_time, is_expired, expire_msg = stats_info
                    stats_update_parts.append(
                        f"[{t}] 统计更新={stats_time or 'N/A'} | 数据修改={data_time or 'N/A'}"
                    )
                    stats_expire_parts.append(f"[{t}] {expire_msg}")
                else:
                    stats_update_parts.append(f"[{t}] N/A")
                    stats_expire_parts.append(f"[{t}] N/A")

            index_details_str = " || ".join(index_details_parts) if index_details_parts else "N/A"
            redundant_idx_str = " || ".join(redundant_idx_parts) if redundant_idx_parts else "无冗余索引"
            stats_update_str = " || ".join(stats_update_parts) if stats_update_parts else "N/A"
            stats_expire_str = " || ".join(stats_expire_parts) if stats_expire_parts else "N/A"

            # 执行用户（从 interf 日志中提取，可能有多个用户执行同一 SQL 模式）
            users_str = ",".join(sorted(info.get("users", set()))) if info.get("users") else ""

            # 表结构：将换行符替换为 \\n，避免破坏 CSV 行格式
            create_table_str = "\\n".join(create_table_list)[:3000] if create_table_list else ""
            create_table_str = create_table_str.replace("\n", "\\n")

            # EXPLAIN 结果和原始 SQL 也做同样处理
            explain_str = (explain_result[:3000] if explain_result else "").replace("\n", "\\n")
            raw_sql_str = raw_sql.replace("\n", " ") if raw_sql else ""

            writer.writerow([
                i, info["sql_type"], db, info["count"], f'{avg_tc:.2f}',
                users_str,
                sql_integrity, use_index, key_name, scan_rows,
                pattern,
                raw_sql_str,
                explain_str,
                explain_issues[:1000] if explain_issues else "",
                ",".join(info["tables"]),
                " | ".join(table_stats_list) if table_stats_list else "",
                create_table_str,
                index_details_str[:2000],
                redundant_idx_str[:1000],
                stats_update_str[:1000],
                stats_expire_str[:1000],
                scan_efficiency,
            ])

    print(f"  [输出] {filepath}", file=sys.stderr)


# ============================================================
# 配置文件解析
# ============================================================

def load_proxy_config(config_path, index):
    """从 tdsql_env.conf 读取 [gateway_proxies] 单条配置"""
    configs = load_all_proxy_configs(config_path)
    return configs.get(index)


def _get_local_ips():
    """获取本机所有 IP 地址"""
    ips = {"127.0.0.1"}
    try:
        # 通过 hostname -I 获取（Linux）
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            for ip in result.stdout.strip().split():
                ips.add(ip.strip())
    except Exception:
        pass
    try:
        # 通过 socket 获取
        import socket
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if not ip.startswith("fe80"):  # 排除 IPv6 链路本地地址
                ips.add(ip)
    except Exception:
        pass
    return ips


def load_all_proxy_configs(config_path):
    """从 tdsql_env.conf 读取 [gateway_proxies] 所有配置"""
    if not os.path.exists(config_path):
        print(f"配置文件不存在: {config_path}", file=sys.stderr)
        return {}

    configs = {}  # {index: {name, proxy_ip, port, db_user, db_pass}}
    in_section = False
    with open(config_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("["):
                in_section = line == "[gateway_proxies]"
                continue
            if not in_section or not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            m = re.match(r"proxies_(\d+)", key)
            if not m:
                continue
            idx = int(m.group(1))
            parts = value.split(",")
            if len(parts) >= 5:
                configs[idx] = {
                    "name": parts[0],
                    "proxy_ip": parts[1],
                    "port": parts[2],
                    "db_user": parts[3],
                    "db_pass": ",".join(parts[4:]),  # 密码可能含逗号
                }
            elif len(parts) >= 3:
                configs[idx] = {
                    "name": parts[0],
                    "proxy_ip": parts[1],
                    "port": parts[2],
                    "db_user": None,
                    "db_pass": None,
                }
    return configs


# ============================================================
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="TDSQL Gateway interf 日志深度分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--files", nargs="+", metavar="FILE",
                        help="interf 日志文件路径（可多个）")
    parser.add_argument("-d", "--log-dir", default=None,
                        help="interf 日志所在目录（配合 --dates 使用，自动匹配文件）")
    parser.add_argument("--dates", nargs="+", metavar="DATE",
                        help="指定日期范围，格式 YYYY-MM-DD，可多个（配合 --log-dir 或 --port 自动匹配文件）")
    parser.add_argument("--time-range", metavar="HH:MM-HH:MM", default=None,
                        help="按时间段过滤，格式 HH:MM-HH:MM，如 14:00-16:00（只分析该时段内的 SQL）")
    parser.add_argument("--name", default="unknown", help="业务名称（用于文件命名和报告标题）")
    parser.add_argument("--proxy-ip", default="unknown", help="Proxy 节点 IP")
    parser.add_argument("--port", default="0", help="Gateway 端口号")
    parser.add_argument("--db-host", default=None, help="数据库连接 IP（用于获取执行计划和表结构）")
    parser.add_argument("--db-port", default=None, help="数据库连接端口")
    parser.add_argument("--db-user", default=None, help="数据库用户名")
    parser.add_argument("--db-pass", default=None, help="数据库密码")
    parser.add_argument("--config-index", type=int, default=None,
                        help="从 tdsql_env.conf 的 [gateway_proxies] 读取第 N 条配置")
    parser.add_argument("--config-all", action="store_true",
                        help="自动遍历 [gateway_proxies] 中所有配置，批量分析所有实例")
    parser.add_argument("--config-local", action="store_true",
                        help="只分析 proxy_ip 与本机 IP 匹配的实例（适用于多 Proxy 节点通过 sshpass_pack 远程执行）")
    parser.add_argument("--config-file", default=None, help="配置文件路径")
    parser.add_argument("-o", "--output-dir", default=None,
                        help="输出目录（默认: 脚本同级 output/{日期} 目录）")
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args()

    # 解析 --time-range
    time_range = None
    if args.time_range:
        m = re.match(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", args.time_range)
        if not m:
            parser.error("--time-range 格式错误，请使用 HH:MM-HH:MM，如 14:00-16:00")
        time_range = (f"{int(m.group(1)):02d}:{m.group(2)}", f"{int(m.group(3)):02d}:{m.group(4)}")
        print(f"  [时间过滤] {time_range[0]} ~ {time_range[1]}", file=sys.stderr)

    # 自动查找 tdsql_env.conf
    config_path = args.config_file
    if not config_path:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cwd = os.getcwd()
        for candidate in [
            os.path.join(script_dir, "..", "tdsql_env.conf"),
            os.path.join(script_dir, "tdsql_env.conf"),
            os.path.join(cwd, "tdsql_env.conf"),
            os.path.join(cwd, "..", "tdsql_env.conf"),
            "/tmp/tdsql_env.conf",
        ]:
            if os.path.exists(candidate):
                config_path = os.path.abspath(candidate)
                print(f"  [配置] 自动找到配置文件: {config_path}", file=sys.stderr)
                break
        if not config_path and (args.config_all or args.config_local or args.config_index is not None):
            print("  [错误] 未找到 tdsql_env.conf，请通过 --config-file 指定路径", file=sys.stderr)
            print("  [提示] 搜索路径: 脚本目录/../、脚本目录/、当前目录/、/tmp/", file=sys.stderr)
            sys.exit(1)

    # --config-all / --config-local: 批量分析
    if args.config_all or args.config_local:
        if not config_path:
            parser.error("--config-all/--config-local 需要 tdsql_env.conf 配置文件")
        all_configs = load_all_proxy_configs(config_path)
        if not all_configs:
            print("错误: [gateway_proxies] 中没有有效配置", file=sys.stderr)
            sys.exit(1)
        
        # --config-local: 获取本机 IP，只保留匹配的配置
        if args.config_local:
            local_ips = _get_local_ips()
            filtered = {k: v for k, v in all_configs.items() if v["proxy_ip"] in local_ips}
            if not filtered:
                print(f"  [跳过] 本机 IP {local_ips} 未匹配到任何 [gateway_proxies] 配置", file=sys.stderr)
                sys.exit(0)
            print(f"  [本机模式] 本机 IP: {', '.join(sorted(local_ips))}", file=sys.stderr)
            print(f"  [本机模式] 匹配到 {len(filtered)}/{len(all_configs)} 个实例", file=sys.stderr)
            all_configs = filtered
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  批量分析模式: 共 {len(all_configs)} 个实例", file=sys.stderr)
        for idx in sorted(all_configs.keys()):
            cfg = all_configs[idx]
            print(f"    proxies_{idx} = {cfg['name']},{cfg['proxy_ip']},{cfg['port']}", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)

        success_count = 0
        for idx in sorted(all_configs.keys()):
            cfg = all_configs[idx]
            print(f"\n{'─'*60}", file=sys.stderr)
            print(f"  [{success_count+1}/{len(all_configs)}] 分析 proxies_{idx}: {cfg['name']} ({cfg['proxy_ip']}:{cfg['port']})", file=sys.stderr)
            print(f"{'─'*60}", file=sys.stderr)
            ok = run_single_analysis(
                dates=args.dates, files=args.files, log_dir=args.log_dir,
                name=cfg["name"], proxy_ip=cfg["proxy_ip"], port=cfg["port"],
                db_host=cfg["proxy_ip"] if cfg.get("db_user") else None,
                db_port=cfg["port"] if cfg.get("db_user") else None,
                db_user=cfg.get("db_user"), db_pass=cfg.get("db_pass"),
                output_dir=args.output_dir, time_range=time_range,
            )
            if ok:
                success_count += 1

        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  批量分析完成: {success_count}/{len(all_configs)} 个实例成功", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)
        return

    # --config-index: 加载单条配置
    if args.config_index is not None:
        if not config_path:
            parser.error("--config-index 需要 tdsql_env.conf 配置文件")
        cfg = load_proxy_config(config_path, args.config_index)
        if cfg:
            args.name = cfg["name"]
            args.proxy_ip = cfg["proxy_ip"]
            args.port = cfg["port"]
            if cfg.get("db_user"):
                args.db_host = args.db_host or cfg["proxy_ip"]
                args.db_port = args.db_port or cfg["port"]
                args.db_user = args.db_user or cfg["db_user"]
                args.db_pass = args.db_pass or cfg["db_pass"]
            print(f"  [配置] 已加载 proxies_{args.config_index}: {cfg['name']},{cfg['proxy_ip']},{cfg['port']}", file=sys.stderr)
        else:
            print(f"  [错误] 配置文件中未找到 proxies_{args.config_index}", file=sys.stderr)
            sys.exit(1)

    # 单实例分析（检查是否缺少必要参数）
    if args.name == "unknown" and args.config_index is None:
        print("  [警告] 未指定 --config-index/--config-all/--config-local，也未指定 --name", file=sys.stderr)
        print("  [提示] 推荐用法:", file=sys.stderr)
        print("    python3 interf_deep_analysis.py --dates 2026-03-31 --config-all", file=sys.stderr)
        print("    python3 interf_deep_analysis.py --dates 2026-03-31 --config-local", file=sys.stderr)
        print("    python3 interf_deep_analysis.py --dates 2026-03-31 --config-index 1", file=sys.stderr)
        print("    python3 interf_deep_analysis.py --dates 2026-03-31 --config-file /path/to/tdsql_env.conf --config-all", file=sys.stderr)
        sys.exit(1)

    run_single_analysis(
        dates=args.dates, files=args.files, log_dir=args.log_dir,
        name=args.name, proxy_ip=args.proxy_ip, port=args.port,
        db_host=args.db_host, db_port=args.db_port,
        db_user=args.db_user, db_pass=args.db_pass,
        output_dir=args.output_dir, time_range=time_range,
    )


def run_single_analysis(dates, files, log_dir, name, proxy_ip, port,
                        db_host, db_port, db_user, db_pass, output_dir,
                        time_range=None):
    """执行单个实例的分析，返回是否成功"""
    import glob as glob_mod

    # 通过 --dates 自动查找 interf 文件
    if dates and not files:
        _log_dir = log_dir
        if not _log_dir and port and port != "0":
            _log_dir = f"/data/tdsql_run/{port}/gateway/log"
        if not _log_dir:
            print(f"  [错误] {name}: 无法确定日志目录，需要 --log-dir 或 --port", file=sys.stderr)
            return False

        files = []
        for date_str in sorted(dates):
            pattern_glob = os.path.join(_log_dir, f"interf_instance_{port}.{date_str}.*")
            matched = sorted(glob_mod.glob(pattern_glob))
            if matched:
                files.extend(matched)
            else:
                print(f"  [警告] 未找到匹配文件: {pattern_glob}", file=sys.stderr)

        if not files:
            current_file = os.path.join(_log_dir, f"interf_instance_{port}")
            if os.path.isfile(current_file):
                files = [current_file]

        if files:
            print(f"  [日期过滤] 匹配到 {len(files)} 个文件", file=sys.stderr)
        else:
            print(f"  [跳过] {name}: --dates 未匹配到任何 interf 文件", file=sys.stderr)
            return False

    if not files:
        print(f"  [错误] {name}: 必须指定 --files 或 --dates 参数", file=sys.stderr)
        return False

    # 验证文件
    valid_files = []
    for f in files:
        abs_f = os.path.abspath(f)
        if os.path.isfile(abs_f):
            valid_files.append(abs_f)
        else:
            print(f"  [警告] 文件不存在，跳过: {f}", file=sys.stderr)
    if not valid_files:
        print(f"  [跳过] {name}: 没有有效的 interf 文件", file=sys.stderr)
        return False

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  interf 日志深度分析 v{VERSION}", file=sys.stderr)
    print(f"  业务: {name} | Proxy: {proxy_ip}:{port}", file=sys.stderr)
    print(f"  文件: {len(valid_files)} 个", file=sys.stderr)
    print(f"  数据库联动: {'是' if db_host else '否'}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    # 分析
    result = analyze_interf_files(valid_files, time_range=time_range)

    print(f"\n  [统计] 总行数: {result['total_lines']:,}, 有效SQL行: {result['total_sql_lines']:,}", file=sys.stderr)
    print(f"  [统计] SQL 模式数: {len(result['sql_patterns']):,}", file=sys.stderr)
    print(f"  [统计] 日期范围: {result['date_str']}", file=sys.stderr)

    # 构造输出目录
    _output_dir = output_dir
    if _output_dir is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        date_dir = result['date_str'].split("~")[0] if "~" in result['date_str'] else result['date_str']
        _output_dir = os.path.join(script_dir, "output", date_dir)
    os.makedirs(_output_dir, exist_ok=True)

    # 构造输出文件名前缀（含执行时间）
    exec_time = datetime.now().strftime("%H%M%S")
    prefix = f"{name}_{proxy_ip}_{port}_{result['date_str']}_{exec_time}"
    prefix = re.sub(r'[<>:"/\\|?*]', '_', prefix)

    meta = {"name": name, "proxy_ip": proxy_ip, "port": port}

    # 输出 CSV 1: 耗时细分
    csv1_path = os.path.join(_output_dir, f"{prefix}_sql_timecost_detail.csv")
    write_timecost_detail_csv(csv1_path, result)

    # 输出 CSV 2: SQL 去重聚合
    csv2_path = os.path.join(_output_dir, f"{prefix}_sql_pattern_summary.csv")
    write_pattern_summary_csv(csv2_path, result)

    # 输出 CSV 3: 执行计划+表结构
    db_config = None
    if db_host and db_port and db_user and db_pass:
        db_config = (db_host, db_port, db_user, db_pass)
    csv3_path = os.path.join(_output_dir, f"{prefix}_sql_explain_schema.csv")
    write_explain_schema_csv(csv3_path, result, db_config)

    print(f"\n  [完成] 所有输出文件已生成到: {os.path.abspath(_output_dir)}", file=sys.stderr)
    return True


if __name__ == "__main__":
    main()
