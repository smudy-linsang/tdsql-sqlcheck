"""
慢SQL扫描任务（slow_scan）问题项抽取器

设计依据：docs/DETAIL-v1.3-扫描结果对比.md §5.3.2

指纹：fp(module, db_name, sha1_16(fingerprint))
  set_id 是多SET合并分布串（如 set_a(40),set_b(11)），一行本为跨 set 聚合，
  故不入指纹、仅记入 attrs 供报告展示。
"""
import logging

from backend.services.database import _get_connection
from .base import IssueItem, fp, sha1_16

logger = logging.getLogger(__name__)

# 问题项口径：仅 ERROR/WARNING 记为问题项，INFO 仅计入 object_total
_ISSUE_SEVERITIES = ("ERROR", "WARNING")


def extract(task_id: int, db_name_default: str = "") -> tuple[list[IssueItem], int]:
    """从 slow_queries 按 scan_task_id 抽取问题项"""
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT fingerprint, db_name, set_id, avg_time_ms, max_time_ms, exec_count,
                   severity, problem_type, suggestion, last_seen, involved_tables
            FROM slow_queries WHERE scan_task_id = ?
        """, (task_id,)).fetchall()
    finally:
        conn.close()

    items, total = [], 0
    for r in rows or []:
        d = dict(r)
        total += 1
        severity = (d.get("severity") or "").upper()
        if severity not in _ISSUE_SEVERITIES:
            continue
        db = d.get("db_name") or db_name_default
        fp_hash = sha1_16(d.get("fingerprint") or "")
        avg_ms = float(d.get("avg_time_ms") or 0)
        exec_cnt = int(d.get("exec_count") or 0)
        items.append(IssueItem(
            key=fp("slow_scan", db, fp_hash),
            object_name=(d.get("involved_tables") or db or "")[:200],
            object_type="SQL",
            issue_type=(d.get("problem_type") or "SLOW")[:64],
            severity=severity,
            title=d.get("fingerprint") or "",
            detail=f"平均耗时 {avg_ms:.1f}ms / 执行 {exec_cnt} 次",
            suggestion=(d.get("suggestion") or "")[:1000],
            attrs={
                "avg_time_ms": avg_ms,
                "max_time_ms": float(d.get("max_time_ms") or 0),
                "exec_count": exec_cnt,
                "severity": severity,
                "last_seen": d.get("last_seen") or "",
                # set 分布仅展示，不入指纹（DETAIL §3.3.2）
                "set_id": d.get("set_id") or "",
            },
        ))
    return items, total
