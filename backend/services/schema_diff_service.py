"""M3 · G6 跨实例表结构比对（源自原厂 table_schema_diff）

比对两个实例的库表结构（表/列/索引），按原厂严重度分级后经 severity_map 归一：
  表缺失 / 索引缺失            → CRITICAL → ERROR
  列缺失                       → HIGH     → ERROR
  列类型不一致 / 同名索引列不一致 → MEDIUM   → WARNING
  多余列 / 多余索引            → INFO     → INFO
以 left 为基准(如生产)，right 为对比(如测试)。列名大小写不敏感。
"""
import logging

from backend.engine.severity_map import map_severity
from backend.services.database import _get_connection

logger = logging.getLogger("tdsql.schema_diff")

_SYS = ("mysql", "information_schema", "performance_schema", "sys",
        "tdsqlpcloud", "tdsqlpcloud_monitor", "__tencentdb__")


def collect_structure(pool, databases=None) -> dict:
    """→ {db: {table: {'columns': {col_lower:(col,type)}, 'indexes': {idx:[cols]}}}}"""
    dbs = [d.strip() for d in (databases or []) if d.strip() and d.strip().upper() != "ALL"]
    if dbs:
        inlist = ",".join("'" + d.replace("'", "") + "'" for d in dbs)
        where = f"TABLE_SCHEMA IN ({inlist})"
    else:
        where = " AND ".join(f"TABLE_SCHEMA <> '{s}'" for s in _SYS)
    cols = pool._execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE "
        f"FROM information_schema.COLUMNS WHERE {where}")
    idxs = pool._execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME "
        f"FROM information_schema.STATISTICS WHERE {where} "
        "ORDER BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX")
    struct = {}
    for c in cols:
        t = struct.setdefault(c["TABLE_SCHEMA"], {}).setdefault(
            c["TABLE_NAME"], {"columns": {}, "indexes": {}})
        t["columns"][c["COLUMN_NAME"].lower()] = (c["COLUMN_NAME"], (c["COLUMN_TYPE"] or "").lower())
    for r in idxs:
        db = struct.setdefault(r["TABLE_SCHEMA"], {})
        t = db.setdefault(r["TABLE_NAME"], {"columns": {}, "indexes": {}})
        t["indexes"].setdefault(r["INDEX_NAME"], []).append((r["COLUMN_NAME"] or "").lower())
    return struct


def diff_structures(left: dict, right: dict) -> list:
    """比对两侧结构，返回 diff item 列表（severity 已归一）。"""
    items = []

    def add(db, table, obj, dtype, vendor_sev, lv="", rv=""):
        items.append({"db_name": db, "table_name": table, "object_name": obj,
                      "diff_type": dtype, "severity": map_severity(vendor_sev),
                      "left_value": str(lv), "right_value": str(rv)})

    all_dbs = set(left) | set(right)
    for db in sorted(all_dbs):
        lt = left.get(db, {})
        rt = right.get(db, {})
        for table in sorted(set(lt) | set(rt)):
            if table not in rt:
                add(db, table, "", "表缺失(右侧缺)", "CRITICAL", "存在", "缺失")
                continue
            if table not in lt:
                add(db, table, "", "表多余(右侧多)", "INFO", "缺失", "存在")
                continue
            lc, rc = lt[table]["columns"], rt[table]["columns"]
            for col in sorted(set(lc) | set(rc)):
                if col not in rc:
                    add(db, table, lc[col][0], "列缺失(右侧缺)", "HIGH", lc[col][1], "缺失")
                elif col not in lc:
                    add(db, table, rc[col][0], "列多余(右侧多)", "INFO", "缺失", rc[col][1])
                elif lc[col][1] != rc[col][1]:
                    add(db, table, lc[col][0], "列类型不一致", "MEDIUM", lc[col][1], rc[col][1])
            li, ri = lt[table]["indexes"], rt[table]["indexes"]
            for idx in sorted(set(li) | set(ri)):
                if idx not in ri:
                    add(db, table, idx, "索引缺失(右侧缺)", "CRITICAL", ",".join(li[idx]), "缺失")
                elif idx not in li:
                    add(db, table, idx, "索引多余(右侧多)", "INFO", "缺失", ",".join(ri[idx]))
                elif li[idx] != ri[idx]:
                    add(db, table, idx, "同名索引列不一致", "MEDIUM", ",".join(li[idx]), ",".join(ri[idx]))
    return items


def run_diff(left_pool, right_pool, databases=None,
             left_conn="", right_conn="", operator="") -> dict:
    left = collect_structure(left_pool, databases)
    right = collect_structure(right_pool, databases)
    items = diff_structures(left, right)
    err = sum(1 for i in items if i["severity"] == "ERROR")
    warn = sum(1 for i in items if i["severity"] == "WARNING")
    info = sum(1 for i in items if i["severity"] == "INFO")
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO schema_diff (left_conn, right_conn, databases_filter, total_items, "
            "error_count, warning_count, info_count, created_by) VALUES (?,?,?,?,?,?,?,?)",
            (left_conn, right_conn, ",".join(databases or []), len(items), err, warn, info, operator))
        diff_id = cur.lastrowid
        for it in items:
            conn.execute(
                "INSERT INTO schema_diff_item (diff_id, db_name, table_name, object_name, "
                "diff_type, severity, left_value, right_value) VALUES (?,?,?,?,?,?,?,?)",
                (diff_id, it["db_name"], it["table_name"], it["object_name"], it["diff_type"],
                 it["severity"], it["left_value"], it["right_value"]))
        conn.commit()
    finally:
        conn.close()
    return {"diff_id": diff_id, "total_items": len(items), "error_count": err,
            "warning_count": warn, "info_count": info, "items": items}


def get_items(diff_id: int, severity: str = "") -> list:
    conn = _get_connection()
    try:
        if severity:
            rows = conn.execute(
                "SELECT * FROM schema_diff_item WHERE diff_id=? AND severity=? "
                "ORDER BY FIELD(severity,'ERROR','WARNING','INFO'), id", (diff_id, severity)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM schema_diff_item WHERE diff_id=? "
                "ORDER BY FIELD(severity,'ERROR','WARNING','INFO'), id", (diff_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
