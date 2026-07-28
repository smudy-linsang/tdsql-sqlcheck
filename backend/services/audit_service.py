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
                        gate_result: Optional[GateResult] = None,
                        connection_id: str = "", db_name: str = "",
                        rule_set_id: str = ""):
    """保存审核历史到数据库

    V1.3(D1): 新增 connection_id / db_name，支撑扫描结果对比按实例筛选。
    V1.4: 新增 rule_set_id，记录本次审核实际生效的规则集（尺度可追溯）。
    均有默认值，既有调用方无需改动。
    """
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
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO audit_history (audit_type, source, total_sql, passed, failed,
                    error_count, warning_count, pass_rate, results_json,
                    created_by, project_id, gate_passed, gate_detail, created_at,
                    connection_id, db_name, rule_set_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                audit_type, source,
                summary.total_sql, summary.passed, summary.failed,
                summary.error_count, summary.warning_count, summary.pass_rate,
                results_json, created_by, project_id,
                (1 if gate_result.passed else 0) if gate_result else None,
                gate_result.detail if gate_result else "",
                datetime.now().isoformat(),
                connection_id or "", db_name or "",
                # NULL 语义为"V1.4 前历史记录，尺度未知"，故空串写 None 而非 ""
                rule_set_id or None,
            ))
            conn.commit()
            return getattr(cursor, "lastrowid", None)
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"保存审核历史失败: {e}")
        return None


class AuditService:
    """SQL审核业务服务"""

    def __init__(self):
        self.checker = RuleChecker(dialect="mysql")

    def _resolve_scale(self) -> tuple[str, Optional[dict]]:
        """解析当前全局生效的评估尺度，返回 (规则集ID, 规则覆盖)。

        V1.4：尺度由管理员全局启用，不再随调用方传入的 project_id 变化——
        这正是为了消除"换个项目再扫一次，问题就变少了"的伪命题。
        解析失败一律回落全默认，绝不因尺度解析异常打断审核主流程。
        """
        try:
            from backend.services.ruleset_service import ruleset_service
            return ruleset_service.get_active_overrides()
        except Exception as e:
            logger.warning(f"解析全局规则集失败(按引擎默认执行): {e}")
            return "default", None

    def _resolve_overrides(self, project_id: Optional[str] = None) -> Optional[dict]:
        """DEPRECATED(V1.4)：保留仅为兼容；尺度已全局化，project_id 被忽略"""
        return self._resolve_scale()[1]

    def audit_single_sql(self, sql: str, created_by: str = "",
                         project_id: str = "",
                         evaluate_gate: bool = False,
                         connection_id: str = "") -> tuple[AuditResult, Optional[GateResult]]:
        """
        审核单条 SQL。

        V1.4：尺度取自全局生效规则集（project_id 不再决定尺度，仅兼容保留）；
        门禁按 connection_id 绑定的实例判定。

        Returns:
            (审核结果, 门禁结果或None)
        """
        from backend.services.database import split_sql_statements
        statements = [s.strip() for s in split_sql_statements(sql) if s.strip()]

        rule_set_id, overrides = self._resolve_scale()

        if len(statements) <= 1:
            result = self.checker.audit_sql(sql, rule_overrides=overrides)
            # v1.2 TDSQL 分布式检测补充
            try:
                from backend.engine.parser.tdsql_auditor import TDSQLAuditor
                from backend.engine.parser.ast_parser import ASTParser
                auditor = TDSQLAuditor()
                ast_p = ASTParser()
                expr = ast_p.parse(sql)
                findings = auditor.check_shard_key_presence(expr, ["order_id", "user_id"])
                for f in findings:
                    from backend.models import Violation, Severity
                    sev = Severity.ERROR if f.severity == "ERROR" else Severity.WARNING
                    result.violations.append(Violation(
                        rule_id=f.rule_id,
                        severity=sev,
                        message=f.message,
                        suggestion=f.suggestion
                    ))
                    result.passed = False if f.severity == "ERROR" else result.passed
            except Exception as e:
                logger.debug(f"TDSQL 深度分布式规则检查跳过: {e}")
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
            gate_result = self._evaluate_gate(result.violations, connection_id)

        summary = self.checker.compute_summary([result])
        _save_audit_history("sql", "api", [result], summary,
                            created_by=created_by, project_id=project_id,
                            gate_result=gate_result, connection_id=connection_id,
                            rule_set_id=rule_set_id)
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
        rule_set_id, overrides = self._resolve_scale()
        results = [self.checker.audit_sql(sql, rule_overrides=overrides)
                   for sql in sql_list]
        summary = self.checker.compute_summary(results)
        _save_audit_history("sql_batch", "api", results, summary,
                            created_by=created_by, project_id=project_id,
                            rule_set_id=rule_set_id)
        return results

    def audit_file_content(self, content: str, file_path: str = "",
                           created_by: str = "", project_id: str = "",
                           evaluate_gate: bool = False,
                           save_history: bool = True,
                           connection_id: str = ""
                           ) -> tuple[list[AuditResult], AuditSummary, Optional[GateResult]]:
        """审核文件内容，返回结果列表、汇总和门禁结果（V1.4：尺度全局、门禁按实例）"""
        rule_set_id, overrides = self._resolve_scale()
        results = self.checker.audit_file(content, file_path=file_path,
                                          rule_overrides=overrides)
        summary = self.checker.compute_summary(results)

        gate_result = None
        if evaluate_gate:
            all_violations = [v for r in results for v in r.violations]
            gate_result = self._evaluate_gate(all_violations, connection_id)

        source = file_path if file_path else "file_upload"
        if save_history:
            _save_audit_history("file", source, results, summary,
                                created_by=created_by, project_id=project_id,
                                gate_result=gate_result, connection_id=connection_id,
                                rule_set_id=rule_set_id)
        return results, summary, gate_result

    def _evaluate_gate(self, violations, connection_id: str = "") -> Optional[GateResult]:
        """门禁评估（V1.4：按实例，不再按项目）"""
        try:
            from backend.services.gate_service import GateService
            return GateService().evaluate_for_instance(violations, connection_id)
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
