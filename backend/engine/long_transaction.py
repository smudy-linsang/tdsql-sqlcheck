"""
TDSQL SQL审核工具 - 长事务分析引擎 (V1.0)

检测和分析TDSQL长事务，评估风险并给出优化建议。
"""
from typing import Optional

from backend.models import LongTransactionInfo


class LongTransactionAnalyzer:
    """长事务分析器"""

    # 查询长事务的SQL
    QUERY_LONG_TRANSACTIONS = """
        SELECT trx_id, trx_started, trx_state, trx_rows_locked, trx_rows_modified,
               trx_query, TIMESTAMPDIFF(SECOND, trx_started, NOW()) as run_seconds
        FROM information_schema.INNODB_TRX
        WHERE TIMESTAMPDIFF(SECOND, trx_started, NOW()) > %s
        ORDER BY run_seconds DESC
    """

    # 风险阈值
    WARNING_THRESHOLD = 5    # 5秒以上告警
    CRITICAL_THRESHOLD = 30  # 30秒以上严重

    def analyze_from_query_results(self, rows: list[dict]) -> list[LongTransactionInfo]:
        """
        从information_schema.INNODB_TRX查询结果分析长事务。

        Args:
            rows: 长事务查询结果列表

        Returns:
            长事务信息列表
        """
        results = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            run_seconds = int(row.get("run_seconds", 0) or 0)
            if run_seconds < self.WARNING_THRESHOLD:
                continue

            severity = "CRITICAL" if run_seconds >= self.CRITICAL_THRESHOLD else "WARNING"
            info = LongTransactionInfo(
                trx_id=str(row.get("trx_id", "")),
                started_at=str(row.get("trx_started", "")),
                run_seconds=run_seconds,
                state=str(row.get("trx_state", "")),
                rows_locked=int(row.get("trx_rows_locked", 0) or 0),
                rows_modified=int(row.get("trx_rows_modified", 0) or 0),
                query=str(row.get("trx_query", ""))[:500],
                severity=severity,
            )
            results.append(info)
        return results

    def get_suggestions(self, info: LongTransactionInfo) -> list[str]:
        """获取长事务优化建议"""
        suggestions = []
        if info.run_seconds >= self.CRITICAL_THRESHOLD:
            suggestions.append(f"事务已运行{info.run_seconds}秒，严重影响系统，建议立即KILL")
        elif info.run_seconds >= self.WARNING_THRESHOLD:
            suggestions.append(f"事务运行{info.run_seconds}秒，建议优化事务范围")

        if info.rows_locked > 1000:
            suggestions.append(f"锁定{info.rows_locked}行，可能阻塞其他事务，建议缩小事务范围")

        if info.rows_modified > 10000:
            suggestions.append(f"已修改{info.rows_modified}行，建议分批提交")

        if not suggestions:
            suggestions.append("建议缩短事务范围，将非必要操作移出事务")
        return suggestions
