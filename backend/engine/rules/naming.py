"""
TDSQL SQL审核工具 - 命名规范规则 (R001-R002)

R001: 库名/表名字符限制32以内，必须符合 ^[a-z][a-z0-9_]*$
R002: 表名不能使用 TDSQL 关键字
"""
import re
from typing import Optional, Dict

from backend.config import TDSQL_RESERVED_KEYWORDS
from backend.engine.parser import ParsedSQL
from backend.engine.rules.base import BaseRule
from backend.models import RuleCategory, Severity, Violation


class R001NamingLength(BaseRule):
    """R001: 库名/表名字符限制32以内，必须符合 ^[a-z][a-z0-9_]*$"""

    rule_id = "R001"
    category = RuleCategory.NAMING
    severity = Severity.ERROR
    description = "库名/表名字符限制32以内，必须以小写字母开头，仅包含小写字母、数字和下划线"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 命名规范"
    fix_suggestion = "表名以小写字母开头，仅包含小写字母、数字、下划线，长度≤32"

    # 命名正则：以小写字母开头，仅包含小写字母、数字、下划线
    NAMING_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
    MAX_LENGTH = 32

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        for table in parsed.tables:
            # 可能包含 schema.table 格式
            parts = table.split(".")
            for part in parts:
                name = part.strip("`\"' ")
                if not name:
                    continue
                # 长度检查
                if len(name) > self.MAX_LENGTH:
                    return self._make_violation(
                        f"表/库名 '{name}' 长度 {len(name)} 超过 {self.MAX_LENGTH} 个字符限制",
                        suggestion=f"缩短 '{name}' 长度至 {self.MAX_LENGTH} 个字符以内",
                    )
                # 命名格式检查
                if not self.NAMING_PATTERN.match(name):
                    return self._make_violation(
                        f"表/库名 '{name}' 不符合命名规范：必须以小写字母开头，仅包含小写字母、数字和下划线",
                        suggestion=f"建议修改为: {self._suggest_name(name)}",
                    )
        return None

    def _suggest_name(self, name: str) -> str:
        """生成符合规范的名称建议"""
        # 转小写
        suggested = name.lower()
        # 移除非法字符
        suggested = re.sub(r"[^a-z0-9_]", "_", suggested)
        # 确保以字母开头
        if suggested and not suggested[0].isalpha():
            suggested = "t_" + suggested
        # 截断
        if len(suggested) > self.MAX_LENGTH:
            suggested = suggested[: self.MAX_LENGTH]
        # 去除尾部下划线
        suggested = suggested.rstrip("_")
        return suggested


class R002ReservedKeywords(BaseRule):
    """R002: 表名不能使用 TDSQL 关键字"""

    rule_id = "R002"
    category = RuleCategory.NAMING
    severity = Severity.ERROR
    description = "表名不能使用 TDSQL/MySQL 保留关键字"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 命名规范"
    fix_suggestion = "请为表名添加业务前缀或后缀，如: t_order, order_tbl"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        for table in parsed.tables:
            parts = table.split(".")
            for part in parts:
                name = part.strip("`\"' ").lower()
                if not name:
                    continue
                if name in TDSQL_RESERVED_KEYWORDS:
                    return self._make_violation(
                        f"表名 '{name}' 是 TDSQL/MySQL 保留关键字，禁止使用",
                        suggestion=f"建议为表名添加前缀或后缀，如: t_{name}, {name}_tbl",
                    )
        return None
