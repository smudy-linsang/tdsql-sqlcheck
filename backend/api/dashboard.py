"""
TDSQL SQL审核工具 - 审核治理概览 API

提供审核拦截效果、慢SQL治理进展和高频违规规则的统计概览数据。
"""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/dashboard", tags=["审核治理概览"])


def _get_rule_info_map() -> dict:
    """获取规则ID到规则信息的映射字典"""
    try:
        from backend.engine.checker import RuleChecker
        checker = RuleChecker()
        rules = checker.get_rules_info()
        return {r["rule_id"]: r for r in rules}
    except Exception:
        return {}


def _get_rule_stats() -> dict:
    """动态获取规则统计"""
    try:
        from backend.engine.checker import RuleChecker
        checker = RuleChecker()
        rules = checker.get_rules_info()
        by_category = {}
        for r in rules:
            cat = r.get("category", "other")
            by_category[cat] = by_category.get(cat, 0) + 1
        return {"total": len(rules), "enabled": len(rules), "by_category": by_category}
    except Exception:
        return {"total": 77, "enabled": 77, "by_category": {}}

from backend.services.database import _get_connection, ensure_db


def _db_exists() -> bool:
    """数据库是否可用"""
    try:
        ensure_db()
        return True
    except Exception:
        return False


@router.get("/summary", summary="获取审核治理概览数据")
def get_summary():
    """
    获取审核拦截效果、慢SQL治理进展和最近审核活动，用于审核治理概览首页展示。
    """
    if not _db_exists():
        return {
            "audit": {
                "today_count": 0,
                "today_passed": 0,
                "today_failed": 0,
                "today_errors": 0,
                "today_warnings": 0,
                "today_violations": 0,
                "today_pass_rate": 0,
            },
            "slow_queries": {
                "total": 0,
                "pending": 0,
                "optimized": 0,
                "critical_count": 0,
                "top3_time": [],
            },
            "recent_audits": [],
            "rules": _get_rule_stats(),
        }

    conn = _get_connection()
    try:
        today = datetime.now().strftime("%Y-%m-%d")

        # 今日审核统计（含ERROR/WARNING违规数）
        audit_today = conn.execute("""
            SELECT COUNT(*) as cnt,
                   SUM(CASE WHEN failed = 0 THEN 1 ELSE 0 END) as passed,
                   SUM(failed) as failed_count,
                   SUM(error_count) as errors,
                   SUM(warning_count) as warnings
            FROM audit_history
            WHERE DATE(created_at) = ?
        """, (today,)).fetchone()

        today_count = audit_today["cnt"] or 0
        today_passed = audit_today["passed"] or 0
        today_failed = audit_today["failed_count"] or 0
        today_errors = audit_today["errors"] or 0
        today_warnings = audit_today["warnings"] or 0
        today_pass_rate = (today_passed / today_count * 100) if today_count > 0 else 0

        # 今日发现违规总数（ERROR + WARNING）
        today_violations = today_errors + today_warnings

        # 今日审核记录（仅当日，避免全表扫描）
        recent_audits = []
        for row in conn.execute("""
            SELECT id, audit_type, source, total_sql, passed, failed,
                   error_count, warning_count, pass_rate, created_at
            FROM audit_history
            WHERE DATE(created_at) = ?
            ORDER BY created_at DESC LIMIT 10
        """, (today,)).fetchall():
            recent_audits.append({
                "id": row["id"],
                "audit_type": row["audit_type"],
                "source": row["source"] or "",
                "total_sql": row["total_sql"],
                "passed": row["passed"],
                "failed": row["failed"],
                "error_count": row["error_count"],
                "warning_count": row["warning_count"],
                "pass_rate": round(row["pass_rate"], 1) if row["pass_rate"] else 0,
                "created_at": row["created_at"] or "",
            })

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

        # 高风险慢SQL数（ERROR级别；慢SQL严重度体系为 ERROR/WARNING/INFO，无 CRITICAL）
        slow_critical = conn.execute(
            "SELECT COUNT(*) as cnt FROM slow_queries WHERE severity IN ('ERROR','CRITICAL') AND status = 'pending'"
        ).fetchone()["cnt"] or 0

        # Top3 高耗时慢SQL（含优化状态）
        top3_time = []
        for row in conn.execute(
            "SELECT id, fingerprint, avg_time_ms, exec_count, severity, status "
            "FROM slow_queries ORDER BY avg_time_ms DESC LIMIT 3"
        ).fetchall():
            top3_time.append(dict(row))

        return {
            "audit": {
                "today_count": today_count,
                "today_passed": today_passed,
                "today_failed": today_failed,
                "today_errors": today_errors,
                "today_warnings": today_warnings,
                "today_violations": today_violations,
                "today_pass_rate": round(today_pass_rate, 1),
            },
            "slow_queries": {
                "total": slow_total,
                "pending": slow_pending,
                "optimized": slow_optimized,
                "critical_count": slow_critical,
                "top3_time": top3_time,
            },
            "recent_audits": recent_audits,
            "rules": _get_rule_stats(),
        }
    finally:
        conn.close()


@router.get("/audit-trend", summary="获取审核趋势数据")
def get_audit_trend(days: int = 7):
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


@router.get("/rule-stats", summary="获取高频违规规则统计")
def get_rule_stats():
    """获取高频违规规则命中统计，返回规则描述和严重级别"""
    if not _db_exists():
        return {"rules": []}

    conn = _get_connection()
    try:
        # 从今日审核历史中提取规则命中统计
        today = datetime.now().strftime("%Y-%m-%d")
        rows = conn.execute("""
            SELECT results_json FROM audit_history
            WHERE DATE(created_at) = ?
            ORDER BY created_at DESC
        """, (today,)).fetchall()

        rule_hits = {}
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

        # 获取规则元数据（描述、严重级别、类别）
        rule_info_map = _get_rule_info_map()

        # 按命中次数排序，取前10
        sorted_rules = sorted(rule_hits.items(), key=lambda x: x[1], reverse=True)[:10]

        rules_data = []
        for rid, count in sorted_rules:
            info = rule_info_map.get(rid, {})
            rules_data.append({
                "rule_id": rid,
                "description": info.get("description", rid),
                "severity": info.get("severity", ""),
                "category": info.get("category", ""),
                "hit_count": count,
            })

        return {"rules": rules_data}
    finally:
        conn.close()
