"""
TDSQL SQL审核工具 - 安全规范规则 (R073-R076)

V1.0新增: 4条安全规范规则。
"""
import re
from typing import Optional

from backend.engine.parser import ParsedSQL
from backend.engine.rules.base import BaseRule
from backend.models import RuleCategory, Severity, Violation


class R073NoDdlWithoutBackup(BaseRule):
    """R073: DDL变更需备份确认"""
    rule_id = "R073"
    category = RuleCategory.SECURITY
    severity = Severity.WARNING
    description = "ALTER/DROP TABLE操作需确认已备份"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 安全规范"
    fix_suggestion = "请在执行DDL前备份表结构和数据"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.is_alter_table:
            return self._make_violation(
                "ALTER TABLE操作需确认已备份表结构和数据，且在低峰期执行",
            )
        if parsed.sql_type == "DROP":
            return self._make_violation(
                "DROP操作需确认已备份，且通过DBA审批",
            )
        return None


class R074NoGrantRevoke(BaseRule):
    """R074: 禁GRANT/REVOKE"""
    rule_id = "R074"
    category = RuleCategory.SECURITY
    severity = Severity.ERROR
    description = "禁止在应用SQL中使用GRANT/REVOKE权限操作"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 安全规范"
    fix_suggestion = "权限管理由DBA通过管理工具操作"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        raw_lower = parsed.raw_sql.lower().strip()
        if re.match(r"\b(grant|revoke)\b", raw_lower):
            return self._make_violation(
                "禁止在应用SQL中使用GRANT/REVOKE，权限管理由DBA操作",
            )
        return None


class R075NoTruncateWithoutCheck(BaseRule):
    """R075: TRUNCATE需确认"""
    rule_id = "R075"
    category = RuleCategory.SECURITY
    severity = Severity.ERROR
    description = "TRUNCATE TABLE需确认，该操作不可回滚"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 安全规范"
    fix_suggestion = "TRUNCATE会清空全表数据且不可回滚，请确认后执行"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        raw_lower = parsed.raw_sql.lower().strip()
        if re.match(r"\btruncate\b", raw_lower):
            return self._make_violation(
                "TRUNCATE TABLE操作不可回滚，请确认表名正确且已备份",
            )
        return None


class R076NoSqlInjectionRisk(BaseRule):
    """R076: SQL注入风险检测"""
    rule_id = "R076"
    category = RuleCategory.SECURITY
    severity = Severity.ERROR
    description = "检测到疑似SQL注入风险（${}拼接或动态表名）"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 安全规范"
    fix_suggestion = "使用参数化查询(#{}), 避免SQL字符串拼接(${}})"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        raw = parsed.raw_sql
        # 检测MyBatis ${} 拼接
        if "${" in raw and "}" in raw:
            return self._make_violation(
                "检测到MyBatis ${}动态拼接，存在SQL注入风险，请使用#{}参数化",
            )
        # 检测拼接式SQL
        if re.search(r"['\"]\s*\+\s*", raw) or re.search(r"\+\s*['\"]", raw):
            return self._make_violation(
                "检测到SQL字符串拼接，存在SQL注入风险",
            )
        return None
