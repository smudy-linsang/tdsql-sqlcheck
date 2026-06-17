"""
TDSQL SQL审核工具 - DML规范规则 (R012-R019)

R012: 禁止 SELECT *
R013: DML 必须带 WHERE 条件
R014: 禁止不带 WHERE 的 UPDATE/DELETE
R015: 禁止嵌套超过3层子查询
R016: WHERE 条件禁止函数/计算
R017: 禁止 ORDER BY RAND()
R018: 单表索引不超过5个
R019: 禁止冗余索引
"""
from typing import Optional, Dict

from backend.engine.parser import ParsedSQL
from backend.engine.rules.base import BaseRule
from backend.models import RuleCategory, Severity, Violation


class R012SelectStar(BaseRule):
    """R012: 禁止 SELECT *"""

    rule_id = "R012"
    category = RuleCategory.DML
    severity = Severity.ERROR
    description = "禁止使用 SELECT *，应指定具体字段"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type != "SELECT":
            return None
        if parsed.has_wildcard_select:
            tables = ", ".join(parsed.tables) if parsed.tables else "未知表"
            return self._make_violation(
                f"禁止使用 SELECT *（涉及表: {tables}），应指定具体字段",
                suggestion="请明确列出需要的字段，如: SELECT col1, col2, col3 FROM ...",
            )
        return None


class R013DmlWithoutWhere(BaseRule):
    """R013: DML 必须带 WHERE 条件"""

    rule_id = "R013"
    category = RuleCategory.DML
    severity = Severity.ERROR
    description = "UPDATE/DELETE/INSERT...SELECT 必须带 WHERE 条件"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        # 仅检查 UPDATE 和 DELETE
        if parsed.sql_type not in ("UPDATE", "DELETE"):
            return None
        if not parsed.has_where:
            action = "UPDATE" if parsed.sql_type == "UPDATE" else "DELETE"
            return self._make_violation(
                f"禁止不带 WHERE 条件的 {action} 操作，可能导致全表数据变更",
                suggestion=f"请添加 WHERE 条件限定影响范围，如: {action} ... WHERE id = ?",
            )
        return None


class R014UpdateDeleteWithoutWhere(BaseRule):
    """R014: 禁止不带 WHERE 的 UPDATE/DELETE（与 R013 相同场景，补充全表扫描警告）"""

    rule_id = "R014"
    category = RuleCategory.DML
    severity = Severity.ERROR
    description = "禁止不带 WHERE 的 UPDATE/DELETE，防止误操作全表"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        # 此规则与 R013 互补：R013 针对所有 DML，R014 额外强调 UPDATE/DELETE
        # 为避免重复报告，仅在 R013 未覆盖时启用
        # 这里我们让它独立检查，实际运行时 checker 会去重
        if parsed.sql_type not in ("UPDATE", "DELETE"):
            return None
        if not parsed.has_where:
            return self._make_violation(
                f"危险操作: 不带 WHERE 的 {parsed.sql_type} 将影响整张表",
                suggestion="请添加 WHERE 条件，或使用 LIMIT 限制影响行数",
            )
        return None


class R015NestedSubquery(BaseRule):
    """R015: 禁止嵌套超过3层子查询"""

    rule_id = "R015"
    category = RuleCategory.DML
    severity = Severity.ERROR
    description = "禁止嵌套超过3层子查询，影响可读性和性能"
    enabled = True

    MAX_SUBQUERY_DEPTH = 3

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type not in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            return None
        if parsed.subquery_depth > self.MAX_SUBQUERY_DEPTH:
            return self._make_violation(
                f"子查询嵌套深度为 {parsed.subquery_depth} 层，超过允许的 {self.MAX_SUBQUERY_DEPTH} 层",
                suggestion="建议将子查询改写为 JOIN 或临时表，降低嵌套层级",
            )
        return None


class R016FunctionInWhere(BaseRule):
    """R016: WHERE 条件禁止函数/计算/全模糊LIKE/OR（会导致索引失效）"""

    rule_id = "R016"
    category = RuleCategory.DML
    severity = Severity.WARNING
    description = "WHERE 条件中禁止函数计算、全模糊LIKE、OR条件，会导致索引失效"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.has_where:
            return None
        if parsed.where_has_function:
            return self._make_violation(
                "WHERE 条件中包含函数/计算/LIKE/OR条件，可能导致索引失效",
                suggestion=(
                    "1) 函数: WHERE create_time >= '2024-01-01' 替代 WHERE DATE(create_time) >= '2024-01-01'; "
                    "2) 全模糊LIKE: 改为前缀匹配 LIKE 'xxx%' 或使用全文索引; "
                    "3) OR条件: 改写为 UNION ALL（前提各字段有独立索引）"
                ),
            )
        return None


class R017OrderByRand(BaseRule):
    """R017: 禁止 ORDER BY RAND()"""

    rule_id = "R017"
    category = RuleCategory.DML
    severity = Severity.ERROR
    description = "禁止 ORDER BY RAND()，会导致全表扫描和临时表"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type != "SELECT":
            return None
        if parsed.order_by_random:
            return self._make_violation(
                "禁止使用 ORDER BY RAND()，会导致全表扫描和临时表排序，严重影响性能",
                suggestion=(
                    "替代方案: 1) 使用应用层随机: SELECT MAX(id) 获取最大ID后随机取值; "
                    "2) 使用子查询: SELECT * FROM table WHERE id >= (SELECT FLOOR(RAND() * (SELECT MAX(id) FROM table))) LIMIT 1; "
                    "3) 预生成随机列"
                ),
            )
        return None


class R018IndexCount(BaseRule):
    """R018: 单表索引不超过5个"""

    rule_id = "R018"
    category = RuleCategory.DML
    severity = Severity.WARNING
    description = "单表索引数量不超过5个（含主键）"
    enabled = True

    MAX_INDEX_COUNT = 5

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        index_count = len(parsed.indexes)
        # 主键也占一个索引
        if parsed.has_primary_key:
            index_count += 1
        if index_count > self.MAX_INDEX_COUNT:
            return self._make_violation(
                f"表索引数量为 {index_count}，超过建议的 {self.MAX_INDEX_COUNT} 个",
                suggestion="过多索引会降低写入性能，建议合并冗余索引或移除不常用索引",
            )
        return None


class R019RedundantIndex(BaseRule):
    """R019: 禁止冗余索引（前缀覆盖检查）"""

    rule_id = "R019"
    category = RuleCategory.DML
    severity = Severity.WARNING
    description = "禁止创建冗余索引，如果索引A的列是索引B列的前缀，则A冗余"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None

        indexes = parsed.indexes
        if len(indexes) < 2:
            return None

        # 提取索引列列表
        index_columns = []
        for idx in indexes:
            cols = idx.get("columns", [])
            if cols:
                index_columns.append((idx.get("name", ""), cols))

        # 检查前缀冗余
        for i, (name_i, cols_i) in enumerate(index_columns):
            for j, (name_j, cols_j) in enumerate(index_columns):
                if i == j:
                    continue
                # cols_i 是 cols_j 的前缀
                if len(cols_i) < len(cols_j) and cols_j[: len(cols_i)] == cols_i:
                    return self._make_violation(
                        f"索引 '{name_i}' ({','.join(cols_i)}) 是 '{name_j}' ({','.join(cols_j)}) 的前缀，存在冗余",
                        suggestion=f"建议移除冗余索引 '{name_i}'",
                    )
        return None
