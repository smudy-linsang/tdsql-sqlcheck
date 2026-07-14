"""M4 · G9 大表增长趋势（源自原厂 count_table_rows/collect_table_stats）

每日快照大表的行数/大小到 bigtable_history，按 (db,table) 出增长趋势与环比。
与现有大表治理(bigtable_service)配合：采集用现有大表清单，本模块做历史留存与趋势。
"""
import datetime as _dt
import logging

from backend.services.database import _get_connection

logger = logging.getLogger("tdsql.bigtable_trend")


def snapshot(pool, connection_id: str = "", database: str = "",
             threshold_gb: float = 1.0, snap_date: str = "") -> dict:
    """快照当前大表大小并落 bigtable_history（幂等按日）。"""
    if not snap_date:
        snap_date = _dt.date.today().strftime("%Y-%m-%d")
    tables = pool.check_large_tables(database or None, threshold_gb)
    conn = _get_connection()
    n = 0
    try:
        for t in tables:
            db = t.get("table_schema") or t.get("TABLE_SCHEMA") or t.get("db_name") or database or ""
            name = t.get("table_name") or t.get("TABLE_NAME") or ""
            rows = int(t.get("rows_count") or t.get("TABLE_ROWS") or 0)
            size_gb = float(t.get("size_gb") or t.get("total_gb") or 0)
            if not name:
                continue
            conn.execute(
                "INSERT INTO bigtable_history (snap_date, connection_id, db_name, table_name, "
                "table_rows, size_gb) VALUES (?,?,?,?,?,?) "
                "ON DUPLICATE KEY UPDATE table_rows=VALUES(table_rows), size_gb=VALUES(size_gb)",
                (snap_date, connection_id, db, name, rows, size_gb))
            n += 1
        conn.commit()
    finally:
        conn.close()
    return {"snap_date": snap_date, "connection_id": connection_id, "snapshotted": n}


def get_growth(connection_id: str = "", db_name: str = "", table_name: str = "",
               date_from: str = "", date_to: str = "") -> dict:
    """按 (db,table) 出历史序列 + 环比增长。"""
    conn = _get_connection()
    try:
        sql = ("SELECT snap_date, db_name, table_name, table_rows, size_gb "
               "FROM bigtable_history WHERE 1=1")
        params = []
        for col, val in (("connection_id", connection_id), ("db_name", db_name),
                         ("table_name", table_name)):
            if val:
                sql += f" AND {col}=?"
                params.append(val)
        if date_from:
            sql += " AND snap_date>=?"
            params.append(date_from)
        if date_to:
            sql += " AND snap_date<=?"
            params.append(date_to)
        sql += " ORDER BY db_name, table_name, snap_date"
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        conn.close()
    # 按表分组算环比
    series = {}
    for r in rows:
        key = f"{r['db_name']}.{r['table_name']}"
        series.setdefault(key, []).append(r)
    growth = []
    for key, pts in series.items():
        if len(pts) >= 2:
            first, last = pts[0], pts[-1]
            delta_gb = round(float(last["size_gb"]) - float(first["size_gb"]), 3)
            growth.append({"table": key, "from_date": first["snap_date"],
                           "to_date": last["snap_date"],
                           "from_gb": first["size_gb"], "to_gb": last["size_gb"],
                           "delta_gb": delta_gb})
    growth.sort(key=lambda x: x["delta_gb"], reverse=True)
    return {"series": series, "growth_ranking": growth}
