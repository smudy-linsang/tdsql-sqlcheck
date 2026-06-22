"""
TDSQL SQL审核工具 - 死锁分析引擎 (V1.0)

分析TDSQL死锁日志，提取死锁事务信息和锁竞争资源。
"""
import re
from typing import Optional

from backend.models import DeadlockReport


class DeadlockAnalyzer:
    """死锁分析器"""

    def analyze_from_log(self, deadlock_log: str) -> DeadlockReport:
        """
        从死锁日志文本分析死锁信息。

        TDSQL/MySQL死锁日志格式:
        *** (1) TRANSACTION: ...
        *** (1) WAITING FOR THIS LOCK TO BE GRANTED: ...
        *** (2) TRANSACTION: ...
        *** (2) HOLDS THE LOCK(S): ...
        *** (2) WAITING FOR THIS LOCK TO BE GRANTED: ...
        *** WE ROLL BACK TRANSACTION (2)

        Args:
            deadlock_log: 死锁日志文本

        Returns:
            DeadlockReport 死锁分析报告
        """
        report = DeadlockReport()

        if not deadlock_log or "DEADLOCK" not in deadlock_log.upper() or "***" not in deadlock_log:
            return report

        report.has_deadlock = True

        # 提取死锁时间
        time_match = re.search(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})", deadlock_log)
        if time_match:
            report.deadlock_time = time_match.group(1)

        # 提取事务1信息
        report.transaction_1 = self._extract_transaction(deadlock_log, 1)

        # 提取事务2信息
        report.transaction_2 = self._extract_transaction(deadlock_log, 2)

        # 提取锁竞争资源
        lock_match = re.search(r"of table\s+`?(\w+)`?\.`?(\w+)`?", deadlock_log)
        if lock_match:
            report.locked_resource = f"{lock_match.group(1)}.{lock_match.group(2)}"

        # 生成建议
        report.suggestions = self._generate_suggestions(report)

        return report

    def _extract_transaction(self, log: str, tx_num: int) -> dict:
        """提取事务信息"""
        tx_info = {"id": "", "query": "", "waiting_for": "", "holds_lock": ""}

        # 事务ID
        id_pattern = rf"\({tx_num}\)\s+TRANSACTION:.*?TRANSACTION\s+(\d+)"
        id_match = re.search(id_pattern, log, re.DOTALL)
        if id_match:
            tx_info["id"] = id_match.group(1)

        # 事务SQL
        tx_pattern = rf"\({tx_num}\)\s+TRANSACTION.*?(?:QUERY|SQL)(?:.*?)?[:\s]+(.*?)(?=\*\*\*|\Z)"
        tx_match = re.search(tx_pattern, log, re.DOTALL)
        if tx_match:
            tx_info["query"] = tx_match.group(1).strip()[:500]

        # 等待的锁
        wait_pattern = rf"\({tx_num}\)\s+WAITING FOR THIS LOCK.*?:(.*?)(?=\*\*\*|\Z)"
        wait_match = re.search(wait_pattern, log, re.DOTALL)
        if wait_match:
            tx_info["waiting_for"] = wait_match.group(1).strip()[:500]

        # 持有的锁
        holds_pattern = rf"\({tx_num}\)\s+HOLDS THE LOCK.*?:(.*?)(?=\*\*\*|\Z)"
        holds_match = re.search(holds_pattern, log, re.DOTALL)
        if holds_match:
            tx_info["holds_lock"] = holds_match.group(1).strip()[:500]

        return tx_info

    def _generate_suggestions(self, report: DeadlockReport) -> list[str]:
        """生成死锁优化建议"""
        suggestions = []

        tx1_query = report.transaction_1.get("query", "").lower()
        tx2_query = report.transaction_2.get("query", "").lower()

        # 检查是否有FOR UPDATE
        if "for update" in tx1_query or "for update" in tx2_query:
            suggestions.append("检测到SELECT...FOR UPDATE，建议缩短事务范围或使用乐观锁")

        # 检查是否有不同顺序的锁
        if report.locked_resource:
            suggestions.append(f"锁竞争资源: {report.locked_resource}，建议统一事务中操作表的顺序")

        # 通用建议
        suggestions.append("建议排查事务中是否有多表交叉更新，统一加锁顺序")
        suggestions.append("考虑使用RC隔离级别减少Gap Lock，或调整业务逻辑避免锁竞争")

        return suggestions
