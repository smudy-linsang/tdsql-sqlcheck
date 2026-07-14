"""M2 · G3 集群深度巡检服务（源自原厂 tdsql-deep-inspection）

读 monitordb 的 KV 指标表 m_data_cur（f_mid/f_pmid/f_key/f_val/f_type），
对每个监控对象按阈值判级，经 severity_map 归一为 ERROR/WARNING/INFO，
落 cluster_inspection(_issue) 表，供报告/看板追溯。

阈值照搬原厂 tdsql_inspect.py 的 T 表（w=WARNING线, c=CRITICAL线）。
"""
import json
import logging

from backend.engine.severity_map import map_severity
from backend.services.database import _get_connection

logger = logging.getLogger("tdsql.cluster_inspect")

# 阈值（可被 DB 配置覆盖）——照搬原厂
INSPECT_THRESHOLDS = {
    "cpu_usage": (70, 90, "high"),          # DB CPU 使用率
    "mysql_max_mem_usage": (120, 150, "high"),  # 内存使用率
    "connect_usage": (70, 85, "high"),      # 连接使用率
    "data_dir_usage": (75, 90, "high"),     # 数据盘使用率
    "slave_delay": (5, 30, "high"),         # 主备延迟(s)
    "slow_query": (1000, 10000, "high"),    # 慢查询数
    "table_hit_rate": (95, 90, "low"),      # 缓冲池命中率(低于告警)
}
# 指标 → (类别, 中文名)
_META = {
    "cpu_usage": ("performance", "DB CPU使用率(%)"),
    "mysql_max_mem_usage": ("performance", "内存使用率(%)"),
    "connect_usage": ("performance", "连接使用率(%)"),
    "data_dir_usage": ("performance", "数据盘使用率(%)"),
    "slave_delay": ("reliability", "主备延迟(s)"),
    "slow_query": ("performance", "慢查询数"),
    "table_hit_rate": ("performance", "缓冲池命中率(%)"),
}
# >0 即告警（可维护性/可靠性）
NONZERO_CHECKS = {
    "no_primary_key_table_nums": ("maintainability", "无主键表数量", "WARNING"),
    "myisam_table_nums": ("maintainability", "非InnoDB(MyISAM)表数量", "WARNING"),
    "binlog_error": ("reliability", "binlog错误", "HIGH"),
}


def _metric(pool, mid: str, f_key: str):
    """读某监控对象的当前指标值（f_type=1）。查不到返回 None。"""
    try:
        rows = pool._monitor_execute(
            "SELECT f_val FROM m_data_cur "
            "WHERE (f_mid LIKE %s OR f_pmid LIKE %s) AND f_key=%s AND f_type=1 "
            "ORDER BY f_val DESC LIMIT 1",
            (f"%{mid}%", f"%{mid}%", f_key))
        if rows and rows[0].get("f_val") is not None:
            return float(rows[0]["f_val"])
    except Exception as e:
        logger.debug("metric read failed mid=%s key=%s: %s", mid, f_key, e)
    return None


def _discover_nodes(pool) -> list:
    """从 m_data_cur 发现携带健康指标的监控对象（f_mid）。"""
    try:
        rows = pool._monitor_execute(
            "SELECT DISTINCT f_mid FROM m_data_cur "
            "WHERE f_key IN ('cpu_usage','alive','connect_usage') AND f_mid IS NOT NULL "
            "LIMIT 500")
        return [r["f_mid"] for r in rows if r.get("f_mid")]
    except Exception as e:
        logger.warning("discover nodes failed: %s", e)
        return []


def _judge(f_key, val):
    """按阈值判级，返回 (vendor_level or None, threshold_desc)。"""
    warn, crit, direction = INSPECT_THRESHOLDS[f_key]
    if direction == "high":
        if val >= crit:
            return "CRITICAL", f">={crit}"
        if val >= warn:
            return "WARNING", f">={warn}"
    else:  # low：低于阈值为坏
        if val <= crit:
            return "CRITICAL", f"<={crit}"
        if val <= warn:
            return "WARNING", f"<={warn}"
    return None, f"{warn}/{crit}"


def run_inspection(pool, connection_id: str = "", operator: str = "",
                   nodes: list = None) -> dict:
    """执行一次集群深度巡检。pool 为业务连接池（内含 monitordb 接入）。"""
    probe = pool.monitor_probe() if hasattr(pool, "monitor_probe") else {"ok": True}
    # 集群名
    cluster_name = ""
    try:
        rows = pool._monitor_execute(
            "SELECT cluster_name FROM tdsqlpcloud.t_cluster WHERE cluster_id=1 LIMIT 1")
        if rows:
            cluster_name = rows[0].get("cluster_name", "") or ""
    except Exception:
        pass

    node_list = nodes if nodes else _discover_nodes(pool)
    issues = []

    def add(category, level, node, title, detail, value="", threshold=""):
        issues.append({
            "category": category, "severity": map_severity(level), "node": node,
            "title": title, "detail": detail,
            "metric_value": str(value), "threshold": str(threshold),
        })

    for mid in node_list:
        # 存活
        alive = _metric(pool, mid, "alive")
        if alive is not None and alive == 0:
            add("availability", "FATAL", mid, "节点不可用", f"节点 {mid} alive=0", 0, "=1")
        # 阈值型
        for f_key, (warn, crit, direction) in INSPECT_THRESHOLDS.items():
            val = _metric(pool, mid, f_key)
            if val is None:
                continue
            level, thr = _judge(f_key, val)
            cat, cname = _META[f_key]
            if level:
                add(cat, level, mid, f"{cname}超阈值",
                    f"节点 {mid} {cname}={val:g}（阈值 {thr}）", f"{val:g}", thr)
        # >0 告警型
        for f_key, (cat, cname, level) in NONZERO_CHECKS.items():
            val = _metric(pool, mid, f_key)
            if val is not None and val > 0:
                add(cat, level, mid, cname, f"节点 {mid} {cname}={val:g}", f"{val:g}", ">0")

    err = sum(1 for i in issues if i["severity"] == "ERROR")
    warn = sum(1 for i in issues if i["severity"] == "WARNING")
    info = sum(1 for i in issues if i["severity"] == "INFO")

    # 落库
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO cluster_inspection (connection_id, cluster_name, inspect_date, "
            "total_issues, error_count, warning_count, info_count, node_count, summary_json, created_by) "
            "VALUES (?,?,NOW(),?,?,?,?,?,?,?)",
            (connection_id, cluster_name, len(issues), err, warn, info, len(node_list),
             json.dumps({"nodes": node_list}, ensure_ascii=False), operator))
        inspection_id = cur.lastrowid
        for it in issues:
            conn.execute(
                "INSERT INTO cluster_inspection_issue (inspection_id, category, severity, node, "
                "title, detail, metric_value, threshold) VALUES (?,?,?,?,?,?,?,?)",
                (inspection_id, it["category"], it["severity"], it["node"], it["title"],
                 it["detail"], it["metric_value"], it["threshold"]))
        conn.commit()
    finally:
        conn.close()

    return {
        "inspection_id": inspection_id, "cluster_name": cluster_name,
        "node_count": len(node_list), "total_issues": len(issues),
        "error_count": err, "warning_count": warn, "info_count": info,
        "issues": issues,
    }


def list_inspections(connection_id: str = "", limit: int = 50) -> list:
    conn = _get_connection()
    try:
        if connection_id:
            rows = conn.execute(
                "SELECT * FROM cluster_inspection WHERE connection_id=? ORDER BY id DESC LIMIT ?",
                (connection_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cluster_inspection ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_issues(inspection_id: int, severity: str = "") -> list:
    conn = _get_connection()
    try:
        if severity:
            rows = conn.execute(
                "SELECT * FROM cluster_inspection_issue WHERE inspection_id=? AND severity=? "
                "ORDER BY FIELD(severity,'ERROR','WARNING','INFO'), id",
                (inspection_id, severity)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cluster_inspection_issue WHERE inspection_id=? "
                "ORDER BY FIELD(severity,'ERROR','WARNING','INFO'), id",
                (inspection_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
