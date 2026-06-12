"""
TDSQL SQL审核工具 - DDL规范规则 (R003-R011)

R003: 必须显式指定主键
R004: 必须指定存储引擎 engine=innodb
R005: 必须指定字符集 charset=utf8mb4
R006: 禁止使用 ENUM/SET 类型
R007: 禁止使用 TIMESTAMP 类型
R008: 禁止使用外键约束
R009: 财务字段禁止使用 FLOAT/DOUBLE
R010: VARCHAR 长度不超过设计需求（不超过 2000）
R011: 禁止在活跃表使用 TEXT/BLOB
"""
from typing import Optional

from backend.engine.parser import ParsedSQL
from backend.engine.rules.base import BaseRule
from backend.models import RuleCategory, Severity, Violation


class R003PrimaryKey(BaseRule):
    """R003: 必须显式指定主键"""

    rule_id = "R003"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "CREATE TABLE 必须显式指定主键"
    enabled = True

    def check(self, parsed: ParsedSQL) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        if not parsed.has_primary_key:
            return self._make_violation(
                "CREATE TABLE 未指定主键，TDSQL 要求每个表必须有主键",
                suggestion="建议添加自增主键: id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY",
            )
        return None


class R004Engine(BaseRule):
    """R004: 必须指定存储引擎 engine=innodb"""

    rule_id = "R004"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "必须指定存储引擎为 InnoDB"
    enabled = True

    def check(self, parsed: ParsedSQL) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        engine = parsed.engine
        if engine is None:
            return self._make_violation(
                "未指定存储引擎，TDSQL 要求使用 InnoDB 引擎",
                suggestion="请在 DDL 中添加: ENGINE=InnoDB",
            )
        if engine.upper() != "INNODB":
            return self._make_violation(
                f"存储引擎 '{engine}' 不符合规范，TDSQL 要求使用 InnoDB",
                suggestion="请修改为: ENGINE=InnoDB",
            )
        return None


class R005Charset(BaseRule):
    """R005: 必须指定字符集 charset=utf8mb4"""

    rule_id = "R005"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "必须指定字符集为 utf8mb4"
    enabled = True

    def check(self, parsed: ParsedSQL) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        charset = parsed.charset
        if charset is None:
            return self._make_violation(
                "未指定字符集，TDSQL 要求使用 utf8mb4",
                suggestion="请在 DDL 中添加: DEFAULT CHARSET=utf8mb4",
            )
        if charset.upper() not in ("UTF8MB4", "UTF8MB4_GENERAL_CI"):
            return self._make_violation(
                f"字符集 '{charset}' 不符合规范，TDSQL 要求使用 utf8mb4",
                suggestion="请修改为: DEFAULT CHARSET=utf8mb4",
            )
        return None


class R006EnumSetType(BaseRule):
    """R006: 禁止使用 ENUM/SET 类型"""

    rule_id = "R006"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "禁止使用 ENUM/SET 数据类型"
    enabled = True

    FORBIDDEN_TYPES = {"ENUM", "SET"}

    def check(self, parsed: ParsedSQL) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for col in parsed.column_types:
            col_type = col.get("type", "").upper()
            raw_type = col.get("raw_type", "").upper()
            if col_type in self.FORBIDDEN_TYPES or raw_type.startswith("ENUM") or raw_type.startswith("SET"):
                return self._make_violation(
                    f"字段 '{col['name']}' 使用了 {col_type or raw_type} 类型，TDSQL 禁止使用 ENUM/SET",
                    suggestion=f"建议将 '{col['name']}' 改为 VARCHAR 或 TINYINT 类型",
                )
        return None


class R007TimestampType(BaseRule):
    """R007: 禁止使用 TIMESTAMP 类型"""

    rule_id = "R007"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "禁止使用 TIMESTAMP 类型，建议使用 DATETIME"
    enabled = True

    def check(self, parsed: ParsedSQL) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for col in parsed.column_types:
            col_type = col.get("type", "").upper()
            raw_type = col.get("raw_type", "").upper()
            if col_type == "TIMESTAMP" or raw_type.startswith("TIMESTAMP"):
                return self._make_violation(
                    f"字段 '{col['name']}' 使用了 TIMESTAMP 类型，TDSQL 禁止使用",
                    suggestion=f"建议将 '{col['name']}' 改为 DATETIME 类型",
                )
        return None


class R008ForeignKey(BaseRule):
    """R008: 禁止使用外键约束"""

    rule_id = "R008"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "禁止使用外键约束，应用层保证数据一致性"
    enabled = True

    def check(self, parsed: ParsedSQL) -> Optional[Violation]:
        if not parsed.is_create_table and not parsed.is_alter_table:
            return None
        if parsed.has_foreign_key:
            return self._make_violation(
                "禁止使用外键约束，TDSQL 分布式架构下外键无法跨分片",
                suggestion="请移除外键约束，在应用层通过代码保证数据一致性",
            )
        return None


class R009FinanceFloatType(BaseRule):
    """R009: 财务字段禁止使用 FLOAT/DOUBLE"""

    rule_id = "R009"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "涉及金额/价格的字段禁止使用 FLOAT/DOUBLE，应使用 DECIMAL"
    enabled = True

    # 财务相关字段名关键词
    FINANCE_KEYWORDS = {
        "amount", "price", "cost", "fee", "balance", "money",
        "salary", "payment", "total", "discount", "rate", "commission",
        "revenue", "profit", "loss", "income", "expense",
        "金额", "价格", "余额", "费用", "费率", "佣金",
    }

    FLOAT_TYPES = {"FLOAT", "DOUBLE", "FLOAT4", "FLOAT8", "REAL"}

    def check(self, parsed: ParsedSQL) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for col in parsed.columns:
            col_type = col.get("type", "").upper()
            col_name_lower = col.get("name", "").lower()

            # 检查是否为浮点类型
            if col_type not in self.FLOAT_TYPES:
                raw_type = col.get("raw_type", "").upper()
                if not any(raw_type.startswith(t) for t in self.FLOAT_TYPES):
                    continue

            # 检查字段名是否包含财务关键词
            if any(kw in col_name_lower for kw in self.FINANCE_KEYWORDS):
                return self._make_violation(
                    f"财务字段 '{col['name']}' 禁止使用 {col_type} 类型，存在精度丢失风险",
                    suggestion=f"建议将 '{col['name']}' 改为 DECIMAL 类型，如 DECIMAL(18,2)",
                )
        return None


class R010VarcharLength(BaseRule):
    """R010: VARCHAR 长度不超过设计需求（不超过 2000）"""

    rule_id = "R010"
    category = RuleCategory.DDL
    severity = Severity.WARNING
    description = "VARCHAR 长度建议不超过 2000，超长字段请评估是否合理"
    enabled = True

    MAX_VARCHAR_LENGTH = 2000

    def check(self, parsed: ParsedSQL) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for col in parsed.columns:
            col_type = col.get("type", "").upper()
            raw_type = col.get("raw_type", "").upper()
            if col_type == "VARCHAR" or raw_type.startswith("VARCHAR"):
                length = col.get("length")
                if length and length > self.MAX_VARCHAR_LENGTH:
                    return self._make_violation(
                        f"字段 '{col['name']}' VARCHAR({length}) 超过建议长度 {self.MAX_VARCHAR_LENGTH}",
                        suggestion=f"请评估是否需要 {length} 个字符，超长字段建议使用 TEXT 类型",
                    )
        return None


class R011TextBlobType(BaseRule):
    """R011: 禁止在活跃表使用 TEXT/BLOB"""

    rule_id = "R011"
    category = RuleCategory.DDL
    severity = Severity.WARNING
    description = "禁止在活跃表使用 TEXT/BLOB 类型，影响性能"
    enabled = True

    LARGE_TYPES = {"TEXT", "TINYTEXT", "MEDIUMTEXT", "LONGTEXT", "BLOB", "TINYBLOB", "MEDIUMBLOB", "LONGBLOB", "JSON"}

    def check(self, parsed: ParsedSQL) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for col in parsed.column_types:
            col_type = col.get("type", "").upper()
            raw_type = col.get("raw_type", "").upper()
            if col_type in self.LARGE_TYPES or any(raw_type.startswith(t) for t in self.LARGE_TYPES):
                return self._make_violation(
                    f"字段 '{col['name']}' 使用了 {col_type or raw_type} 类型，活跃表不建议使用",
                    suggestion=(
                        f"建议: 1) 如需存储大文本，将 '{col['name']}' 拆分到独立扩展表; "
                        f"2) 或使用 VARCHAR 限定长度; 3) 评估是否可移除"
                    ),
                )
        return None
