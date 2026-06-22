"""
TDSQL SQL审核工具 - 规则引擎基类 (V1.0)

定义规则接口和数据结构，新增 spec_source 和 fix_suggestion 元数据字段。
"""
from abc import ABC, abstractmethod
from typing import Optional

from backend.engine.parser import ParsedSQL
from backend.models import RuleCategory, Severity, Violation


class BaseRule(ABC):
    """审核规则基类（V1.0）"""

    rule_id: str = ""               # 规则ID，如 "R001"
    category: RuleCategory = RuleCategory.DML  # 规则类别
    severity: Severity = Severity.ERROR        # 违规级别
    description: str = ""           # 规则描述
    enabled: bool = True            # 是否启用
    spec_source: str = ""           # 规范来源（V1.0新增）
    fix_suggestion: str = ""        # 修复建议模板（V1.0新增）

    @abstractmethod
    def check(self, parsed: ParsedSQL,
              table_metadata: Optional[dict] = None) -> Optional[Violation]:
        """
        检查SQL是否违反此规则。

        Args:
            parsed: 解析后的SQL结构
            table_metadata: 表元数据字典，key为表名，value为元数据字典。
                            可选，用于分布式规则获取真实分片键信息。
                            格式: {
                                "table_name": {
                                    "shard_key": "user_id",
                                    "is_shard_table": True,
                                    "indexes": [...]
                                }
                            }

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
            suggestion=suggestion or self.fix_suggestion,
            line_number=line_number,
        )
