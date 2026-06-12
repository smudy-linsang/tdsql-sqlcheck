"""
TDSQL SQL审核工具 - Dashboard API

提供审核和慢SQL的统计概览数据。
"""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/dashboard", tags=["Dashboard"])

DB_PATH = Path(__file__).parent.parent.parent / "data" / "tdsql_check.db"


def _get_connection() -> sqlite3.Connection:
    """获取数据库连接（WAL模式 + 超时）"""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _db_exists() -> bool:
    return DB_PATH.exists()


@router.get("/summary", summary="获取Dashboard概览数据")
async def get_summary():
    """
    获取审核和慢SQL的统计概览，用于Dashboard首页展示。
    """
    if not _db_exists():
        return {
            "audit": {
                "today_count": 0,
                "today_passed": 0,
                "today_failed": 0,
                "today_pass_rate": 0,
                "total_count": 0,
            },
            "slow_queries": {
                "total": 0,
                "pending": 0,
                "optimized": 0,
                "by_severity": {},
                "top5_time": [],
                "top5_frequency": [],
            },
            "rules": {
                "total": 22,
                "enabled": 22,
                "by_category": {
                    "naming": 2,
                    "ddl": 9,
                    "dml": 8,
                    "distributed": 3,
                },
            },
        }

    conn = _get_connection()
    try:
        today = datetime.now().strftime("%Y-%m-%d")

        # 今日审核统计
        audit_today = conn.execute("""
            SELECT COUNT(*) as cnt,
                   SUM(CASE WHEN failed = 0 THEN 1 ELSE 0 END) as passed,
                   SUM(failed) as failed_count
            FROM audit_history
            WHERE DATE(created_at) = ?
        """, (today,)).fetchone()

        today_count = audit_today["cnt"] or 0
        today_passed = audit_today["passed"] or 0
        today_failed = audit_today["failed_count"] or 0
        today_pass_rate = (today_passed / today_count * 100) if today_count > 0 else 0

        # 历史审核总数
        total_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_history"
        ).fetchone()["cnt"] or 0

        # 慢SQL统计
        slow_total = conn.execute(
            "SELECT COUNT(*) as cnt FROM slow_queries"
        ).fetchone()["cnt"] or 0

        slow_pending = conn.execute(
            "SELECT COUNT(*) as cnt FROM slow_queries WHERE status = 'pending'"
        ).fetchone()["cnt"] or 0

        slow_optimized = conn.execute(
            "SELECT COUNT(*) as cnt FROM slow_queries WHERE status = 'optimized'"
        ).fetchone()["cnt"] or 0

        # 按严重程度统计
        by_severity = {}
        for row in conn.execute(
            "SELECT severity, COUNT(*) as cnt FROM slow_queries GROUP BY severity"
        ).fetchall():
            by_severity[row["severity"]] = row["cnt"]

        # Top5 高耗时
        top5_time = []
        for row in conn.execute(
            "SELECT id, fingerprint, avg_time_ms, exec_count, severity FROM slow_queries ORDER BY avg_time_ms DESC LIMIT 5"
        ).fetchall():
            top5_time.append(dict(row))

        # Top5 高频次
        top5_freq = []
        for row in conn.execute(
            "SELECT id, fingerprint, avg_time_ms, exec_count, severity FROM slow_queries ORDER BY exec_count DESC LIMIT 5"
        ).fetchall():
            top5_freq.append(dict(row))

        return {
            "audit": {
                "today_count": today_count,
                "today_passed": today_passed,
                "today_failed": today_failed,
                "today_pass_rate": round(today_pass_rate, 1),
                "total_count": total_count,
            },
            "slow_queries": {
                "total": slow_total,
                "pending": slow_pending,
                "optimized": slow_optimized,
                "by_severity": by_severity,
                "top5_time": top5_time,
                "top5_frequency": top5_freq,
            },
            "rules": {
                "total": 22,
                "enabled": 22,
                "by_category": {
                    "naming": 2,
                    "ddl": 9,
                    "dml": 8,
                    "distributed": 3,
                },
            },
        }
    finally:
        conn.close()


@router.get("/audit-trend", summary="获取审核趋势数据")
async def get_audit_trend(days: int = 7):
    """
    获取最近N天的审核趋势数据，用于图表展示。
    """
    if not _db_exists():
        return {"dates": [], "passed": [], "failed": []}

    conn = _get_connection()
    try:
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        rows = conn.execute("""
            SELECT DATE(created_at) as dt,
                   SUM(CASE WHEN failed = 0 THEN 1 ELSE 0 END) as passed,
                   SUM(failed) as failed_count
            FROM audit_history
            WHERE DATE(created_at) >= ?
            GROUP BY DATE(created_at)
            ORDER BY dt
        """, (start_date,)).fetchall()

        dates = [row["dt"] for row in rows]
        passed = [row["passed"] or 0 for row in rows]
        failed = [row["failed_count"] or 0 for row in rows]

        return {"dates": dates, "passed": passed, "failed": failed}
    finally:
        conn.close()


@router.get("/rule-stats", summary="获取规则统计")
async def get_rule_stats():
    """获取各规则的命中统计"""
    if not _db_exists():
        return {"rules": []}

    conn = _get_connection()
    try:
        # 从审核历史中提取规则命中统计
        rows = conn.execute("""
            SELECT results_json FROM audit_history
            ORDER BY created_at DESC LIMIT 100
        """).fetchall()

        rule_hits = {}
        import json
        for row in rows:
            try:
                results = json.loads(row["results_json"])
                for result in results:
                    for v in result.get("violations", []):
                        rid = v.get("rule_id", "")
                        if rid:
                            rule_hits[rid] = rule_hits.get(rid, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue

        # 按命中次数排序
        sorted_rules = sorted(rule_hits.items(), key=lambda x: x[1], reverse=True)

        return {
            "rules": [{"rule_id": r, "hit_count": c} for r, c in sorted_rules]
        }
    finally:
        conn.close()
