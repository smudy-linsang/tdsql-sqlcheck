"""
TDSQL SQL审核工具 - 索引顾问引擎 (V1.0)

基于SQL结构和EXPLAIN输出，推荐合适的索引。
"""
import re
from typing import Optional

from backend.models import IndexRecommendation


class IndexAdvisor:
    """索引顾问引擎"""

    # WHERE条件中常见的等值/范围操作符
    EQ_OPERATORS = ("=", "is", "in")
    RANGE_OPERATORS = (">", "<", ">=", "<=", "between", "like")

    def advise_from_sql(self, sql: str, table_metadata: Optional[dict] = None) -> list[IndexRecommendation]:
        """
        从SQL文本分析并推荐索引。

        Args:
            sql: SQL语句
            table_metadata: 表元数据 {table: {indexes: [...], columns: [...]}}

        Returns:
            索引推荐列表
        """
        recommendations = []
        sql_lower = sql.lower().strip()

        if not sql_lower.startswith("select"):
            return recommendations

        # 提取表名和WHERE条件
        tables = self._extract_tables(sql)
        where_columns = self._extract_where_columns(sql)

        if not tables or not where_columns:
            return recommendations

        for table in tables:
            # 获取已有索引
            existing_indexes = []
            existing_cols = set()
            if table_metadata and table in table_metadata:
                existing_indexes = table_metadata[table].get("indexes", [])
                for idx in existing_indexes:
                    existing_cols.update(c.lower() for c in idx.get("columns", []))

            # 过滤已有索引覆盖的列
            missing_cols = [c for c in where_columns if c.lower() not in existing_cols]
            if not missing_cols:
                continue

            # 推荐单列索引或复合索引
            if len(missing_cols) == 1:
                col = missing_cols[0]
                rec = IndexRecommendation(
                    type="single",
                    table=table,
                    index_name=f"idx_{table}_{col}"[:30],
                    columns=[col],
                    ddl=f"ALTER TABLE {table} ADD INDEX idx_{table}_{col} ({col})",
                    reason=f"WHERE条件字段 '{col}' 无索引覆盖，可能导致全表扫描",
                )
                recommendations.append(rec)
            else:
                # 复合索引：等值条件在前，范围条件在后
                eq_cols, range_cols = self._classify_columns(sql, missing_cols)
                composite_cols = eq_cols + range_cols[:1]
                if composite_cols:
                    col_str = "_".join(composite_cols)
                    rec = IndexRecommendation(
                        type="composite",
                        table=table,
                        index_name=f"idx_{table}_{col_str}"[:30],
                        columns=composite_cols,
                        ddl=f"ALTER TABLE {table} ADD INDEX idx_{table}_{col_str} ({', '.join(composite_cols)})",
                        reason=f"WHERE条件多字段无索引覆盖，建议复合索引(等值在前)",
                    )
                    recommendations.append(rec)

        return recommendations

    def advise_from_explain(self, explain_rows: list[dict]) -> list[IndexRecommendation]:
        """从EXPLAIN输出分析索引缺失"""
        recommendations = []
        for row in explain_rows:
            access_type = str(row.get("type", "")).lower()
            table = row.get("table", "")
            possible_keys = row.get("possible_keys", "")
            key_used = row.get("key", "")
            rows_estimated = int(row.get("rows", 0) or 0)

            if access_type in ("all", "index") and rows_estimated > 1000:
                if possible_keys and (not key_used or key_used == "NULL"):
                    # 有可选索引但优化器未使用
                    rec = IndexRecommendation(
                        type="unused",
                        table=table,
                        index_name="",
                        columns=[],
                        ddl=f"-- 表 {table} 有可选索引({possible_keys})但未使用，检查类型匹配",
                        reason=f"表 {table} 有索引可选({possible_keys})但优化器未选择，可能存在类型不匹配",
                    )
                    recommendations.append(rec)
                elif not key_used or key_used == "NULL":
                    rec = IndexRecommendation(
                        type="missing",
                        table=table,
                        index_name=f"idx_{table}_advise",
                        columns=[],
                        ddl=f"-- 表 {table} 全表扫描({rows_estimated}行)，建议分析WHERE条件添加索引",
                        reason=f"EXPLAIN显示表 {table} 访问类型为 {access_type}，扫描 {rows_estimated} 行，未使用索引",
                    )
                    recommendations.append(rec)
            elif possible_keys and (not key_used or key_used == "NULL"):
                rec = IndexRecommendation(
                    type="unused",
                    table=table,
                    index_name="",
                    columns=[],
                    ddl=f"-- 表 {table} 有可选索引({possible_keys})但未使用，检查类型匹配",
                    reason=f"表 {table} 有索引可选({possible_keys})但优化器未选择，可能存在类型不匹配",
                )
                recommendations.append(rec)

        return recommendations

    def detect_redundant_indexes(self, indexes: list[dict]) -> list[IndexRecommendation]:
        """检测冗余索引"""
        recommendations = []
        for i, idx_a in enumerate(indexes):
            cols_a = idx_a.get("columns", [])
            for j, idx_b in enumerate(indexes):
                if i >= j:
                    continue
                cols_b = idx_b.get("columns", [])
                # A是B的前缀
                if len(cols_a) < len(cols_b) and cols_b[:len(cols_a)] == cols_a:
                    recommendations.append(IndexRecommendation(
                        type="redundant",
                        table="",
                        index_name=idx_a.get("name", ""),
                        columns=cols_a,
                        ddl=f"-- 索引 {idx_a.get('name', '')} 是 {idx_b.get('name', '')} 的前缀，建议删除",
                        reason=f"索引 {idx_a.get('name', '')} ({','.join(cols_a)}) 是 {idx_b.get('name', '')} 的前缀索引，冗余",
                    ))
        return recommendations

    def _extract_tables(self, sql: str) -> list[str]:
        """提取表名"""
        tables = []
        for pattern in [r"\bfrom\s+([a-zA-Z_][a-zA-Z0-9_.]*)", r"\bjoin\s+([a-zA-Z_][a-zA-Z0-9_.]*)"]:
            for m in re.finditer(pattern, sql, re.IGNORECASE):
                t = m.group(1).strip("`\"")
                if t and t not in tables:
                    tables.append(t)
        return tables

    def _extract_where_columns(self, sql: str) -> list[str]:
        """提取WHERE条件中的列名"""
        cols = []
        where_match = re.search(r"\bwhere\b(.+?)(\bgroup\s+by\b|\border\s+by\b|\blimit\b|$)", sql, re.IGNORECASE | re.DOTALL)
        if not where_match:
            return cols
        where_text = where_match.group(1)
        # 提取 column = / column > / column in 等
        for m in re.finditer(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*(=|>|<|>=|<=|!=|<>|\bis\b|\bin\b|\bbetween\b|\blike\b)", where_text, re.IGNORECASE):
            col = m.group(1).lower()
            if col not in ("and", "or", "not", "null", "true", "false") and col not in cols:
                cols.append(col)
        return cols

    def _classify_columns(self, sql: str, columns: list[str]) -> tuple[list[str], list[str]]:
        """将列分为等值条件和范围条件"""
        eq_cols = []
        range_cols = []
        where_match = re.search(r"\bwhere\b(.+?)(\bgroup\s+by\b|\border\s+by\b|\blimit\b|$)", sql, re.IGNORECASE | re.DOTALL)
        where_text = where_match.group(1) if where_match else ""
        for col in columns:
            # 检查是否等值条件
            if re.search(rf"\b{re.escape(col)}\s*=\s*", where_text, re.IGNORECASE):
                eq_cols.append(col)
            elif re.search(rf"\b{re.escape(col)}\s*(>|<|>=|<=|between|like)\s*", where_text, re.IGNORECASE):
                range_cols.append(col)
            else:
                eq_cols.append(col)  # 默认归等值
        return eq_cols, range_cols
