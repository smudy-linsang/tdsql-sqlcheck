"""
TDSQL SQL审核工具 - 事务规范规则 (R069-R072)

V1.0新增: 4条事务管理规范规则。
"""
import re
from typing import Optional

from backend.engine.parser import ParsedSQL
from backend.engine.rules.base import BaseRule
from backend.models import RuleCategory, Severity, Violation


class R069NoLongTransaction(BaseRule):
    """R069: 禁长事务"""
    rule_id = "R069"
    category = RuleCategory.TRANSACTION
    severity = Severity.WARNING
    description = "避免长事务，事务持续时间建议不超过5秒"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 事务规范"
    fix_suggestion = "缩短事务范围，将非必要操作移出事务"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.is_begin:
            return self._make_violation(
                "BEGIN/START TRANSACTION后请尽快COMMIT，避免长事务导致锁等待和MDL阻塞",
            )
        return None


class R070NoLargeTransaction(BaseRule):
    """R070: 禁大事务"""
    rule_id = "R070"
    category = RuleCategory.TRANSACTION
    severity = Severity.WARNING
    description = "单事务影响行数建议不超过10000行"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 事务规范"
    fix_suggestion = "大批量操作请分批提交，每批≤10000行"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type in ("UPDATE", "DELETE") and not parsed.has_where:
            return self._make_violation(
                "无WHERE条件的UPDATE/DELETE在大事务中会锁定大量行，请分批操作",
            )
        return None


class R071TransactionMustCommit(BaseRule):
    """R071: 事务必须显式提交"""
    rule_id = "R071"
    category = RuleCategory.TRANSACTION
    severity = Severity.WARNING
    description = "BEGIN后必须有显式COMMIT或ROLLBACK"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 事务规范"
    fix_suggestion = "确保每个BEGIN都有对应的COMMIT/ROLLBACK"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        # 静态检测：单条SQL中如果同时有BEGIN但无COMMIT/ROLLBACK
        raw_lower = parsed.raw_sql.lower()
        if ("begin" in raw_lower or "start transaction" in raw_lower):
            if "commit" not in raw_lower and "rollback" not in raw_lower:
                return self._make_violation(
                    "检测到BEGIN但未见COMMIT/ROLLBACK，请确保事务显式结束",
                )
        return None


class R072NoLockInTransaction(BaseRule):
    """R072: 事务中禁用排他锁"""
    rule_id = "R072"
    category = RuleCategory.TRANSACTION
    severity = Severity.WARNING
    description = "事务中避免使用SELECT...FOR UPDATE导致锁等待"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 事务规范"
    fix_suggestion = "如需加锁请缩短事务范围，或使用乐观锁替代"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.has_for_update:
            return self._make_violation(
                "SELECT...FOR UPDATE在事务中会导致行锁等待，请评估是否必要",
                suggestion="考虑使用乐观锁或缩短事务范围",
            )
        return None
