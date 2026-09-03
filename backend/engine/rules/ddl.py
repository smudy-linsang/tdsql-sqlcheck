"""
TDSQL SQL审核工具 - DDL规范规则 (R003-R011, R023-R038)

V1.0: 共25条DDL规则，覆盖建表/修改表结构全规范。
"""
import re
from typing import Optional

from backend.engine.parser import ParsedSQL
from backend.engine.rules.base import BaseRule
from backend.models import RuleCategory, Severity, Violation, InstanceScope


# ═══════════════════════════════════════════════════════════════
# R003-R011: 现有规则（V1.0增强 spec_source / fix_suggestion）
# ═══════════════════════════════════════════════════════════════

class R003PrimaryKey(BaseRule):
    """R003: 必须显式指定主键"""
    rule_id = "R003"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "CREATE TABLE 必须显式指定主键"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 表设计规范"
    fix_suggestion = "建议添加自增主键: id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        if not parsed.has_primary_key:
            return self._make_violation(
                "CREATE TABLE 未指定主键，TDSQL 要求每个表必须有主键",
            )
        return None


class R004Engine(BaseRule):
    """R004: 必须指定存储引擎 engine=innodb"""
    rule_id = "R004"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "必须指定存储引擎为 InnoDB"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 表设计规范"
    fix_suggestion = "请在 DDL 中添加: ENGINE=InnoDB"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        engine = parsed.engine
        if engine is None:
            return self._make_violation("未指定存储引擎，TDSQL 要求使用 InnoDB 引擎")
        if engine.upper() != "INNODB":
            return self._make_violation(f"存储引擎 '{engine}' 不符合规范，TDSQL 要求使用 InnoDB")
        return None


class R005Charset(BaseRule):
    """R005: 必须指定字符集 charset=utf8mb4"""
    rule_id = "R005"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "必须指定字符集为 utf8mb4"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 表设计规范"
    fix_suggestion = "请在 DDL 中添加: DEFAULT CHARSET=utf8mb4"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        charset = parsed.charset
        if charset is None:
            return self._make_violation("未指定字符集，TDSQL 要求使用 utf8mb4")
        if charset.upper() not in ("UTF8MB4", "UTF8MB4_GENERAL_CI"):
            return self._make_violation(f"字符集 '{charset}' 不符合规范，TDSQL 要求使用 utf8mb4")
        return None


class R006EnumSetType(BaseRule):
    """R006: 禁止使用 ENUM/SET 类型"""
    rule_id = "R006"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "禁止使用 ENUM/SET 数据类型"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 列设计规范"
    fix_suggestion = "建议改为 VARCHAR 或 TINYINT 类型"

    FORBIDDEN_TYPES = {"ENUM", "SET"}

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for col in parsed.column_types:
            col_type = col.get("type", "").upper()
            raw_type = col.get("raw_type", "").upper()
            if col_type in self.FORBIDDEN_TYPES or raw_type.startswith("ENUM") or raw_type.startswith("SET"):
                return self._make_violation(
                    f"字段 '{col['name']}' 使用了 {col_type or raw_type} 类型，TDSQL 禁止使用 ENUM/SET",
                )
        return None


class R007TimestampType(BaseRule):
    """R007: 禁止使用 TIMESTAMP 类型"""
    rule_id = "R007"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "禁止使用 TIMESTAMP 类型，建议使用 DATETIME"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 列设计规范"
    fix_suggestion = "建议改为 DATETIME 类型"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for col in parsed.column_types:
            col_type = col.get("type", "").upper()
            raw_type = col.get("raw_type", "").upper()
            if col_type == "TIMESTAMP" or raw_type.startswith("TIMESTAMP"):
                return self._make_violation(
                    f"字段 '{col['name']}' 使用了 TIMESTAMP 类型，TDSQL 禁止使用",
                )
        return None


class R008ForeignKey(BaseRule):
    """R008: 禁止使用外键约束"""
    rule_id = "R008"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "禁止使用外键约束，应用层保证数据一致性"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 表设计规范"
    fix_suggestion = "请移除外键约束，在应用层通过代码保证数据一致性"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table and not parsed.is_alter_table:
            return None
        if parsed.has_foreign_key:
            return self._make_violation(
                "禁止使用外键约束，TDSQL 分布式架构下外键无法跨分片",
            )
        return None


class R009FinanceFloatType(BaseRule):
    """R009: 财务字段禁止使用 FLOAT/DOUBLE"""
    rule_id = "R009"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "涉及金额/价格的字段禁止使用 FLOAT/DOUBLE，应使用 DECIMAL"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 列设计规范"
    fix_suggestion = "建议改为 DECIMAL 类型，如 DECIMAL(18,2)"

    FINANCE_KEYWORDS = {
        "amount", "price", "cost", "fee", "balance", "money",
        "salary", "payment", "total", "discount", "rate", "commission",
        "revenue", "profit", "loss", "income", "expense",
    }
    FLOAT_TYPES = {"FLOAT", "DOUBLE", "FLOAT4", "FLOAT8", "REAL"}

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for col in parsed.columns:
            col_type = col.get("type", "").upper()
            col_name_lower = col.get("name", "").lower()
            if col_type not in self.FLOAT_TYPES:
                raw_type = col.get("raw_type", "").upper()
                if not any(raw_type.startswith(t) for t in self.FLOAT_TYPES):
                    continue
            if any(kw in col_name_lower for kw in self.FINANCE_KEYWORDS):
                return self._make_violation(
                    f"财务字段 '{col['name']}' 禁止使用 {col_type} 类型，存在精度丢失风险",
                )
        return None


class R010VarcharLength(BaseRule):
    """R010: VARCHAR 长度不超过2000"""
    rule_id = "R010"
    category = RuleCategory.DDL
    severity = Severity.WARNING
    description = "VARCHAR 长度建议不超过 2000，超长字段请评估是否合理"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 列设计规范"
    fix_suggestion = "请评估是否需要如此长度，超长字段建议使用 TEXT 类型"

    MAX_VARCHAR_LENGTH = 2000

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
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
                    )
        return None


class R011TextBlobType(BaseRule):
    """R011: 谨慎使用TEXT大对象字段（v1.6.3.2 收窄为仅 TEXT，级别 INFO）"""
    rule_id = "R011"
    category = RuleCategory.DDL
    severity = Severity.INFO
    description = "谨慎使用TEXT大对象字段"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 列设计规范（v1.6.3.2原厂专家定版）"
    fix_suggestion = "建议将大对象字段拆分到独立扩展表，或根据实际容量改用 VARCHAR(n) 并明确长度上限"

    # v1.6.3.2 / REQ-01：只匹配精确归一值 TEXT。TINYTEXT/MEDIUMTEXT/LONGTEXT/
    # 任何 BLOB/JSON 改由 R120 或不再覆盖（定版边界），不使用
    # endsWith/contains/startswith 等扩大匹配。
    TEXT_ONLY = {"TEXT"}

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        hits = []
        if parsed.is_create_table:
            sources = parsed.column_types
        elif parsed.is_alter_table:
            # v1.6.3.2 / REQ-01A：ALTER ADD/MODIFY/CHANGE 列类型通道
            sources = parsed.alter_column_types
        else:
            return None
        for col in sources:
            if (col.get("type") or "").upper() in self.TEXT_ONLY:
                hits.append(col.get("name", ""))
        if not hits:
            return None
        return self._make_violation(
            f"字段 {', '.join(hits)} 使用了 TEXT 数据类型，可能影响 TDSQL 的读写与存储性能。",
        )


class R120LobAbuse(BaseRule):
    """R120: 禁止滥用LOB大对象字段（v1.6.3.2 新增）"""
    rule_id = "R120"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "禁止滥用LOB大对象字段"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 列设计规范（v1.6.3.2原厂专家定版）"
    fix_suggestion = "不要使用上述大对象类型；非结构化数据建议对接影像平台或对象存储"

    # v1.6.3.2 / REQ-02：5 种受限 LOB 的精确归一集合。TINYTEXT/TINYBLOB/JSON
    # 不在其中（OUT-04）；列名 blob_url 或字符串 'LONGTEXT' 不得误命中。
    LOB_TYPES = {"BLOB", "MEDIUMTEXT", "LONGBLOB", "MEDIUMBLOB", "LONGTEXT"}

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.is_create_table:
            sources = parsed.column_types
        elif parsed.is_alter_table:
            sources = parsed.alter_column_types
        else:
            return None
        hits = []
        for col in sources:
            col_type = (col.get("type") or "").upper()
            if col_type in self.LOB_TYPES:
                display = col.get("raw_type") or col_type
                hits.append(f"{col.get('name', '')}({display})")
        if not hits:
            return None
        return self._make_violation(
            f"字段 {', '.join(hits)} 使用了受限 LOB 大对象类型；"
            "这些类型在 TDSQL 上会大幅降低性能，禁止使用。",
        )


# ═══════════════════════════════════════════════════════════════
# R023-R038: 新增DDL规则
# ═══════════════════════════════════════════════════════════════

class R023NoCreateTableSelect(BaseRule):
    """R023: 禁CREATE TABLE...SELECT"""
    rule_id = "R023"
    instance_scope = InstanceScope.DISTRIBUTED
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "禁止使用CREATE TABLE ... SELECT语句，TDSQL分布式不支持"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DDL规范"
    fix_suggestion = "请拆分为CREATE TABLE + INSERT INTO ... SELECT两步执行"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.is_create_table_select:
            return self._make_violation(
                "检测到CREATE TABLE ... SELECT语句，TDSQL分布式实例不支持此语法",
            )
        return None


class R024NoTemporaryTable(BaseRule):
    """R024: 禁临时表"""
    rule_id = "R024"
    instance_scope = InstanceScope.DISTRIBUTED
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "禁止使用CREATE TEMPORARY TABLE，分布式实例不支持"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DDL规范"
    fix_suggestion = "请使用普通表或应用内存缓存替代临时表"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.is_temporary_table:
            return self._make_violation(
                "检测到CREATE TEMPORARY TABLE语句，TDSQL分布式实例不支持临时表",
            )
        return None


class R025NoAlterShardKey(BaseRule):
    """R025: 禁修改分片键"""
    rule_id = "R025"
    instance_scope = InstanceScope.DISTRIBUTED
    category = RuleCategory.DISTRIBUTED
    severity = Severity.ERROR
    description = "禁止通过ALTER TABLE修改分片键字段"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "分片键不可修改，如需变更请联系DBA通过数据迁移完成"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_alter_table:
            return None
        if not table_metadata:
            return None
        for table in parsed.tables:
            meta = table_metadata.get(table, {})
            shard_key = meta.get("shard_key", "")
            if shard_key:
                for action in parsed.alter_actions:
                    if action.get("column", "").lower() == shard_key.lower():
                        return self._make_violation(
                            f"禁止通过ALTER TABLE修改分片键字段 '{shard_key}'",
                        )
        return None


class R026NoColumnShrink(BaseRule):
    """R026: 禁缩短字段长度"""
    rule_id = "R026"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "ALTER TABLE修改字段时禁止缩短字段长度，可能导致数据截断"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DDL规范"
    fix_suggestion = "如需缩短字段长度，请先确认无超长数据，并通过DBA审核"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_alter_table:
            return None
        raw_lower = parsed.raw_sql.lower()
        if "modify" in raw_lower or "change" in raw_lower:
            # 检测ALTER MODIFY/CHANGE操作，提示潜在缩短风险
            return self._make_violation(
                "ALTER TABLE MODIFY/CHANGE可能缩短字段长度，请确认无数据截断风险",
            )
        return None


class R027NoDropDatabase(BaseRule):
    """R027: 禁DROP DATABASE"""
    rule_id = "R027"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "禁止使用DROP DATABASE/SCHEMA语句"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DDL规范"
    fix_suggestion = "如需清理数据库请联系DBA处理"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.has_drop_database:
            return self._make_violation(
                "禁止使用DROP DATABASE/SCHEMA语句，此操作不可逆",
            )
        return None


class R028TableMustHaveComment(BaseRule):
    """R028: 表必须有COMMENT"""
    rule_id = "R028"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "建表必须显式指定表级别COMMENT"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 表设计规范"
    fix_suggestion = "请添加 COMMENT '表用途说明' 到CREATE TABLE语句中"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        if not parsed.has_table_comment:
            table_name = parsed.tables[0] if parsed.tables else "未知表"
            return self._make_violation(f"表 {table_name} 缺少表级别COMMENT")
        return None


class R029ColumnMustHaveComment(BaseRule):
    """R029: 列必须有COMMENT"""
    rule_id = "R029"
    category = RuleCategory.DDL
    severity = Severity.WARNING
    description = "建表时每个字段应显式指定COMMENT"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 列设计规范"
    fix_suggestion = "请为每个字段添加COMMENT，如: col_name INT COMMENT '字段说明'"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        missing_cols = []
        for col in parsed.columns:
            col_name = col.get("name", "")
            if col_name and col_name not in parsed.column_comments:
                missing_cols.append(col_name)
        if missing_cols:
            cols_str = ", ".join(f"'{c}'" for c in missing_cols)
            return self._make_violation(
                f"字段 {cols_str} 缺少COMMENT注释",
            )
        return None


class R030NoViewProcTrigger(BaseRule):
    """R030: 禁视图/存储过程/触发器（v1.6.3.2 仅分布式适用）"""
    rule_id = "R030"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    # v1.6.3.2 / REQ-03：腾讯云 TDSQL MySQL 版使用限制把自定义函数/存储过程/
    # 触发器列为分布式限制；集中式改域后对视图/过程/触发器为零覆盖（RISK-16，
    # 需 DBA 书面确认），R031 对自定义函数的残余覆盖保持原状（OUT-01）。
    instance_scope = InstanceScope.DISTRIBUTED
    description = "禁止使用视图、存储过程、触发器、自定义函数"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DDL规范"
    fix_suggestion = "请在应用层实现相应逻辑"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        clean_sql = re.sub(r"--[^\n]*", "", parsed.raw_sql)
        clean_sql = re.sub(r"/\*.*?\*/", "", clean_sql, flags=re.DOTALL).lower().strip()
        if re.search(r"\bcreate\s+(?:or\s+replace\s+)?(?:definer\s*=\s*\S+\s+)?(view|procedure|function|trigger)\b", clean_sql):
            return self._make_violation(
                "禁止创建视图/存储过程/触发器/自定义函数，TDSQL分布式架构下不推荐使用",
            )
        return None


class R031NoCustomFunction(BaseRule):
    """R031: 禁自定义函数"""
    rule_id = "R031"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "禁止创建自定义函数"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DDL规范"
    fix_suggestion = "请在应用层实现函数逻辑"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        clean_sql = re.sub(r"--[^\n]*", "", parsed.raw_sql)
        clean_sql = re.sub(r"/\*.*?\*/", "", clean_sql, flags=re.DOTALL).lower().strip()
        if re.search(r"\bcreate\s+(?:or\s+replace\s+)?(?:definer\s*=\s*\S+\s+)?function\b", clean_sql):
            return self._make_violation("禁止创建自定义函数，请在应用层实现")
        return None


class R032NoTemporaryTableRule(BaseRule):
    """R032: 禁临时表(R024补充)（v1.6.3.2 仅分布式适用）"""
    rule_id = "R032"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    # v1.6.3.2 / REQ-04：TDSQL 分布式官方限制不支持 CREATE TEMPORARY TABLE；
    # 与 R024 同域后集中式对临时表为零覆盖（RISK-16，需 DBA 书面确认）。
    instance_scope = InstanceScope.DISTRIBUTED
    description = "禁止使用临时表进行复杂业务逻辑"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DDL规范"
    fix_suggestion = "请使用应用层内存缓存或普通表替代"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.is_temporary_table:
            return self._make_violation("禁止使用临时表，分布式环境下影响性能")
        return None


class R033NoPluralTableName(BaseRule):
    """R033: 表名禁复数"""
    rule_id = "R033"
    category = RuleCategory.NAMING
    severity = Severity.WARNING
    description = "表名建议使用单数形式"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 命名规范"
    fix_suggestion = "建议将表名改为单数形式"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        if parsed.table_name_plural:
            table_name = parsed.tables[0] if parsed.tables else ""
            return self._make_violation(
                f"表名 '{table_name}' 为复数形式，建议使用单数",
            )
        return None


class R034BackupTableNaming(BaseRule):
    """R034: 备份表命名规范"""
    rule_id = "R034"
    category = RuleCategory.NAMING
    severity = Severity.WARNING
    description = "备份表命名必须以_bak_或_bk_为前缀，并附日期后缀"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 命名规范"
    fix_suggestion = "备份表命名格式: 原表名_bak_YYYYMMDD"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for table in parsed.tables:
            table_lower = table.lower().strip('`"')
            if "bak" in table_lower or "backup" in table_lower or "_bk_" in table_lower:
                # 检查是否有日期后缀
                if not re.search(r"\d{8}$", table_lower):
                    return self._make_violation(
                        f"备份表 '{table}' 命名缺少日期后缀(YYYYMMDD)",
                    )
        return None


class R035CrossTableFieldType(BaseRule):
    """R035: 跨表关联字段类型必须一致（v1.6.3.2 不再比较长度等括号参数）"""
    rule_id = "R035"
    category = RuleCategory.DDL
    severity = Severity.ERROR
    description = "跨表关联字段类型必须一致"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 列设计规范"
    fix_suggestion = "请统一关联字段的数据类型；字段长度可按各表实际容量分别设置"

    # v1.6.3.2 / REQ-05A：批内跨表上下文保留键。由 RuleChecker 在单次审核
    # 请求内构造（只读、请求结束释放）；其他规则不得消费它。
    CROSS_KEY = "__r035_cross_table_columns__"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        meta = table_metadata or {}
        index = meta.get(self.CROSS_KEY)
        if not isinstance(index, dict) or not index:
            # 单条离线 SQL 无第二张表/实时库上下文：明确跳过，不伪造违规，
            # 也不把"没有证据"描述为"检查通过"。
            return None
        current_table = parsed.tables[0] if parsed.tables else ""
        for col in parsed.columns:
            name = col.get("name", "")
            cur_type = col.get("type") or ""
            cur_raw = col.get("raw_type") or cur_type
            refs = index.get(name) or index.get(name.lower()) or []
            for ref in refs:
                ref_table = ref.get("table_name", "")
                if not ref_table or ref_table == current_table:
                    continue                     # 同一表内部不做跨表规则
                ref_type = ref.get("type") or ""
                if ref_type != cur_type:
                    ref_raw = ref.get("raw_type") or ref_type
                    return self._make_violation(
                        f"关联字段 {name} 在表 {current_table} 中的类型为 {cur_raw}，"
                        f"与表 {ref_table} 中的类型 {ref_raw} 不一致。",
                        suggestion=f"请统一关联字段 {name} 的数据类型；"
                                   "字段长度可按各表实际容量分别设置。",
                    )
        return None


class R036SuggestTimestampColumns(BaseRule):
    """R036: 建议时间戳列"""
    rule_id = "R036"
    category = RuleCategory.DDL
    severity = Severity.INFO
    description = "建议每张表包含create_time和update_time字段"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 列设计规范"
    fix_suggestion = "建议添加: create_time DATETIME DEFAULT CURRENT_TIMESTAMP, update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        # 列信息缺失（如 TDSQL BROADCAST 等方言语法致 sqlglot 降级为 Command）时
        # 无判定依据，不做建议——避免对实际含 create_time/update_time 的表误报
        if not parsed.columns:
            return None
        col_names = {c.get("name", "").lower() for c in parsed.columns}
        if "create_time" not in col_names or "update_time" not in col_names:
            return self._make_violation(
                "建议表包含 create_time 和 update_time 字段用于数据追踪",
            )
        return None


class R037SuggestLogicalDelete(BaseRule):
    """R037: 建议逻辑删除"""
    rule_id = "R037"
    category = RuleCategory.DDL
    severity = Severity.INFO
    description = "建议使用逻辑删除(is_deleted)替代物理删除(DELETE)"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DML规范"
    fix_suggestion = "建议添加: is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除标记'"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        # 列信息缺失时无判定依据，不做建议（同 R036，避免解析降级时误报）
        if not parsed.columns:
            return None
        col_names = {c.get("name", "").lower() for c in parsed.columns}
        delete_flags = {"is_deleted", "is_del", "deleted", "del_flag", "status"}
        if not col_names.intersection(delete_flags):
            return self._make_violation(
                "建议添加逻辑删除字段(如 is_deleted)替代物理删除",
            )
        return None


class R038NoAutoIncrementForLargeTable(BaseRule):
    """R038: 大表禁自增主键"""
    rule_id = "R038"
    category = RuleCategory.DDL
    severity = Severity.WARNING
    description = "预期数据量超千万的表不建议使用AUTO_INCREMENT主键：自增锁在高并发写入下形成瓶颈，且分库分表/数据迁移/多源合并时主键易冲突"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 表设计规范"
    fix_suggestion = "大表建议使用业务主键或全局唯一ID生成器（雪花算法等）；分布式实例还需保证该主键包含分片键"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for col in parsed.columns:
            raw_type = col.get("raw_type", "").lower()
            if "auto_increment" in raw_type:
                # 仅在表名包含log/detail/record等暗示大表时告警
                table_name = (parsed.tables[0] if parsed.tables else "").lower()
                large_hints = {"log", "detail", "record", "history", "flow", "trace"}
                if any(hint in table_name for hint in large_hints):
                    return self._make_violation(
                        f"表 '{parsed.tables[0]}' 疑似大表，不建议使用AUTO_INCREMENT主键",
                        suggestion="大表建议使用业务主键或全局唯一ID生成器（雪花算法等）",
                    )
        return None
