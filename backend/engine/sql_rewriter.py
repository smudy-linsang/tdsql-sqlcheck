"""
TDSQL SQL审核工具 - SQL改写引擎 (V1.0)

自动生成SQL改写建议：SELECT*改写、深分页、子查询→JOIN、OR→UNION ALL。
"""
import re
from typing import Optional

from backend.models import RewriteSuggestion


class SQLRewriter:
    """SQL改写建议引擎"""

    def rewrite(self, sql: str, table_metadata: Optional[dict] = None) -> list[RewriteSuggestion]:
        """生成SQL改写建议"""
        suggestions = []
        suggestions.extend(self._rewrite_select_star(sql, table_metadata))
        suggestions.extend(self._rewrite_deep_pagination(sql))
        suggestions.extend(self._rewrite_or_to_union(sql))
        suggestions.extend(self._rewrite_subquery_to_join(sql))
        return suggestions

    def _rewrite_select_star(self, sql: str, table_metadata: Optional[dict] = None) -> list[RewriteSuggestion]:
        """SELECT * 改写"""
        results = []
        if not re.search(r"SELECT\s+\*", sql, re.IGNORECASE):
            return results

        # 提取表名
        from_match = re.search(r"\bFROM\s+([a-zA-Z_][a-zA-Z0-9_.]*)", sql, re.IGNORECASE)
        if not from_match:
            return results
        table = from_match.group(1).strip("`\"")

        # 如果有元数据，列出具体字段
        if table_metadata and table in table_metadata:
            columns = table_metadata[table].get("columns", [])
            if columns:
                col_list = ", ".join(columns[:10])
                rewritten = re.sub(r"SELECT\s+\*", f"SELECT {col_list}", sql, count=1, flags=re.IGNORECASE)
                results.append(RewriteSuggestion(
                    type="select_star",
                    original_sql=sql,
                    rewritten_sql=rewritten,
                    reason="SELECT *无法使用覆盖索引，增加IO和网络开销",
                    expected_benefit="减少不必要字段传输，可能触发覆盖索引",
                ))
        else:
            results.append(RewriteSuggestion(
                type="select_star",
                original_sql=sql,
                rewritten_sql="-- 请替换SELECT *为具体字段列表",
                reason="SELECT *应替换为明确字段列表",
                expected_benefit="减少IO，可能触发覆盖索引",
            ))
        return results

    def _rewrite_deep_pagination(self, sql: str) -> list[RewriteSuggestion]:
        """深分页改写"""
        results = []
        # 检测 LIMIT offset, count 且 offset > 10000
        limit_match = re.search(r"LIMIT\s+(\d+)\s*,\s*(\d+)", sql, re.IGNORECASE)
        if limit_match:
            offset = int(limit_match.group(1))
            count = int(limit_match.group(2))
            if offset > 10000:
                # 尝试改写为游标分页
                order_match = re.search(r"ORDER\s+BY\s+(\w+)", sql, re.IGNORECASE)
                if order_match:
                    order_col = order_match.group(1)
                    rewritten = re.sub(
                        r"LIMIT\s+\d+\s*,\s*\d+",
                        f"-- 游标分页: WHERE {order_col} > <last_id> ORDER BY {order_col} LIMIT {count}",
                        sql, flags=re.IGNORECASE
                    )
                else:
                    rewritten = f"-- 建议添加ORDER BY主键并使用游标分页: WHERE id > <last_id> ORDER BY id LIMIT {count}"
                results.append(RewriteSuggestion(
                    type="deep_pagination",
                    original_sql=sql,
                    rewritten_sql=rewritten,
                    reason=f"LIMIT偏移量{offset}过大，MySQL需扫描{offset + count}行后丢弃前{offset}行",
                    expected_benefit="游标分页避免OFFSET扫描，性能从O(N)降为O(1)",
                ))
        return results

    def _rewrite_or_to_union(self, sql: str) -> list[RewriteSuggestion]:
        """OR → UNION ALL 改写"""
        results = []
        if not re.search(r"\bWHERE\b.*\bOR\b", sql, re.IGNORECASE):
            return results
        # 仅对SELECT且WHERE中有OR的情况建议
        if not sql.strip().upper().startswith("SELECT"):
            return results
        results.append(RewriteSuggestion(
            type="or_to_union",
            original_sql=sql,
            rewritten_sql="-- 将 WHERE col1=x OR col2=y 拆分为: SELECT ... WHERE col1=x UNION ALL SELECT ... WHERE col2=y",
            reason="OR条件可能导致索引失效，拆分为UNION ALL可分别利用各自索引",
            expected_benefit="各子查询独立走索引，避免全表扫描",
        ))
        return results

    def _rewrite_subquery_to_join(self, sql: str) -> list[RewriteSuggestion]:
        """子查询 → JOIN 改写"""
        results = []
        # 检测 IN (SELECT ...) 子查询
        if re.search(r"\bIN\s*\(\s*SELECT\b", sql, re.IGNORECASE):
            results.append(RewriteSuggestion(
                type="subquery_to_join",
                original_sql=sql,
                rewritten_sql="-- 将 IN (SELECT ...) 改写为 INNER JOIN",
                reason="IN子查询可能被优化器当作相关子查询执行，改写为JOIN更高效",
                expected_benefit="JOIN方式优化器可选择更优执行计划",
            ))
        # 检测 NOT IN (SELECT ...)
        if re.search(r"\bNOT\s+IN\s*\(\s*SELECT\b", sql, re.IGNORECASE):
            results.append(RewriteSuggestion(
                type="not_in_to_left_join",
                original_sql=sql,
                rewritten_sql="-- 将 NOT IN (SELECT ...) 改写为 LEFT JOIN ... WHERE ... IS NULL",
                reason="NOT IN子查询性能差且对NULL值敏感，改写为LEFT JOIN更可靠",
                expected_benefit="LEFT JOIN方式性能更优且避免NULL值陷阱",
            ))
        return results
