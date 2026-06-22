"""
TDSQL SQL审核工具 - 审核服务

封装审核引擎，提供业务层接口。
"""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.engine.checker import RuleChecker
from backend.models import (
    AuditResult,
    AuditSummary,
)

logger = logging.getLogger("tdsql.audit_service")

# 数据库路径
DB_PATH = Path(__file__).parent.parent.parent / "data" / "tdsql_check.db"


def _save_audit_history(audit_type: str, source: str, results: list[AuditResult],
                        summary: AuditSummary):
    """保存审核历史到数据库"""
    try:
        # 确保数据库和表已初始化（V1.0 database.py）
        from backend.services.database import ensure_db
        ensure_db()

        conn = sqlite3.connect(str(DB_PATH))
        try:
            results_json = json.dumps([{
                "sql": r.sql[:500],
                "sql_type": r.sql_type,
                "passed": r.passed,
                "file_path": r.file_path,
                "line_number": r.line_number,
                "violations": [{
                    "rule_id": v.rule_id,
                    "severity": v.severity.value if hasattr(v.severity, 'value') else str(v.severity),
                    "message": v.message,
                    "suggestion": v.suggestion,
                    "line_number": v.line_number,
                } for v in r.violations],
            } for r in results], ensure_ascii=False)
            conn.execute("""
                INSERT INTO audit_history (audit_type, source, total_sql, passed, failed,
                    error_count, warning_count, pass_rate, results_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                audit_type, source,
                summary.total_sql, summary.passed, summary.failed,
                summary.error_count, summary.warning_count, summary.pass_rate,
                results_json, datetime.now().isoformat(),
            ))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"保存审核历史失败: {e}")


class AuditService:
    """SQL审核业务服务"""

    def __init__(self):
        self.checker = RuleChecker(dialect="mysql")

    def audit_single_sql(self, sql: str) -> AuditResult:
        """审核单条 SQL"""
        result = self.checker.audit_sql(sql)
        # 保存审核历史
        summary = self.checker.compute_summary([result])
        _save_audit_history("sql", "api", [result], summary)
        return result

    def audit_sql_list(self, sql_list: list[str]) -> list[AuditResult]:
        """审核多条 SQL"""
        results = [self.checker.audit_sql(sql) for sql in sql_list]
        # 保存审核历史
        summary = self.checker.compute_summary(results)
        _save_audit_history("sql_batch", "api", results, summary)
        return results

    def audit_file_content(self, content: str, file_path: str = "") -> tuple[list[AuditResult], AuditSummary]:
        """审核文件内容，返回结果列表和汇总"""
        results = self.checker.audit_file(content, file_path=file_path)
        summary = self.checker.compute_summary(results)
        # 保存审核历史
        source = file_path if file_path else "file_upload"
        _save_audit_history("file", source, results, summary)
        return results, summary

    def get_rule_list(self) -> list[dict]:
        """获取所有已启用的规则列表"""
        rules = self.checker.get_enabled_rules()
        return [
            {
                "rule_id": r.rule_id,
                "category": r.category.value,
                "severity": r.severity.value,
                "description": r.description,
                "enabled": r.enabled,
            }
            for r in rules
        ]
