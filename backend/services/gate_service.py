"""
TDSQL SQL审核工具 - 质量门禁服务 (V1.0)

基于审核结果评估质量门禁，支持strict/normal/loose三种策略。
"""
import json
import logging
from datetime import datetime
from typing import Optional

from backend.models import GateRule, GateResult, Violation, Severity
from backend.services.database import _get_connection, ensure_db, log_operation

logger = logging.getLogger("tdsql.gate")

# 门禁策略预设
GATE_STRATEGIES = {
    "strict": {"max_error": 0, "max_warning": 0, "desc": "严格策略：不允许任何违规"},
    "normal": {"max_error": 0, "max_warning": -1, "desc": "普通策略：不允许ERROR，WARNING不限"},
    "loose": {"max_error": -1, "max_warning": -1, "desc": "宽松策略：不限制违规数量"},
}


class GateService:
    """质量门禁服务"""

    def evaluate(self, violations: list[Violation], gate_rule: Optional[GateRule] = None) -> GateResult:
        """
        评估审核结果是否通过门禁。

        Args:
            violations: 违规列表
            gate_rule: 门禁规则，None则使用默认规则

        Returns:
            GateResult 门禁评估结果
        """
        if gate_rule is None:
            gate_rule = self.get_gate_rule("default")

        error_count = sum(1 for v in violations if v.severity == Severity.ERROR or str(v.severity) == "ERROR")
        warning_count = sum(1 for v in violations if v.severity == Severity.WARNING or str(v.severity) == "WARNING")

        # 检查阻断规则
        blocked_by = []
        for v in violations:
            if v.rule_id in (gate_rule.blocked_rules or []):
                blocked_by.append(v.rule_id)

        # 评估是否通过
        passed = True
        reasons = []

        if gate_rule.max_error_count >= 0 and error_count > gate_rule.max_error_count:
            passed = False
            reasons.append(f"ERROR违规{error_count}个，超过上限{gate_rule.max_error_count}")

        if gate_rule.max_warning_count >= 0 and warning_count > gate_rule.max_warning_count:
            passed = False
            reasons.append(f"WARNING违规{warning_count}个，超过上限{gate_rule.max_warning_count}")

        if blocked_by:
            passed = False
            reasons.append(f"触发阻断规则: {','.join(blocked_by)}")

        # 检查必须通过的规则
        for required_rule in (gate_rule.required_rules or []):
            for v in violations:
                if v.rule_id == required_rule:
                    passed = False
                    reasons.append(f"必须通过的规则 {required_rule} 未通过")
                    break

        detail = "；".join(reasons) if reasons else "门禁检查通过"

        return GateResult(
            passed=passed,
            gate_rule_id=gate_rule.project_id,
            error_count=error_count,
            warning_count=warning_count,
            blocked_by=blocked_by,
            detail=detail,
        )

    def get_gate_rule(self, project_id: str = "default") -> GateRule:
        """获取门禁规则"""
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM gate_rules WHERE project_id = ?", (project_id,)
            ).fetchone()
            if not row:
                return GateRule(project_id=project_id, max_error_count=0, max_warning_count=-1)
            return GateRule(
                project_id=row["project_id"],
                max_error_count=row["max_error_count"],
                max_warning_count=row["max_warning_count"],
                required_rules=json.loads(row["required_rules"] or "[]"),
                blocked_rules=json.loads(row["blocked_rules"] or "[]"),
                description=row["description"],
            )
        finally:
            conn.close()

    def set_gate_rule(self, gate_rule: GateRule) -> bool:
        """设置门禁规则"""
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO gate_rules
                (project_id, max_error_count, max_warning_count, required_rules, blocked_rules, description, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                gate_rule.project_id,
                gate_rule.max_error_count,
                gate_rule.max_warning_count,
                json.dumps(gate_rule.required_rules),
                json.dumps(gate_rule.blocked_rules),
                gate_rule.description,
                datetime.now().isoformat(),
            ))
            conn.commit()
            log_operation("system", "set_gate_rule", "gate_rule", gate_rule.project_id)
            return True
        finally:
            conn.close()

    def apply_strategy(self, project_id: str, strategy: str) -> bool:
        """应用预设门禁策略"""
        if strategy not in GATE_STRATEGIES:
            return False
        config = GATE_STRATEGIES[strategy]
        gate_rule = GateRule(
            project_id=project_id,
            max_error_count=config["max_error"],
            max_warning_count=config["max_warning"],
            description=config["desc"],
        )
        return self.set_gate_rule(gate_rule)

    def log_gate_audit(self, project_id: str, passed: bool, error_count: int,
                       warning_count: int, blocked_by: list, detail: str,
                       audit_history_id: Optional[int] = None, source: str = ""):
        """记录门禁审核日志"""
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute("""
                INSERT INTO gate_audit_logs
                (project_id, audit_history_id, source, passed, error_count, warning_count, blocked_by, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                project_id, audit_history_id, source,
                1 if passed else 0, error_count, warning_count,
                json.dumps(blocked_by), detail,
            ))
            conn.commit()
        finally:
            conn.close()
