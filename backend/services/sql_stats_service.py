"""M4 · G8 SQL 调用量与耗时分析（源自原厂 sql_analysis）

复用 monitordb 集群级慢SQL聚合(get_cluster_slow_queries)，产出多维统计视图：
SQL类型分布、TOP-N 高频/耗时/慢/疑似全表扫描。纯分析视图，不新建表。
"""
import logging

logger = logging.getLogger("tdsql.sql_stats")


def _sql_type(fingerprint: str) -> str:
    fp = (fingerprint or "").strip().upper()
    for t in ("SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE"):
        if fp.startswith(t):
            return t
    return "OTHER"


def analyze(pool, time_start=None, time_end=None, top_n=20, database=None) -> dict:
    """从 monitordb 取全量聚合后产出多维 TOP-N。"""
    rows = pool.get_cluster_slow_queries(
        limit=max(top_n * 10, 200), min_time=0,
        time_start=time_start, time_end=time_end, database=database)

    def _f(r, k):
        try:
            return float(r.get(k) or 0)
        except (ValueError, TypeError):
            return 0.0

    def _i(r, k):
        try:
            return int(float(r.get(k) or 0))
        except (ValueError, TypeError):
            return 0

    # SQL 类型分布
    type_dist = {}
    total_exec = 0
    for r in rows:
        t = _sql_type(r.get("DIGEST_TEXT"))
        cnt = _i(r, "exec_count")
        total_exec += cnt
        d = type_dist.setdefault(t, {"sql_classes": 0, "exec_count": 0})
        d["sql_classes"] += 1
        d["exec_count"] += cnt

    def _view(r):
        return {
            "db_name": r.get("SCHEMA_NAME"), "fingerprint": r.get("DIGEST_TEXT"),
            "sql_type": _sql_type(r.get("DIGEST_TEXT")),
            "exec_count": _i(r, "exec_count"),
            "total_seconds": _f(r, "total_seconds"), "avg_seconds": _f(r, "avg_seconds"),
            "max_seconds": _f(r, "max_seconds"),
            "rows_examined": _i(r, "rows_examined"), "rows_sent": _i(r, "rows_sent"),
            "client_user": r.get("client_user"), "set_ids": r.get("set_ids"),
        }

    top_freq = sorted(rows, key=lambda r: _i(r, "exec_count"), reverse=True)[:top_n]
    top_total = sorted(rows, key=lambda r: _f(r, "total_seconds"), reverse=True)[:top_n]
    top_slow = sorted(rows, key=lambda r: _f(r, "avg_seconds"), reverse=True)[:top_n]
    # 疑似全表扫描：扫描行多且效率低(返回/扫描<0.1)
    scan_cands = [r for r in rows
                  if _i(r, "rows_examined") > 1000
                  and (_i(r, "rows_sent") / _i(r, "rows_examined") if _i(r, "rows_examined") else 1) < 0.1]
    top_scan = sorted(scan_cands, key=lambda r: _i(r, "rows_examined"), reverse=True)[:top_n]

    return {
        "sql_class_count": len(rows), "total_exec": total_exec,
        "type_distribution": type_dist,
        "top_frequent": [_view(r) for r in top_freq],
        "top_total_time": [_view(r) for r in top_total],
        "top_slow": [_view(r) for r in top_slow],
        "top_full_scan": [_view(r) for r in top_scan],
    }
