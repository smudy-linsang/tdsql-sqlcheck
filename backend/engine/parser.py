"""
TDSQL SQL审核工具 - SQL解析器

基于 sqlglot 实现 SQL 解析，提取语法树中的关键信息。
"""
import re
from dataclasses import dataclass, field
from typing import Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError


@dataclass
class ParsedSQL:
    """解析后的SQL结构"""
    raw_sql: str = ""
    sql_type: str = ""  # SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP
    tables: list[str] = field(default_factory=list)
    select_fields: list[str] = field(default_factory=list)
    has_wildcard_select: bool = False
    where_clause: Optional[str] = None
    has_where: bool = False
    where_columns: list[str] = field(default_factory=list)
    where_has_function: bool = False
    has_order_by: bool = False
    order_by_random: bool = False
    subquery_depth: int = 0
    join_count: int = 0
    # DDL 相关
    is_create_table: bool = False
    is_alter_table: bool = False
    columns: list[dict] = field(default_factory=list)
    has_primary_key: bool = False
    engine: Optional[str] = None
    charset: Optional[str] = None
    has_foreign_key: bool = False
    column_types: list[dict] = field(default_factory=list)
    indexes: list[dict] = field(default_factory=list)
    table_options: dict = field(default_factory=dict)
    parse_error: Optional[str] = None
    ast: Optional[object] = None


class SQLParser:
    """SQL解析器"""

    def __init__(self, dialect: str = "mysql"):
        self.dialect = dialect

    def parse(self, sql: str) -> ParsedSQL:
        """
        解析SQL语句，返回结构化的 ParsedSQL 对象。
        对于无法解析的SQL，会设置 parse_error 并尽可能提取基本信息。
        """
        parsed = ParsedSQL(raw_sql=sql.strip())
        sql_clean = sql.strip().rstrip(";")

        # 尝试解析SQL
        try:
            ast = sqlglot.parse_one(sql_clean, read=self.dialect)
            parsed.ast = ast
        except SqlglotError as e:
            parsed.parse_error = str(e)
            # 解析失败时，用正则做基础识别
            parsed.sql_type = self._detect_sql_type_regex(sql_clean)
            return parsed
        except Exception as e:
            parsed.parse_error = str(e)
            parsed.sql_type = self._detect_sql_type_regex(sql_clean)
            return parsed

        # 确定 SQL 类型
        parsed.sql_type = self._get_sql_type(ast)

        # 根据SQL类型分别解析
        if isinstance(ast, exp.Select):
            self._parse_select(ast, parsed)
        elif isinstance(ast, exp.Insert):
            self._parse_insert(ast, parsed)
        elif isinstance(ast, exp.Update):
            self._parse_update(ast, parsed)
        elif isinstance(ast, exp.Delete):
            self._parse_delete(ast, parsed)
        elif isinstance(ast, exp.Create):
            self._parse_create(ast, parsed)
        elif isinstance(ast, exp.AlterTable):
            self._parse_alter(ast, parsed)

        # 通用：提取所有涉及的表
        if not parsed.tables:
            parsed.tables = self._extract_tables(ast)

        return parsed

    def _detect_sql_type_regex(self, sql: str) -> str:
        """正则检测SQL类型（解析失败时的回退方案）"""
        sql_upper = sql.upper().strip()
        for keyword in ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"):
            if sql_upper.startswith(keyword):
                return keyword
        return "UNKNOWN"

    def _get_sql_type(self, ast) -> str:
        """从AST获取SQL类型"""
        if isinstance(ast, exp.Select):
            return "SELECT"
        elif isinstance(ast, exp.Insert):
            return "INSERT"
        elif isinstance(ast, exp.Update):
            return "UPDATE"
        elif isinstance(ast, exp.Delete):
            return "DELETE"
        elif isinstance(ast, exp.Create):
            kind = ast.args.get("kind", "")
            return f"CREATE {kind}".strip().upper() if kind else "CREATE"
        elif isinstance(ast, exp.AlterTable):
            return "ALTER"
        elif isinstance(ast, exp.Drop):
            return "DROP"
        return "UNKNOWN"

    # ── SELECT 解析 ──────────────────────────────────────

    def _parse_select(self, ast: exp.Select, parsed: ParsedSQL):
        """解析 SELECT 语句"""
        # 提取表名
        parsed.tables = self._extract_tables(ast)

        # 提取 SELECT 字段
        parsed.select_fields = []
        for e in ast.expressions:
            if isinstance(e, exp.Star):
                parsed.has_wildcard_select = True
                parsed.select_fields.append("*")
            else:
                parsed.select_fields.append(e.sql(dialect=self.dialect))

        # WHERE 条件
        where = ast.args.get("where")
        if where:
            parsed.has_where = True
            parsed.where_clause = where.sql(dialect=self.dialect)
            parsed.where_columns = self._extract_where_columns(where)
            parsed.where_has_function = self._check_where_has_function(where)

        # ORDER BY
        order = ast.args.get("order")
        if order:
            parsed.has_order_by = True
            parsed.order_by_random = self._check_order_by_random(order)

        # 子查询深度
        parsed.subquery_depth = self._calc_subquery_depth(ast)

        # JOIN 计数
        parsed.join_count = self._count_joins(ast)

    # ── INSERT 解析 ──────────────────────────────────────

    def _parse_insert(self, ast: exp.Insert, parsed: ParsedSQL):
        """解析 INSERT 语句"""
        # INSERT 的 target 可能是 Schema（含列名）或 Table
        target = ast.args.get("this")
        if target:
            if isinstance(target, exp.Schema):
                # Schema.this 是 Table 对象（纯表名）
                table_obj = target.this
                if table_obj:
                    parsed.tables.append(table_obj.sql(dialect=self.dialect))
            else:
                parsed.tables.append(target.sql(dialect=self.dialect))
        # INSERT 可能有子查询 (INSERT INTO ... SELECT)
        select = ast.args.get("expression")
        if isinstance(select, exp.Select):
            self._parse_select(select, parsed)
            # 合并表
            target_name = target.sql(dialect=self.dialect) if target else ""
            if target_name and target_name not in parsed.tables:
                parsed.tables.insert(0, target_name)

    # ── UPDATE 解析 ──────────────────────────────────────

    def _parse_update(self, ast: exp.Update, parsed: ParsedSQL):
        """解析 UPDATE 语句"""
        parsed.tables = self._extract_tables(ast)
        where = ast.args.get("where")
        if where:
            parsed.has_where = True
            parsed.where_clause = where.sql(dialect=self.dialect)
            parsed.where_columns = self._extract_where_columns(where)
            parsed.where_has_function = self._check_where_has_function(where)

    # ── DELETE 解析 ──────────────────────────────────────

    def _parse_delete(self, ast: exp.Delete, parsed: ParsedSQL):
        """解析 DELETE 语句"""
        parsed.tables = self._extract_tables(ast)
        where = ast.args.get("where")
        if where:
            parsed.has_where = True
            parsed.where_clause = where.sql(dialect=self.dialect)
            parsed.where_columns = self._extract_where_columns(where)
            parsed.where_has_function = self._check_where_has_function(where)

    # ── CREATE TABLE 解析 ────────────────────────────────

    def _parse_create(self, ast: exp.Create, parsed: ParsedSQL):
        """解析 CREATE TABLE 语句"""
        parsed.is_create_table = True

        # ast.this 是 Schema 对象，Schema.this 才是 Table 对象
        schema = ast.args.get("this")

        # 提取表名
        if isinstance(schema, exp.Schema):
            table_obj = schema.this
            if table_obj:
                parsed.tables.append(table_obj.sql(dialect=self.dialect))
        elif schema:
            parsed.tables.append(schema.sql(dialect=self.dialect))

        # 解析列定义和索引定义
        if isinstance(schema, exp.Schema):
            for col_def in schema.expressions:
                if isinstance(col_def, exp.ColumnDef):
                    col_info = self._parse_column_def(col_def)
                    parsed.columns.append(col_info)
                    parsed.column_types.append({
                        "name": col_info["name"],
                        "type": col_info["type"],
                        "raw_type": col_info["raw_type"],
                    })
                elif isinstance(col_def, exp.PrimaryKey):
                    parsed.has_primary_key = True
                # MySQL方言下 INDEX 解析为 IndexColumnConstraint
                elif type(col_def).__name__ == "IndexColumnConstraint":
                    idx_name_node = col_def.args.get("this")
                    idx_name = idx_name_node.sql(dialect=self.dialect) if idx_name_node else ""
                    idx_cols = []
                    for ordered_expr in col_def.expressions:
                        col_node = ordered_expr.args.get("this") if hasattr(ordered_expr, 'args') else None
                        if col_node:
                            col_name = col_node.sql(dialect=self.dialect).strip('`"')
                            if col_name:
                                idx_cols.append(col_name)
                    if idx_cols:
                        parsed.indexes.append({
                            "name": idx_name,
                            "columns": idx_cols,
                        })

        # 检查约束中的主键和外键
        if isinstance(schema, exp.Schema):
            for constraint in schema.find_all(exp.PrimaryKey):
                parsed.has_primary_key = True
            for fk in schema.find_all(exp.ForeignKey):
                parsed.has_foreign_key = True

        # 检查列定义中的主键标记
        for col in parsed.columns:
            if col.get("is_primary_key"):
                parsed.has_primary_key = True

        # 解析表选项 (ENGINE, CHARSET 等)
        properties = ast.args.get("properties")
        if properties:
            self._parse_table_properties(properties, parsed)

    def _parse_column_def(self, col_def: exp.ColumnDef) -> dict:
        """解析单个列定义"""
        col_name = col_def.name
        # 新版 sqlglot 使用 kind 存储数据类型（DataType 对象）
        data_type = col_def.args.get("kind")
        raw_type = data_type.sql(dialect=self.dialect) if data_type else ""

        # 从 DataType.this (DType 枚举) 提取类型名
        type_name = ""
        if data_type and data_type.this is not None:
            dtype = data_type.this
            if hasattr(dtype, 'name'):
                type_name = dtype.name.upper()
            elif hasattr(dtype, 'value'):
                type_name = str(dtype.value).upper()

        # 回退：从 raw_type 提取类型名
        if not type_name and raw_type:
            type_name = raw_type.split("(")[0].split(" ")[0].upper()

        info = {
            "name": col_name,
            "type": type_name,
            "raw_type": raw_type,
            "is_primary_key": False,
            "is_not_null": False,
            "has_default": False,
            "default_value": None,
            "length": None,
        }

        # 检查约束
        for constraint in col_def.find_all(exp.ColumnConstraint):
            c_kind = constraint.args.get("kind")
            if isinstance(c_kind, exp.PrimaryKeyColumnConstraint):
                info["is_primary_key"] = True
            elif isinstance(c_kind, exp.NotNullColumnConstraint):
                info["is_not_null"] = True
            elif isinstance(c_kind, exp.DefaultColumnConstraint):
                info["has_default"] = True
                info["default_value"] = c_kind.this.sql(dialect=self.dialect) if c_kind.this else None

        # 提取长度
        if data_type:
            size = data_type.args.get("expressions")
            if size and len(size) > 0:
                try:
                    info["length"] = int(size[0].sql(dialect=self.dialect))
                except (ValueError, IndexError):
                    pass

        return info

    def _parse_table_properties(self, properties, parsed: ParsedSQL):
        """解析表选项 (ENGINE, CHARSET 等)"""
        for prop in properties.expressions:
            if isinstance(prop, exp.EngineProperty):
                # EngineProperty.this 是 Var(this=InnoDB)
                engine_var = prop.this
                if engine_var:
                    parsed.engine = engine_var.name.upper() if hasattr(engine_var, 'name') else str(engine_var).upper()
                    parsed.table_options["engine"] = parsed.engine
            elif isinstance(prop, exp.CharacterSetProperty):
                charset_var = prop.this
                if charset_var:
                    parsed.charset = charset_var.name.upper() if hasattr(charset_var, 'name') else str(charset_var).upper()
                    parsed.table_options["charset"] = parsed.charset
            elif isinstance(prop, exp.Property):
                # 通用属性处理
                key = prop.name.upper() if hasattr(prop, 'name') else ""
                val = prop.args.get("value")
                if key and val:
                    parsed.table_options[key] = val.sql(dialect=self.dialect)

    # ── ALTER TABLE 解析 ─────────────────────────────────

    def _parse_alter(self, ast: exp.AlterTable, parsed: ParsedSQL):
        """解析 ALTER TABLE 语句"""
        parsed.is_alter_table = True
        table = ast.args.get("this")
        if table:
            parsed.tables.append(table.sql(dialect=self.dialect))

    # ── 通用辅助方法 ─────────────────────────────────────

    def _extract_tables(self, ast) -> list[str]:
        """从AST提取所有表名"""
        tables = []
        for table in ast.find_all(exp.Table):
            name = table.sql(dialect=self.dialect)
            if name and name not in tables:
                tables.append(name)
        return tables

    def _extract_where_columns(self, where_node) -> list[str]:
        """提取WHERE条件中涉及的列名"""
        columns = []
        for col in where_node.find_all(exp.Column):
            name = col.sql(dialect=self.dialect)
            if name and name not in columns:
                columns.append(name)
        return columns

    def _check_where_has_function(self, where_node) -> bool:
        """检查WHERE条件中是否包含函数调用或索引失效模式（LIKE/OR/函数）"""
        # 排除的运算符类型（sqlglot中这些都继承自exp.Func）
        _op_names = {
            'And', 'Or', 'Not', 'EQ', 'NEQ', 'GT', 'GTE', 'LT', 'LTE',
            'Is', 'IsNot', 'In', 'Between', 'Like', 'ILike',
            'Paren', 'Condition',
        }
        for node in where_node.walk():
            node_type = type(node).__name__
            # 检查真正的函数调用（如 DATE(), CONCAT(), NOW() 等）
            if isinstance(node, exp.Func) and node_type not in _op_names:
                return True
            # 检查全模糊 LIKE '%xxx%'
            if isinstance(node, exp.Like):
                pattern = node.args.get("expression")
                if pattern:
                    pattern_sql = pattern.sql().strip("'\"")
                    if pattern_sql.startswith("%"):
                        return True
            # 检查 OR 条件（可能导致索引失效）
            if isinstance(node, exp.Or):
                return True
        return False

    def _check_order_by_random(self, order_node) -> bool:
        """检查 ORDER BY 中是否包含 RAND()"""
        for expression in order_node.expressions:
            expr = expression.this  # Ordered
            if isinstance(expr, exp.Anonymous) and expr.name.upper() in ("RAND", "RANDOM"):
                return True
            if isinstance(expr, exp.Func) and expr.sql(dialect=self.dialect).upper().startswith("RAND"):
                return True
        return False

    def _calc_subquery_depth(self, ast) -> int:
        """计算子查询嵌套深度（直接统计最大嵌套层数）"""
        # 统计所有嵌套的子查询（包括 IN (SELECT ...) 模式）
        # Subquery 节点和独立的 Select 节点（作为表达式的一部分）都算作子查询
        max_depth = 0
        stack = [(ast, 0)]
        while stack:
            node, depth = stack.pop()
            new_depth = depth
            # Subquery 包装节点
            if isinstance(node, exp.Subquery):
                new_depth = depth + 1
                max_depth = max(max_depth, new_depth)
            # 独立的 Select 作为子查询（如 IN (SELECT ...)）
            elif isinstance(node, exp.Select) and depth > 0:
                new_depth = depth + 1
                max_depth = max(max_depth, new_depth)
            # 遍历 args 中的子节点
            for key, val in node.args.items():
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, exp.Expression):
                            stack.append((item, new_depth))
                elif isinstance(val, exp.Expression):
                    stack.append((val, new_depth))
        return max_depth

    def _count_joins(self, ast) -> int:
        """统计 JOIN 数量"""
        count = 0
        for join in ast.find_all(exp.Join):
            count += 1
        return count
