"""M1 · G2 慢SQL 十列增强诊断服务（源自原厂 slow_sql_enrich.py）

对每条慢SQL连接对应业务库，追加十项诊断：
  EXPLAIN执行计划 / EXPLAIN问题标记 / 涉及表 / 表数据量 / 表结构 /
  索引详情 / 冗余索引 / 统计信息更新时间 / 统计信息是否过期 / 扫描效率

安全红线（最高优先级）：
  1. 只执行 EXPLAIN SELECT；UPDATE/DELETE 自动转写为等价 SELECT 再 EXPLAIN
  2. 最终校验拼接后 SQL 必须以 EXPLAIN SELECT 开头，否则拦截
  3. 含分号的 SQL 直接拒绝（防多语句注入）
  4. INSERT/REPLACE/DDL 等一律跳过

健壮性（评审整改 §0.4）：
  - 系统库/视图无权限（1142/1044）→ 该项填 N/A(权限不足)，绝不中断主流程
  - 统计信息回退 information_schema.TABLES.UPDATE_TIME/CREATE_TIME
  - 所有比率计算防除零
"""
import logging
import re
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger("tdsql.slow_enrich")

STATS_EXPIRE_DAYS = 15

_SYS_SCHEMAS = {"information_schema", "mysql", "performance_schema", "sys"}
_SQL_KEYWORDS = {
    "SET", "VALUES", "INTO", "FROM", "WHERE", "AND", "OR", "ON", "SELECT",
    "UPDATE", "INSERT", "DELETE", "REPLACE", "NULL", "LIMIT", "ORDER", "GROUP",
    "HAVING", "UNION", "ALL", "AS", "DUAL", "LEFT", "RIGHT", "INNER", "OUTER",
    "CROSS", "JOIN", "NOT", "IN", "EXISTS", "BETWEEN", "LIKE", "IS", "CASE",
    "WHEN", "THEN", "ELSE", "END", "DISTINCT", "COUNT", "SUM", "IGNORE",
}


# ── 纯函数（无DB，可单测）────────────────────────────────────────────

def clean_sql(sql_text: str) -> str:
    """清理 SQL：去除透传注释、压缩空白。"""
    if not sql_text:
        return ""
    s = sql_text.strip()
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)   # 去 /*sets:...*/ 等
    s = re.sub(r"\s+", " ", s).strip()
    return s


def convert_to_select(sql_text: str):
    """UPDATE/DELETE → 等价 SELECT（提取表与 WHERE）。失败返回 None。"""
    s = sql_text.strip()
    su = s.upper()
    if su.startswith("UPDATE"):
        m = re.match(r"UPDATE\s+(.+?)\s+SET\s+", s, re.IGNORECASE | re.DOTALL)
        if m:
            table_part = m.group(1).strip()
            where_pos = su.find(" WHERE ", m.end())
            if where_pos >= 0:
                return f"SELECT * FROM {table_part}{s[where_pos:]}"
            return f"SELECT * FROM {table_part} LIMIT 1"
        return None
    if su.startswith("DELETE"):
        m = re.match(r"DELETE\s+FROM\s+(.+?)(\s+WHERE\s+.+)?$", s, re.IGNORECASE | re.DOTALL)
        if m:
            return f"SELECT * FROM {m.group(1).strip()}{m.group(2) or ' LIMIT 1'}"
        m = re.match(r"DELETE\s+\w+\s+FROM\s+(.+?)(\s+WHERE\s+.+)?$", s, re.IGNORECASE | re.DOTALL)
        if m:
            return f"SELECT * FROM {m.group(1).strip()}{m.group(2) or ' LIMIT 1'}"
        return None
    return None


def safe_sql_for_explain(sql_text: str):
    """返回 (explain_sql, skip_reason)。skip_reason 非 None 表示跳过。

    安全不依赖正则正确性：最终门禁确保只放行 EXPLAIN SELECT，
    转写失败/非法只会少一条诊断，绝不误执行写操作（评审整改 §0.4-3）。
    """
    s = clean_sql(sql_text)
    if not s:
        return None, "空SQL"
    s_check = s.rstrip().rstrip(";").strip()
    if ";" in s_check:
        return None, "含分号，跳过（防多语句注入）"
    s = s_check
    first = s.split()[0].upper() if s.split() else ""
    if first == "SELECT":
        explain_sql = f"EXPLAIN {s}"
    elif first in ("UPDATE", "DELETE"):
        sel = convert_to_select(s)
        if not sel:
            return None, f"跳过{first}（转写SELECT失败）"
        explain_sql = f"EXPLAIN {sel}"
    elif first in ("INSERT", "REPLACE"):
        return None, f"跳过{first}语句"
    elif first in ("SET", "SHOW", "USE", "BEGIN", "COMMIT", "ROLLBACK",
                   "CREATE", "ALTER", "DROP", "TRUNCATE", "GRANT", "REVOKE"):
        return None, f"跳过{first}语句（不支持EXPLAIN）"
    else:
        return None, f"跳过非DML语句({first})"
    if not explain_sql.strip().upper().startswith("EXPLAIN SELECT"):
        return None, "安全校验失败（最终SQL非EXPLAIN SELECT，已拦截）"
    return explain_sql, None


def _filter_table_names(tables) -> list:
    return sorted(
        t for t in tables
        if t.upper() not in _SQL_KEYWORDS and t not in _SYS_SCHEMAS and len(t) > 1
    )


def extract_tables_from_sql(sql_text: str) -> list:
    """从 SQL 提取涉及表（FROM/JOIN/UPDATE/INSERT 目标），过滤关键字/系统库。"""
    if not sql_text:
        return []
    s = clean_sql(sql_text)
    if not s:
        return []
    tables = set()
    m = re.match(r"(?:INSERT|REPLACE)\s+(?:IGNORE\s+)?(?:INTO\s+)?`?(\w+)`?(?:\.`?(\w+)`?)?",
                 s, re.IGNORECASE)
    if m:
        tables.add(m.group(2) or m.group(1))
    m = re.match(r"UPDATE\s+(.+?)\s+SET\b", s, re.IGNORECASE)
    if m:
        for part in m.group(1).split(","):
            tm = re.match(r"`?(\w+)`?(?:\.`?(\w+)`?)?\s*", part.strip())
            if tm:
                tables.add(tm.group(2) or tm.group(1))
    for m in re.finditer(r"\bFROM\s+", s, re.IGNORECASE):
        pos = m.end()
        if pos < len(s) and s[pos] == "(":
            continue
        rest = s[pos:]
        end_m = re.search(r"\b(?:WHERE|GROUP|ORDER|LIMIT|HAVING|UNION|ON|LEFT|RIGHT|"
                          r"INNER|CROSS|FULL|OUTER|JOIN|SET|VALUES)\b|\(|\)",
                          rest, re.IGNORECASE)
        from_clause = rest[:end_m.start()] if end_m else rest
        for part in from_clause.split(","):
            part = part.strip()
            if not part:
                continue
            tm = re.match(r"`?(\w+)`?(?:\.`?(\w+)`?)?\s*", part)
            if tm:
                tables.add(tm.group(2) or tm.group(1))
    for m in re.finditer(r"\bJOIN\s+`?(\w+)`?(?:\.`?(\w+)`?)?", s, re.IGNORECASE):
        tables.add(m.group(2) or m.group(1))
    return _filter_table_names(tables)


def extract_explain_issues(explain_text: str) -> str:
    """从 EXPLAIN 文本提取问题标记。"""
    if not explain_text or explain_text.startswith("N/A"):
        return "N/A"
    issues = []
    if re.search(r"\btype=ALL\b", explain_text):
        issues.append("❌ 全表扫描(type=ALL)")
    if re.search(r"\btype=index\b", explain_text):
        issues.append("⚠️ 索引全扫描(type=index)")
    if re.search(r"Using filesort", explain_text, re.IGNORECASE):
        issues.append("⚠️ 文件排序(Using filesort)")
    if re.search(r"Using temporary", explain_text, re.IGNORECASE):
        issues.append("⚠️ 使用临时表(Using temporary)")
    for rows_str in re.findall(r"\brows=(\d+)", explain_text):
        rv = int(rows_str)
        if rv > 100000:
            issues.append(f"❌ 预估扫描行数过大(rows={rv})")
        elif rv > 10000:
            issues.append(f"⚠️ 预估扫描行数较多(rows={rv})")
    if re.search(r"\bkey=NULL\b", explain_text):
        issues.append("❌ 未使用索引(key=NULL)")
    return "; ".join(issues) if issues else "无明显问题"


def calc_scan_efficiency(examined, sent) -> str:
    """扫描效率 = 返回行/扫描行；防除零（评审整改 §0.4-4）。"""
    try:
        examined = float(examined or 0)
        sent = float(sent or 0)
    except (ValueError, TypeError):
        return "N/A"
    if examined <= 0:
        return "N/A (无扫描行数)"
    eff = sent / examined
    if eff >= 0.8:
        return f"{eff:.4f} (优秀)"
    if eff >= 0.5:
        return f"{eff:.4f} (良好)"
    if eff >= 0.1:
        return f"{eff:.4f} (⚠️ 较低，扫描了较多无用行)"
    return f"{eff:.4f} (❌ 极低，建议优化索引或查询条件)"


# ── DB 联动（业务库，全部只读 + 优雅降级）──────────────────────────

def _is_perm_error(err: str) -> bool:
    e = (err or "").lower()
    return "1142" in e or "1044" in e or "command denied" in e or "access denied" in e


def _q(pool, sql: str, params=None):
    """业务库只读查询；失败返回 ("ERROR:..", None)。"""
    try:
        return None, pool._execute(sql, params)
    except Exception as e:
        return f"ERROR: {str(e)[:200]}", None


def get_explain(pool, db: str, sql_sample: str) -> str:
    """安全 EXPLAIN。在目标库上下文(select_db)执行，使无库限定的表名可解析。"""
    explain_sql, skip = safe_sql_for_explain(sql_sample)
    if skip is not None:
        return f"N/A ({skip})"
    try:
        with pool.get_connection() as conn:
            if db:
                try:
                    conn.select_db(db)
                except Exception:
                    pass  # 库不可切则退回连接默认库，失败会降级为 N/A
            with conn.cursor() as cur:
                cur.execute(explain_sql)
                rows = list(cur.fetchall())
    except Exception as e:
        return "N/A (权限不足)" if _is_perm_error(str(e)) else f"N/A ({str(e)[:120]})"
    parts = []
    for r in rows:
        d = r if isinstance(r, dict) else dict(enumerate(r))
        pairs = [f"{k}={v}" for k, v in d.items() if v is not None and str(v) != "NULL"]
        if pairs:
            parts.append(" | ".join(pairs))
    return "; ".join(parts) if parts else "N/A (无输出)"


def get_table_stats(pool, db: str, table: str) -> str:
    err, rows = _q(pool,
        "SELECT TABLE_ROWS, ROUND(DATA_LENGTH/1024/1024,2) dm, "
        "ROUND(INDEX_LENGTH/1024/1024,2) im, "
        "ROUND((DATA_LENGTH+INDEX_LENGTH)/1024/1024,2) tm, ENGINE "
        "FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
        (db, table))
    if err or not rows:
        return "N/A (权限不足)" if err and _is_perm_error(err) else (err or "N/A")
    r = rows[0]
    return (f"行数(估算)={r.get('TABLE_ROWS')} | 数据={r.get('dm')}MB | "
            f"索引={r.get('im')}MB | 总={r.get('tm')}MB | 引擎={r.get('ENGINE')}")


def get_table_schema(pool, db: str, table: str) -> str:
    err, rows = _q(pool, f"SHOW CREATE TABLE `{db}`.`{table}`")
    if err or not rows:
        return "N/A (权限不足)" if err and _is_perm_error(err) else (err or "N/A")
    r = rows[0]
    for k, v in r.items():
        if k.lower().startswith("create"):
            return v
    return "N/A"


def get_index_details(pool, db: str, table: str) -> str:
    err, rows = _q(pool,
        "SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX, NON_UNIQUE, CARDINALITY, INDEX_TYPE "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
        "ORDER BY INDEX_NAME, SEQ_IN_INDEX", (db, table))
    if err or rows is None:
        return "N/A (权限不足)" if err and _is_perm_error(err) else (err or "N/A")
    idx = defaultdict(lambda: {"cols": [], "unique": True, "card": 0, "type": ""})
    for r in rows:
        info = idx[r["INDEX_NAME"]]
        info["cols"].append(r["COLUMN_NAME"])
        info["unique"] = (str(r["NON_UNIQUE"]) == "0")
        info["card"] = r["CARDINALITY"]
        info["type"] = r["INDEX_TYPE"]
    if not idx:
        return "N/A (无索引)"
    return "; ".join(
        f"{name}({','.join(i['cols'])})[{'UNIQUE' if i['unique'] else 'NONUNIQUE'}] "
        f"CARDINALITY={i['card']} TYPE={i['type']}"
        for name, i in idx.items())


def get_redundant_indexes(pool, db: str, table: str) -> str:
    err, rows = _q(pool,
        "SELECT redundant_index_name, redundant_index_columns, "
        "dominant_index_name, dominant_index_columns FROM sys.schema_redundant_indexes "
        "WHERE table_schema=%s AND table_name=%s", (db, table))
    if err:
        return "N/A (sys库不可用/权限不足)" if _is_perm_error(err) or "doesn't exist" in err.lower() or "sys" in err.lower() else err
    if not rows:
        return "无冗余索引"
    return "; ".join(f"⚠️ {r['redundant_index_name']}({r['redundant_index_columns']}) → "
                     f"被 {r['dominant_index_name']}({r['dominant_index_columns']}) 包含" for r in rows)


def get_stats_update_info(pool, db: str, table: str) -> tuple:
    """返回 (stats_update_str, expire_msg)。
    优先 mysql.innodb_table_stats（/*sets:allsets*/），无权限则回退 TABLES.UPDATE_TIME。"""
    stats_time = ""
    # 1) innodb_table_stats（分布式加 hint；多行取最早）——权限/hint 不支持则跳过
    err, rows = _q(pool,
        "/*sets:allsets*/SELECT last_update FROM mysql.innodb_table_stats "
        "WHERE database_name=%s AND table_name=%s", (db, table))
    if not err and rows:
        earliest = None
        for r in rows:
            t = str(r.get("last_update") or "").strip()
            if t and t != "NULL" and (earliest is None or t < earliest):
                earliest = t
        stats_time = earliest or ""
    # 2) 回退：information_schema.TABLES.UPDATE_TIME / CREATE_TIME（只读账号必可读）
    data_time = ""
    err2, rows2 = _q(pool,
        "SELECT UPDATE_TIME, CREATE_TIME FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (db, table))
    if not err2 and rows2:
        r = rows2[0]
        data_time = str(r.get("UPDATE_TIME") or r.get("CREATE_TIME") or "").strip()
    if not stats_time:
        stats_time = data_time  # 无 innodb 统计时，用数据修改时间近似
    # 判定过期
    if stats_time and stats_time != "NULL":
        try:
            dt = datetime.strptime(stats_time[:19], "%Y-%m-%d %H:%M:%S")
            days = (datetime.now() - dt).days
            if days > STATS_EXPIRE_DAYS:
                msg = f"⚠️ 统计信息已 {days} 天未更新(>{STATS_EXPIRE_DAYS}天)，建议 ANALYZE TABLE `{db}`.`{table}`"
            else:
                msg = f"正常({days}天前更新)"
        except (ValueError, TypeError):
            msg = "正常"
    else:
        msg = "N/A (无统计信息记录)"
    return (f"统计更新={stats_time or 'N/A'} | 数据修改={data_time or 'N/A'}", msg)


# ── 增强主逻辑 ──────────────────────────────────────────────────────

_NEW_KEYS = ["explain_plan", "explain_issues", "involved_tables", "table_stats",
             "table_schema_ddl", "index_details", "redundant_indexes",
             "stats_update_info", "stats_expired", "scan_efficiency"]


def enrich_rows(pool, rows: list, db_default: str = "") -> list:
    """为每行慢SQL追加十项诊断（就地写入 row 的 _NEW_KEYS）。

    - rows：get_cluster_slow_queries/_query_digest_* 产出的 dict 列表
    - 表信息按 (db,table) 去重缓存，避免重复查询
    - 任一子项失败不影响其它项与主流程（返回 N/A）
    """
    def _sql_of(r):
        return (r.get("example_sql") or r.get("DIGEST_TEXT") or r.get("sql_text") or "").strip()

    def _db_of(r):
        raw = (r.get("SCHEMA_NAME") or r.get("db_name") or r.get("db") or db_default or "")
        return str(raw).split(",")[0].strip() if raw else db_default

    # 预收集 (db, table)
    pairs = set()
    for r in rows:
        sql_text = _sql_of(r)
        db = _db_of(r)
        if sql_text and db:
            for t in extract_tables_from_sql(sql_text):
                pairs.add((db, t))
    cache = {}
    for (db, t) in sorted(pairs):
        cache[(db, t)] = {
            "stats": get_table_stats(pool, db, t),
            "schema": get_table_schema(pool, db, t),
            "index": get_index_details(pool, db, t),
            "redundant": get_redundant_indexes(pool, db, t),
            "statsupd": get_stats_update_info(pool, db, t),
        }

    for r in rows:
        sql_text = _sql_of(r)
        db = _db_of(r)
        tables = extract_tables_from_sql(sql_text) if sql_text else []
        explain = get_explain(pool, db, sql_text) if (sql_text and db) else "N/A (空SQL或未知库)"
        r["explain_plan"] = explain[:3000]
        r["explain_issues"] = extract_explain_issues(explain)[:1000]
        r["involved_tables"] = ",".join(tables)
        stats_parts, schema_parts, idx_parts, red_parts, upd_parts, exp_parts = [], [], [], [], [], []
        for t in tables:
            info = cache.get((db, t), {})
            stats_parts.append(f"{t}: {info.get('stats', 'N/A')}")
            schema_parts.append(f"-- {t} --\n{info.get('schema', 'N/A')}")
            idx_parts.append(f"[{t}] {info.get('index', 'N/A')}")
            red = info.get("redundant", "")
            if red and red != "无冗余索引":
                red_parts.append(f"[{t}] {red}")
            su = info.get("statsupd", ("N/A", "N/A"))
            upd_parts.append(f"[{t}] {su[0]}")
            exp_parts.append(f"[{t}] {su[1]}")
        r["table_stats"] = (" || ".join(stats_parts) or "N/A")[:2000]
        r["table_schema_ddl"] = ("\n\n".join(schema_parts) or "N/A")[:5000]
        r["index_details"] = (" || ".join(idx_parts) or "N/A")[:3000]
        r["redundant_indexes"] = (" || ".join(red_parts) or "无冗余索引")[:1000]
        r["stats_update_info"] = (" || ".join(upd_parts) or "N/A")[:2000]
        r["stats_expired"] = (" || ".join(exp_parts) or "N/A")[:2000]
        r["scan_efficiency"] = calc_scan_efficiency(r.get("rows_examined"), r.get("rows_sent"))
    return rows
