"""
TDSQL SQL审核工具 - 审核服务 (V2.0)

封装审核引擎，提供业务层接口。

V2.0 变更:
- 审核历史记录操作用户（created_by）与项目ID
- 支持项目级规则集覆盖（多租户规则）
- 支持门禁评估联动
"""
import json
import logging
from datetime import datetime
from typing import Optional

from backend.engine.checker import RuleChecker
from backend.models import (
    AuditResult,
    AuditSummary,
    GateResult,
)

logger = logging.getLogger("tdsql.audit_service")

from backend.services.database import _get_connection, ensure_db


def _save_audit_history(audit_type: str, source: str, results: list[AuditResult],
                        summary: AuditSummary, created_by: str = "",
                        project_id: str = "",
                        gate_result: Optional[GateResult] = None):
    """保存审核历史到数据库"""
    try:
        ensure_db()
        conn = _get_connection()
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
                    error_count, warning_count, pass_rate, results_json,
                    created_by, project_id, gate_passed, gate_detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                audit_type, source,
                summary.total_sql, summary.passed, summary.failed,
                summary.error_count, summary.warning_count, summary.pass_rate,
                results_json, created_by, project_id,
                (1 if gate_result.passed else 0) if gate_result else None,
                gate_result.detail if gate_result else "",
                datetime.now().isoformat(),
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

    def _resolve_overrides(self, project_id: Optional[str]) -> Optional[dict]:
        """按项目解析规则集覆盖"""
        if not project_id:
            return None
        try:
            from backend.services.ruleset_service import ruleset_service
            return ruleset_service.get_overrides_for_project(project_id)
        except Exception as e:
            logger.warning(f"解析项目规则集失败(按默认规则执行): {e}")
            return None

    def audit_single_sql(self, sql: str, created_by: str = "",
                         project_id: str = "",
                         evaluate_gate: bool = False) -> tuple[AuditResult, Optional[GateResult]]:
        """
        审核单条 SQL。

        Returns:
            (审核结果, 门禁结果或None)
        """
        from backend.services.database import split_sql_statements
        statements = [s.strip() for s in split_sql_statements(sql) if s.strip()]

        overrides = self._resolve_overrides(project_id)

        if len(statements) <= 1:
            result = self.checker.audit_sql(sql, rule_overrides=overrides)
        else:
            results = []
            all_violations = []
            for idx, stmt in enumerate(statements, 1):
                res = self.checker.audit_sql(stmt, rule_overrides=overrides)
                for v in res.violations:
                    v.message = f"[第{idx}条语句] {v.message}"
                    all_violations.append(v)
                results.append(res)

            sql_types = {res.sql_type for res in results if res.sql_type}
            combined_type = "BATCH" if len(sql_types) > 1 else (list(sql_types)[0] if sql_types else "BATCH")

            result = AuditResult(
                sql=sql,
                sql_type=combined_type,
                passed=len(all_violations) == 0,
                violations=all_violations,
            )

        gate_result = None
        if evaluate_gate:
            gate_result = self._evaluate_gate(result.violations, project_id)

        summary = self.checker.compute_summary([result])
        _save_audit_history("sql", "api", [result], summary,
                            created_by=created_by, project_id=project_id,
                            gate_result=gate_result)
        try:
            from backend.services import metrics_service
            metrics_service.inc("tdsql_audit_sql_total")
            for v in result.violations:
                sev = v.severity.value if hasattr(v.severity, 'value') else str(v.severity)
                metrics_service.inc("tdsql_violations_total", {"severity": sev})
        except Exception:
            pass
        return result, gate_result

    def audit_sql_list(self, sql_list: list[str], created_by: str = "",
                       project_id: str = "") -> list[AuditResult]:
        """审核多条 SQL"""
        overrides = self._resolve_overrides(project_id)
        results = [self.checker.audit_sql(sql, rule_overrides=overrides)
                   for sql in sql_list]
        summary = self.checker.compute_summary(results)
        _save_audit_history("sql_batch", "api", results, summary,
                            created_by=created_by, project_id=project_id)
        return results

    def audit_file_content(self, content: str, file_path: str = "",
                           created_by: str = "", project_id: str = "",
                           evaluate_gate: bool = False
                           ) -> tuple[list[AuditResult], AuditSummary, Optional[GateResult]]:
        """审核文件内容，返回结果列表、汇总和门禁结果"""
        overrides = self._resolve_overrides(project_id)
        results = self.checker.audit_file(content, file_path=file_path,
                                          rule_overrides=overrides)
        summary = self.checker.compute_summary(results)

        gate_result = None
        if evaluate_gate:
            all_violations = [v for r in results for v in r.violations]
            gate_result = self._evaluate_gate(all_violations, project_id)

        source = file_path if file_path else "file_upload"
        _save_audit_history("file", source, results, summary,
                            created_by=created_by, project_id=project_id,
                            gate_result=gate_result)
        return results, summary, gate_result

    def _evaluate_gate(self, violations, project_id: str) -> Optional[GateResult]:
        """门禁评估"""
        try:
            from backend.services.gate_service import GateService
            gate_service = GateService()
            gate_rule = gate_service.get_gate_rule(project_id or "default")
            return gate_service.evaluate(violations, gate_rule)
        except Exception as e:
            logger.warning(f"门禁评估失败: {e}")
            return None

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
