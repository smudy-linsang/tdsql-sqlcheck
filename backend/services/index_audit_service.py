"""M3 · G5 实例级索引健康审计（源自原厂 index_analysis）

对业务实例的库表做索引体检：区分度/重复/前缀冗余/单表索引过多/表碎片/
自增耗尽（未使用/低利用率依赖 performance_schema，不可用时跳过）。
全部只读 information_schema；比率计算防除零（评审整改 §0.4-4）。
"""
import logging

from backend.services.database import _get_connection

logger = logging.getLogger("tdsql.index_audit")

# 阈值（照搬原厂）
SELECTIVITY_POOR = 0.1        # 区分度 < 0.1 报低区分度
MAX_INDEXES_PER_TABLE = 8     # 单表索引过多
MIN_FRAG_MB = 1               # 碎片 >= 1MB 才报
FRAG_RATIO_ERROR = 0.5        # 碎片率 >= 50% 升为 ERROR
AUTOINC_WARN_PCT = 40         # 自增使用率 >= 40% 告警
AUTOINC_ERROR_PCT = 90        # >= 90% 升为 ERROR

_SYS = ("mysql", "information_schema", "performance_schema", "sys",
        "tdsqlpcloud", "tdsqlpcloud_monitor", "__tencentdb__")

_INT_MAX = {
    "tinyint": (127, 255), "smallint": (32767, 65535),
    "mediumint": (8388607, 16777215), "int": (2147483647, 4294967295),
    "integer": (2147483647, 4294967295),
    "bigint": (9223372036854775807, 18446744073709551615),
}


def _sys_filter(alias="TABLE_SCHEMA"):
    return " AND ".join(f"{alias} <> '{s}'" for s in _SYS)


def _collect(pool, database):
    dbf = f" AND TABLE_SCHEMA = '{database}'" if database else ""
    tables = pool._execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH, "
        "DATA_FREE, AUTO_INCREMENT, ENGINE FROM information_schema.TABLES "
        f"WHERE TABLE_TYPE='BASE TABLE' AND {_sys_filter()}{dbf}")
    stats = pool._execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, "
        "NON_UNIQUE, CARDINALITY FROM information_schema.STATISTICS "
        f"WHERE {_sys_filter()}{dbf} ORDER BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX")
    autoinc = pool._execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE "
        "FROM information_schema.COLUMNS WHERE EXTRA LIKE '%auto_increment%'"
        f" AND {_sys_filter()}{dbf}")
    return tables, stats, autoinc


def _build_indexes(stats):
    """→ {(db,table): {index_name: {'cols':[..], 'unique':bool, 'card':int}}}"""
    out = {}
    for r in stats:
        key = (r["TABLE_SCHEMA"], r["TABLE_NAME"])
        idxs = out.setdefault(key, {})
        info = idxs.setdefault(r["INDEX_NAME"], {"cols": [], "unique": None, "card": 0})
        info["cols"].append(r["COLUMN_NAME"])
        info["unique"] = (str(r["NON_UNIQUE"]) == "0")
        try:
            info["card"] = max(info["card"], int(r["CARDINALITY"] or 0))
        except (ValueError, TypeError):
            pass
    return out


def analyze(pool, database: str = "") -> dict:
    """执行索引体检，返回 {tables,indexes,findings:[...]}（不落库）。"""
    tables, stats, autoinc = _collect(pool, database)
    idx_map = _build_indexes(stats)
    rows_map = {(t["TABLE_SCHEMA"], t["TABLE_NAME"]): t for t in tables}
    findings = []

    def add(db, table, index, ftype, severity, detail, suggestion, metric=""):
        findings.append({"db_name": db, "table_name": table, "index_name": index,
                         "finding_type": ftype, "severity": severity, "detail": detail,
                         "suggestion": suggestion, "metric": str(metric)})

    total_indexes = 0
    for (db, table), idxs in idx_map.items():
        total_indexes += len(idxs)
        trow = rows_map.get((db, table), {})
        table_rows = int(trow.get("TABLE_ROWS") or 0)

        # 单表索引过多
        if len(idxs) > MAX_INDEXES_PER_TABLE:
            add(db, table, "", "单表索引过多", "WARNING",
                f"表 {db}.{table} 有 {len(idxs)} 个索引(>{MAX_INDEXES_PER_TABLE})",
                "评估合并/删除低价值索引", len(idxs))

        # 区分度 + 重复/前缀冗余
        names = list(idxs.keys())
        for name, info in idxs.items():
            if name.upper() != "PRIMARY":
                # 区分度（防除零）
                sel = (info["card"] / table_rows) if table_rows > 0 else 0.0
                if table_rows > 0 and sel < SELECTIVITY_POOR:
                    add(db, table, name, "低区分度索引", "WARNING",
                        f"索引 {name}({','.join(info['cols'])}) 区分度 {sel:.4f}(<{SELECTIVITY_POOR})，"
                        f"cardinality={info['card']}/行数={table_rows}",
                        "评估是否需要此索引/优化器可能不走它", f"{sel:.4f}")
        # 重复 & 前缀冗余（两两比较列序列）
        for i in range(len(names)):
            for j in range(len(names)):
                if i == j:
                    continue
                a, b = idxs[names[i]]["cols"], idxs[names[j]]["cols"]
                if names[i].upper() == "PRIMARY":
                    continue
                if a == b and i < j:
                    add(db, table, names[i], "重复索引", "WARNING",
                        f"索引 {names[i]} 与 {names[j]} 列完全相同({','.join(a)})",
                        f"删除其一（保留唯一性更强者）", "")
                elif len(a) < len(b) and b[:len(a)] == a:
                    add(db, table, names[i], "前缀冗余索引", "WARNING",
                        f"索引 {names[i]}({','.join(a)}) 是 {names[j]}({','.join(b)}) 的前缀",
                        f"可由 {names[j]} 覆盖，评估删除 {names[i]}", "")

        # 表碎片
        try:
            data_free = int(trow.get("DATA_FREE") or 0)
            total_size = int(trow.get("DATA_LENGTH") or 0) + int(trow.get("INDEX_LENGTH") or 0) + data_free
            frag_mb = data_free / 1024 / 1024
            frag_ratio = (data_free / total_size) if total_size > 0 else 0.0
            if frag_mb >= MIN_FRAG_MB:
                sev = "ERROR" if frag_ratio >= FRAG_RATIO_ERROR else "WARNING"
                add(db, table, "", "表碎片", sev,
                    f"表 {db}.{table} 碎片 {frag_mb:.1f}MB，碎片率 {frag_ratio*100:.1f}%",
                    f"OPTIMIZE TABLE `{db}`.`{table}` 回收碎片", f"{frag_ratio*100:.1f}%")
        except (ValueError, TypeError):
            pass

    # 自增耗尽
    ai_next = {(t["TABLE_SCHEMA"], t["TABLE_NAME"]): t.get("AUTO_INCREMENT") for t in tables}
    for c in autoinc:
        key = (c["TABLE_SCHEMA"], c["TABLE_NAME"])
        nxt = ai_next.get(key)
        if not nxt:
            continue
        col_type = (c["COLUMN_TYPE"] or "").lower()
        base = col_type.split("(")[0].strip()
        unsigned = "unsigned" in col_type
        maxv = _INT_MAX.get(base, (None, None))[1 if unsigned else 0]
        if not maxv:
            continue
        pct = int(nxt) / maxv * 100
        if pct >= AUTOINC_WARN_PCT:
            sev = "ERROR" if pct >= AUTOINC_ERROR_PCT else "WARNING"
            add(c["TABLE_SCHEMA"], c["TABLE_NAME"], c["COLUMN_NAME"], "自增耗尽风险", sev,
                f"自增列 {c['COLUMN_NAME']}({base}{'unsigned' if unsigned else ''}) "
                f"已用 {pct:.1f}%（{nxt}/{maxv}）",
                "评估改大整型或归档历史数据", f"{pct:.1f}%")

    # 未使用/低利用率（performance_schema，可能不可用）
    try:
        pf = pool._execute(
            "SELECT OBJECT_SCHEMA, OBJECT_NAME, INDEX_NAME, COUNT_READ "
            "FROM performance_schema.table_io_waits_summary_by_index_usage "
            "WHERE INDEX_NAME IS NOT NULL AND OBJECT_SCHEMA NOT IN "
            "('mysql','sys','performance_schema','information_schema')"
            + (f" AND OBJECT_SCHEMA='{database}'" if database else "") + " LIMIT 5000")
        for r in pf:
            if r.get("INDEX_NAME") and str(r.get("INDEX_NAME")).upper() != "PRIMARY" \
                    and int(r.get("COUNT_READ") or 0) == 0:
                add(r["OBJECT_SCHEMA"], r["OBJECT_NAME"], r["INDEX_NAME"], "未使用索引", "INFO",
                    f"索引 {r['INDEX_NAME']} 自监控以来读取次数为0", "确认后评估删除(注意运行时长)", "0")
    except Exception:
        findings.append({"db_name": database or "", "table_name": "", "index_name": "",
                         "finding_type": "未使用索引检测", "severity": "INFO",
                         "detail": "performance_schema 不可用或无权限，跳过未使用/低利用率检测",
                         "suggestion": "开启 performance_schema instrument 后重试", "metric": ""})

    return {"tables": len(rows_map), "indexes": total_indexes, "findings": findings}


def run_audit(pool, connection_id: str = "", database: str = "", operator: str = "") -> dict:
    res = analyze(pool, database)
    findings = res["findings"]
    err = sum(1 for f in findings if f["severity"] == "ERROR")
    warn = sum(1 for f in findings if f["severity"] == "WARNING")
    info = sum(1 for f in findings if f["severity"] == "INFO")
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO index_audit (connection_id, database_filter, total_tables, "
            "total_indexes, total_findings, error_count, warning_count, info_count, created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (connection_id, database, res["tables"], res["indexes"], len(findings),
             err, warn, info, operator))
        audit_id = cur.lastrowid
        for f in findings:
            conn.execute(
                "INSERT INTO index_audit_finding (audit_id, db_name, table_name, index_name, "
                "finding_type, severity, detail, suggestion, metric) VALUES (?,?,?,?,?,?,?,?,?)",
                (audit_id, f["db_name"], f["table_name"], f["index_name"], f["finding_type"],
                 f["severity"], f["detail"], f["suggestion"], f["metric"]))
        conn.commit()
    finally:
        conn.close()
    return {"audit_id": audit_id, "total_tables": res["tables"], "total_indexes": res["indexes"],
            "total_findings": len(findings), "error_count": err, "warning_count": warn,
            "info_count": info, "findings": findings}


def get_findings(audit_id: int, severity: str = "") -> list:
    conn = _get_connection()
    try:
        if severity:
            rows = conn.execute(
                "SELECT * FROM index_audit_finding WHERE audit_id=? AND severity=? "
                "ORDER BY FIELD(severity,'ERROR','WARNING','INFO'), id", (audit_id, severity)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM index_audit_finding WHERE audit_id=? "
                "ORDER BY FIELD(severity,'ERROR','WARNING','INFO'), id", (audit_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
