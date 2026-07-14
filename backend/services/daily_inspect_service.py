"""M3 · G4 每日巡检 + 多日趋势（源自原厂 daily_inspection + compare_reports）

每日从 monitordb m_data_cur 采集关键指标，落 daily_inspection 表；
trend 查询按日期区间聚合，供趋势看板/多日对比。
"""
import datetime as _dt
import logging

from backend.services.database import _get_connection
from backend.services.cluster_inspect_service import _metric, _discover_nodes

logger = logging.getLogger("tdsql.daily_inspect")

# 7 项指标 → daily_inspection 列
_METRICS = {
    "cpu_peak": "cpu_usage_max",
    "cpu_avg": "cpu_usage",
    "mem_peak": "mysql_max_mem_usage",
    "conn_peak": "connect_usage",
    "slow_query": "slow_query",
    "delay_peak": "slave_delay",
    "disk_peak": "data_dir_usage",
}
TREND_METRICS = list(_METRICS.keys())


def run_daily(pool, connection_id: str = "", inspect_date: str = "", nodes: list = None) -> dict:
    """采集某日各监控对象的关键指标并 upsert 落库。date 默认今天。"""
    if not inspect_date:
        inspect_date = _dt.date.today().strftime("%Y-%m-%d")
    node_list = nodes if nodes else _discover_nodes(pool)
    rows = []
    conn = _get_connection()
    try:
        for mid in node_list:
            vals = {col: (_metric(pool, mid, key) or 0.0) for col, key in _METRICS.items()}
            conn.execute(
                "INSERT INTO daily_inspection (inspect_date, connection_id, node, "
                "cpu_peak, cpu_avg, mem_peak, conn_peak, slow_query, delay_peak, disk_peak) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON DUPLICATE KEY UPDATE cpu_peak=VALUES(cpu_peak), cpu_avg=VALUES(cpu_avg), "
                "mem_peak=VALUES(mem_peak), conn_peak=VALUES(conn_peak), "
                "slow_query=VALUES(slow_query), delay_peak=VALUES(delay_peak), disk_peak=VALUES(disk_peak)",
                (inspect_date, connection_id, mid, vals["cpu_peak"], vals["cpu_avg"],
                 vals["mem_peak"], vals["conn_peak"], vals["slow_query"],
                 vals["delay_peak"], vals["disk_peak"]))
            rows.append({"node": mid, **vals})
        conn.commit()
    finally:
        conn.close()
    return {"inspect_date": inspect_date, "connection_id": connection_id,
            "node_count": len(node_list), "rows": rows}


def get_trend(connection_id: str = "", date_from: str = "", date_to: str = "",
              metrics: list = None) -> dict:
    """按日期区间取趋势序列：{metric: [{date, node, value}...]}。"""
    metrics = [m for m in (metrics or TREND_METRICS) if m in _METRICS]
    cols = ", ".join(metrics)
    conn = _get_connection()
    try:
        sql = f"SELECT inspect_date, node, {cols} FROM daily_inspection WHERE 1=1"
        params = []
        if connection_id:
            sql += " AND connection_id=?"
            params.append(connection_id)
        if date_from:
            sql += " AND inspect_date>=?"
            params.append(date_from)
        if date_to:
            sql += " AND inspect_date<=?"
            params.append(date_to)
        sql += " ORDER BY inspect_date, node"
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()
    series = {m: [] for m in metrics}
    for r in rows:
        d = dict(r)
        for m in metrics:
            series[m].append({"date": d["inspect_date"], "node": d["node"], "value": d[m]})
    return {"metrics": metrics, "days": len(set(dict(r)["inspect_date"] for r in rows)),
            "series": series}
