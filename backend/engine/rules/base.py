"""
TDSQL SQL审核工具 - 规则引擎基类

定义规则接口和数据结构。
"""
from abc import ABC, abstractmethod
from typing import Optional

from backend.engine.parser import ParsedSQL
from backend.models import RuleCategory, Severity, Violation


class BaseRule(ABC):
    """审核规则基类"""

    rule_id: str = ""               # 规则ID，如 "R001"
    category: RuleCategory = RuleCategory.DML  # 规则类别
    severity: Severity = Severity.ERROR        # 违规级别
    description: str = ""           # 规则描述
    enabled: bool = True            # 是否启用

    @abstractmethod
    def check(self, parsed: ParsedSQL) -> Optional[Violation]:
        """
        检查SQL是否违反此规则。

        Args:
            parsed: 解析后的SQL结构

        Returns:
            违规信息，通过则返回 None
        """
        pass

    def _make_violation(self, message: str, suggestion: Optional[str] = None,
                        line_number: Optional[int] = None) -> Violation:
        """创建违规记录"""
        return Violation(
            rule_id=self.rule_id,
            category=self.category,
            severity=self.severity,
            message=message,
            suggestion=suggestion,
            line_number=line_number,
        )
