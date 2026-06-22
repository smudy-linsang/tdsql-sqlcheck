"""
TDSQL SQL审核工具 - DML规范规则 (R012-R019, R039-R052)

V1.0: 共22条DML规则，覆盖查询/更新/删除全规范。
"""
import re
from typing import Optional

from backend.engine.parser import ParsedSQL
from backend.engine.rules.base import BaseRule
from backend.models import RuleCategory, Severity, Violation


# ═══════════════════════════════════════════════════════════════
# R012-R019: 现有规则（V1.0增强 spec_source / fix_suggestion）
# ═══════════════════════════════════════════════════════════════

class R012SelectStar(BaseRule):
    """R012: 禁止 SELECT *"""
    rule_id = "R012"
    category = RuleCategory.DML
    severity = Severity.ERROR
    description = "禁止使用 SELECT *，应指定具体字段"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DML规范"
    fix_suggestion = "请明确列出需要的字段，如: SELECT col1, col2, col3 FROM ..."

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type != "SELECT":
            return None
        if parsed.has_wildcard_select:
            tables = ", ".join(parsed.tables) if parsed.tables else "未知表"
            return self._make_violation(f"禁止使用 SELECT *（涉及表: {tables}），应指定具体字段")
        return None


class R013DmlWithoutWhere(BaseRule):
    """R013: DML 必须带 WHERE 条件"""
    rule_id = "R013"
    category = RuleCategory.DML
    severity = Severity.ERROR
    description = "UPDATE/DELETE/INSERT...SELECT 必须带 WHERE 条件"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DML规范"
    fix_suggestion = "请添加 WHERE 条件限定影响范围"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type not in ("UPDATE", "DELETE"):
            return None
        if not parsed.has_where:
            action = "UPDATE" if parsed.sql_type == "UPDATE" else "DELETE"
            return self._make_violation(f"禁止不带 WHERE 条件的 {action} 操作，可能导致全表数据变更")
        return None


class R014UpdateDeleteWithoutWhere(BaseRule):
    """R014: 禁止不带 WHERE 的 UPDATE/DELETE"""
    rule_id = "R014"
    category = RuleCategory.DML
    severity = Severity.ERROR
    description = "禁止不带 WHERE 的 UPDATE/DELETE，防止误操作全表"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DML规范"
    fix_suggestion = "请添加 WHERE 条件，或使用 LIMIT 限制影响行数"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type not in ("UPDATE", "DELETE"):
            return None
        if not parsed.has_where:
            return self._make_violation(f"危险操作: 不带 WHERE 的 {parsed.sql_type} 将影响整张表")
        return None


class R015NestedSubquery(BaseRule):
    """R015: 禁止嵌套超过3层子查询"""
    rule_id = "R015"
    category = RuleCategory.DML
    severity = Severity.ERROR
    description = "禁止嵌套超过3层子查询，影响可读性和性能"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DML规范"
    fix_suggestion = "建议将子查询改写为 JOIN 或临时表，降低嵌套层级"

    MAX_SUBQUERY_DEPTH = 3

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type not in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            return None
        if parsed.subquery_depth > self.MAX_SUBQUERY_DEPTH:
            return self._make_violation(
                f"子查询嵌套深度为 {parsed.subquery_depth} 层，超过允许的 {self.MAX_SUBQUERY_DEPTH} 层",
            )
        return None


class R016FunctionInWhere(BaseRule):
    """R016: WHERE 条件禁止函数/计算"""
    rule_id = "R016"
    category = RuleCategory.DML
    severity = Severity.WARNING
    description = "WHERE 条件中禁止函数计算、全模糊LIKE、OR条件，会导致索引失效"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 性能规范"
    fix_suggestion = "1)函数:用范围查询替代 2)全模糊LIKE:改为前缀匹配 3)OR:改写为UNION ALL"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.has_where:
            return None
        if parsed.where_has_function:
            return self._make_violation(
                "WHERE 条件中包含函数/计算/LIKE/OR条件，可能导致索引失效",
            )
        return None


class R017OrderByRand(BaseRule):
    """R017: 禁止 ORDER BY RAND()"""
    rule_id = "R017"
    category = RuleCategory.DML
    severity = Severity.ERROR
    description = "禁止 ORDER BY RAND()，会导致全表扫描和临时表"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DML规范"
    fix_suggestion = "替代方案: 使用应用层随机或预生成随机列"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type != "SELECT":
            return None
        if parsed.order_by_random:
            return self._make_violation("禁止使用 ORDER BY RAND()，会导致全表扫描和临时表排序")
        return None


class R018IndexCount(BaseRule):
    """R018: 单表索引不超过5个"""
    rule_id = "R018"
    category = RuleCategory.INDEX
    severity = Severity.WARNING
    description = "单表索引数量不超过5个（含主键）"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 索引设计规范"
    fix_suggestion = "过多索引会降低写入性能，建议合并冗余索引或移除不常用索引"

    MAX_INDEX_COUNT = 5

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        index_count = len(parsed.indexes)
        if parsed.has_primary_key:
            index_count += 1
        if index_count > self.MAX_INDEX_COUNT:
            return self._make_violation(
                f"表索引数量为 {index_count}，超过建议的 {self.MAX_INDEX_COUNT} 个",
            )
        return None


class R019RedundantIndex(BaseRule):
    """R019: 禁止冗余索引"""
    rule_id = "R019"
    category = RuleCategory.INDEX
    severity = Severity.WARNING
    description = "禁止创建冗余索引，如果索引A的列是索引B列的前缀，则A冗余"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 索引设计规范"
    fix_suggestion = "建议移除冗余索引"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        indexes = parsed.indexes
        if len(indexes) < 2:
            return None
        index_columns = [(idx.get("name", ""), idx.get("columns", [])) for idx in indexes]
        for i, (name_i, cols_i) in enumerate(index_columns):
            for j, (name_j, cols_j) in enumerate(index_columns):
                if i == j:
                    continue
                if len(cols_i) < len(cols_j) and cols_j[:len(cols_i)] == cols_i:
                    return self._make_violation(
                        f"索引 '{name_i}' ({','.join(cols_i)}) 是 '{name_j}' ({','.join(cols_j)}) 的前缀，存在冗余",
                    )
        return None


# ═══════════════════════════════════════════════════════════════
# R039-R052: 新增DML规则
# ═══════════════════════════════════════════════════════════════

class R039NoIntoOutfile(BaseRule):
    """R039: 禁INTO OUTFILE"""
    rule_id = "R039"
    category = RuleCategory.SECURITY
    severity = Severity.ERROR
    description = "禁止使用SELECT ... INTO OUTFILE/DUMPFILE"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 安全规范"
    fix_suggestion = "请在应用层导出数据，禁止数据库直接写文件"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.has_into_outfile:
            return self._make_violation("禁止使用SELECT ... INTO OUTFILE/DUMPFILE，存在安全风险")
        return None


class R040NoDelayedLowPriority(BaseRule):
    """R040: 禁DELAYED/LOW_PRIORITY"""
    rule_id = "R040"
    category = RuleCategory.DML
    severity = Severity.WARNING
    description = "禁止使用DELAYED/LOW_PRIORITY关键字"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DML规范"
    fix_suggestion = "请移除DELAYED/LOW_PRIORITY关键字"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.has_delayed_keyword:
            return self._make_violation("禁止使用DELAYED/LOW_PRIORITY关键字，TDSQL不支持")
        return None


class R041NoUnnamedInsert(BaseRule):
    """R041: 禁不带列名的INSERT/REPLACE"""
    rule_id = "R041"
    category = RuleCategory.DML
    severity = Severity.ERROR
    description = "INSERT/REPLACE语句必须显式指定列名"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DML规范"
    fix_suggestion = "请改为 INSERT INTO table_name(col1, col2, ...) VALUES(...)"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.has_unnamed_insert:
            return self._make_violation("INSERT/REPLACE语句未指定列名，请显式指定列名列表")
        return None


class R042NoLoadData(BaseRule):
    """R042: 禁LOAD DATA"""
    rule_id = "R042"
    category = RuleCategory.SECURITY
    severity = Severity.ERROR
    description = "禁止使用LOAD DATA INFILE/LOAD XML"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 安全规范"
    fix_suggestion = "请在应用层批量导入数据"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.has_load_data:
            return self._make_violation("禁止使用LOAD DATA INFILE/LOAD XML，存在安全风险")
        return None


class R043NoMultiTableUpdate(BaseRule):
    """R043: 禁联表更新"""
    rule_id = "R043"
    category = RuleCategory.DML
    severity = Severity.ERROR
    description = "禁止使用多表联表UPDATE/DELETE"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DML规范【分布式】"
    fix_suggestion = "请拆分为单表操作，在应用层维护数据一致性"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.is_multi_table_update:
            return self._make_violation("禁止使用多表联表UPDATE，分布式环境下可能导致跨SET操作")
        return None


class R044NoIndexHint(BaseRule):
    """R044: 禁INDEX HINT"""
    rule_id = "R044"
    category = RuleCategory.PERFORMANCE
    severity = Severity.WARNING
    description = "禁止使用USE INDEX/FORCE INDEX/IGNORE INDEX等索引提示"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 性能规范"
    fix_suggestion = "请优化SQL和索引设计，不要强制指定索引"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.has_index_hint:
            return self._make_violation("禁止使用索引提示(USE/FORCE/IGNORE INDEX)，应通过优化SQL解决")
        return None


class R045NoHandlerDo(BaseRule):
    """R045: 禁HANDLER/DO"""
    rule_id = "R045"
    category = RuleCategory.SECURITY
    severity = Severity.ERROR
    description = "禁止使用HANDLER语句"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 安全规范"
    fix_suggestion = "请使用标准SELECT语句替代HANDLER"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.has_handler_do:
            return self._make_violation("禁止使用HANDLER语句，TDSQL不支持")
        return None


class R046NoFlushLockTable(BaseRule):
    """R046: 禁FLUSH/LOCK TABLES"""
    rule_id = "R046"
    category = RuleCategory.SECURITY
    severity = Severity.ERROR
    description = "禁止使用FLUSH和LOCK TABLES语句"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 安全规范"
    fix_suggestion = "DBA运维操作请通过管理工具完成"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.has_flush:
            return self._make_violation("禁止使用FLUSH语句，仅限DBA运维操作")
        if parsed.has_lock_tables:
            return self._make_violation("禁止使用LOCK TABLES语句，TDSQL分布式不支持")
        return None


class R047DeleteAllUseTruncate(BaseRule):
    """R047: 全表删除建议TRUNCATE"""
    rule_id = "R047"
    category = RuleCategory.PERFORMANCE
    severity = Severity.WARNING
    description = "全表删除数据时建议使用TRUNCATE替代DELETE"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DML规范"
    fix_suggestion = "全表删除请使用 TRUNCATE TABLE table_name"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type != "DELETE":
            return None
        if not parsed.has_where:
            return self._make_violation(
                "全表DELETE建议改为TRUNCATE TABLE，效率更高且释放空间",
                suggestion="请使用 TRUNCATE TABLE table_name 替代 DELETE FROM table_name",
            )
        return None


class R048InsertMustIncludeShardKey(BaseRule):
    """R048: INSERT必须包含分片键"""
    rule_id = "R048"
    category = RuleCategory.DISTRIBUTED
    severity = Severity.ERROR
    description = "分布式实例执行INSERT/REPLACE时，字段列表必须包含分片键"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DML规范【分布式】"
    fix_suggestion = "请在字段列表中添加分片键字段"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type not in ("INSERT", "REPLACE"):
            return None
        if not parsed.insert_columns or not table_metadata:
            return None
        for table in parsed.tables:
            meta = table_metadata.get(table, {})
            shard_key = meta.get("shard_key")
            if shard_key and shard_key not in parsed.insert_columns:
                return self._make_violation(
                    f"INSERT语句未包含分片键字段 '{shard_key}'",
                    suggestion=f"请在字段列表中添加分片键 '{shard_key}'",
                )
        return None


class R049DifferentAliasForTables(BaseRule):
    """R049: 表别名规范"""
    rule_id = "R049"
    category = RuleCategory.NAMING
    severity = Severity.INFO
    description = "多表关联时建议为每个表指定不同别名"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - DML规范"
    fix_suggestion = "请为每个表指定有意义的唯一别名"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if len(parsed.tables) < 2:
            return None
        # 简单检测：FROM/JOIN后是否有别名（sqlglot解析后别名信息较难提取，此处简化）
        return None


class R050InListSize(BaseRule):
    """R050: IN列表不超过200"""
    rule_id = "R050"
    category = RuleCategory.PERFORMANCE
    severity = Severity.WARNING
    description = "IN列表元素数量建议不超过200，过多会导致解析变慢"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 性能规范"
    fix_suggestion = "IN列表过多时建议使用临时表关联或分批查询"

    MAX_IN_LIST = 200

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.in_list_size > self.MAX_IN_LIST:
            return self._make_violation(
                f"IN列表包含 {parsed.in_list_size} 个元素，超过建议的 {self.MAX_IN_LIST} 个",
            )
        return None


class R051NoSelectWithoutWhere(BaseRule):
    """R051: 禁无WHERE的SELECT"""
    rule_id = "R051"
    category = RuleCategory.PERFORMANCE
    severity = Severity.WARNING
    description = "SELECT语句建议包含WHERE条件，避免全表扫描"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 性能规范"
    fix_suggestion = "请添加WHERE条件限制查询范围"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type != "SELECT":
            return None
        if not parsed.has_where and not parsed.has_order_by:
            return self._make_violation("SELECT语句无WHERE条件，将导致全表扫描")
        return None


class R052NoImplicitTypeCast(BaseRule):
    """R052: WHERE条件禁隐式类型转换"""
    rule_id = "R052"
    category = RuleCategory.PERFORMANCE
    severity = Severity.ERROR
    description = "WHERE条件等号两侧字段类型必须一致，禁止隐式类型转换"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 性能规范"
    fix_suggestion = "请确保WHERE条件等号两侧类型一致"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        # 静态检测：WHERE中字符串字段与数字比较（简化版）
        if not parsed.has_where or not parsed.where_clause:
            return None
        where_text = parsed.where_clause
        # 检测形如 varchar_col = 123 的模式（字段名=数字）
        if re.search(r"[a-zA-Z_]\w*\s*=\s*\d+\s*(?!['])", where_text):
            # 如果字段名看起来是varchar类型（包含name/code/title等关键词）
            match = re.search(r"([a-zA-Z_]\w*(?:name|code|title|status|type|key|id_str))\s*=\s*\d+", where_text, re.IGNORECASE)
            if match:
                return self._make_violation(
                    f"WHERE条件中字段 '{match.group(1)}' 疑似存在隐式类型转换，可能导致索引失效",
                )
        return None
