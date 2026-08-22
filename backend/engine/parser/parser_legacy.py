"""
TDSQL SQL审核工具 - SQL解析器 (V1.0)

基于 sqlglot 实现 SQL 解析，提取语法树中的关键信息。
V1.0 扩展：新增30+字段，支持CREATE/ALTER/INSERT/LOAD/HANDLER等深度解析。
"""
import re
from dataclasses import dataclass, field
from typing import Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError


# TDSQL 方言尾子句：sqlglot 不认识，会导致整条建表语句降级为 exp.Command，
# 进而使 columns/indexes/table_options 全空，所有结构类规则静默跳过。
#   分片表:  ) ENGINE=InnoDB ... TDSQL_DISTRIBUTED BY HASH(`cust_no`)
#            （HASH / RANGE / LIST 三种方法均会降级）
#   广播表:  ) ENGINE=InnoDB ... BROADCAST
# 注: `shardkey=col` 与单独的 `PARTITION BY ...` 不会降级，无需处理。
# 本正则只在"已经降级"的重试路径上使用，故无需做注释/字符串感知——
# 正常解析的语句根本不会走到那里（见 parse() 内注释）。
_TDSQL_DIALECT_RE = re.compile(
    r"\btdsql_distributed\s+by\s+\w+\s*\([^)]*\)"   # 分片表：BY HASH/RANGE/LIST(col)
    r"|\bbroadcast\b",                               # 广播表关键字
    re.IGNORECASE,
)


@dataclass
class ParsedSQL:
    """解析后的SQL结构（V1.0 完整字段）"""
    # === 基础信息 ===
    raw_sql: str = ""
    sql_type: str = ""  # SELECT/INSERT/UPDATE/DELETE/CREATE/ALTER/DROP/LOAD/HANDLER/FLUSH/LOCK
    tables: list[str] = field(default_factory=list)
    select_fields: list[str] = field(default_factory=list)

    # === DDL结构信息 ===
    is_create_table: bool = False
    is_alter_table: bool = False
    has_primary_key: bool = False
    has_foreign_key: bool = False
    engine: Optional[str] = None
    charset: Optional[str] = None
    columns: list[dict] = field(default_factory=list)
    column_types: list[dict] = field(default_factory=list)
    indexes: list[dict] = field(default_factory=list)
    table_options: dict = field(default_factory=dict)
    has_table_comment: bool = False
    column_comments: dict[str, str] = field(default_factory=dict)
    index_definitions: list[dict] = field(default_factory=list)
    is_create_table_select: bool = False
    is_temporary_table: bool = False
    has_drop_database: bool = False
    alter_actions: list[dict] = field(default_factory=list)

    # === DML结构信息 ===
    has_wildcard_select: bool = False
    where_clause: Optional[str] = None
    has_where: bool = False
    where_columns: list[str] = field(default_factory=list)
    where_has_function: bool = False
    has_order_by: bool = False
    order_by_random: bool = False
    subquery_depth: int = 0
    join_count: int = 0
    has_explicit_join: bool = False
    has_into_outfile: bool = False
    has_index_hint: bool = False
    has_for_update: bool = False
    has_lock_tables: bool = False
    or_in_where: bool = False
    in_list_size: int = 0
    limit_offset: int = -1
    has_delayed_keyword: bool = False
    is_multi_table_update: bool = False
    has_load_data: bool = False
    has_handler_do: bool = False
    has_flush: bool = False
    has_unnamed_insert: bool = False
    insert_columns: list[str] = field(default_factory=list)
    where_has_not_equal: bool = False
    has_hint: bool = False

    # === 命名信息 ===
    table_name_plural: bool = False

    # === 分布式信息（需元数据增强） ===
    shardkey_in_where: bool = False
    shardkey_in_insert: bool = False
    shardkey_in_orderby: bool = False

    # === 事务信息 ===
    is_begin: bool = False
    is_commit: bool = False
    is_rollback: bool = False
    transaction_sql_count: int = 0

    # === 解析元信息 ===
    parse_error: Optional[str] = None
    ast: Optional[object] = None


class SQLParser:
    """SQL解析器（V1.0）"""

    # 常见英语复数后缀
    PLURAL_SUFFIXES = ("s", "es", "ies", "ses")
    # 需要忽略复数检查的词（本身以s结尾但非复数）
    PLURAL_IGNORE = {"status", "process", "address", "access", "class", "glass", "gas", "bus", "plus", "this", "news", "series", "species"}

    def __init__(self, dialect: str = "mysql"):
        self.dialect = dialect

    def parse(self, sql: str) -> ParsedSQL:
        """解析SQL语句，返回结构化的 ParsedSQL 对象。"""
        parsed = ParsedSQL(raw_sql=sql.strip())
        sql_clean = sql.strip().rstrip(";")

        # 先做正则级别的快速检测（补充sqlglot可能遗漏的信息）
        parsed = self._regex_pre_parse(sql_clean, parsed)

        # 尝试解析SQL
        try:
            ast = sqlglot.parse_one(sql_clean, read=self.dialect)
            # v1.6.2.0: TDSQL 方言尾子句会让 sqlglot 把整条语句降级为 Command，
            # 导致 columns/indexes/table_options 全空、结构类规则静默漏审。
            # 仅在"确实已降级"且"语句含方言子句"时，剥离该子句重试一次；
            # 且只有重试确实产出非 Command 节点才采用其结果。
            # 正常解析的语句不会进入本分支，故对既有行为的影响可证明为零；
            # 重试失败时保留原 Command 结果，不劣于改前。
            # 注意: parsed.raw_sql 始终保持原文——R077/R054 依赖它提取分片键。
            if isinstance(ast, exp.Command) and _TDSQL_DIALECT_RE.search(sql_clean):
                try:
                    _retry_ast = sqlglot.parse_one(
                        _TDSQL_DIALECT_RE.sub(" ", sql_clean), read=self.dialect)
                    if not isinstance(_retry_ast, exp.Command):
                        ast = _retry_ast
                except Exception:
                    pass
            parsed.ast = ast
        except (SqlglotError, Exception) as e:
            parsed.parse_error = str(e)
            parsed.sql_type = self._detect_sql_type_regex(sql_clean)
            # 正则回退提取表名（防止含中划线等语法不合规表名在解析报错时漏检）
            tbl_match = re.search(r'\b(?:create\s+table|alter\s+table|drop\s+table|truncate\s+table|from|into|update)\s+(?:if\s+(?:not\s+)?exists\s+)?([`\'"]?[a-zA-Z0-9_\-]+[`\'"]?)', sql_clean, re.IGNORECASE)
            if tbl_match:
                tb_name = tbl_match.group(1).strip("`\"' ")
                if tb_name and tb_name.lower() not in ("table", "if", "exists"):
                    parsed.tables.append(tb_name)
                    if "create table" in sql_clean.lower():
                        parsed.is_create_table = True
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
        elif isinstance(ast, exp.Alter):
            self._parse_alter(ast, parsed)
        elif isinstance(ast, exp.Drop):
            self._parse_drop(ast, parsed)

        # 通用解析
        self._parse_common(ast, parsed)

        # 提取表名及 DDL 属性（如果各类型解析未提取到，或为 sqlglot Command 降级节点）
        if not parsed.tables or isinstance(ast, exp.Command) or parsed.sql_type == "UNKNOWN":
            if parsed.sql_type == "UNKNOWN":
                parsed.sql_type = self._detect_sql_type_regex(sql_clean)
            parsed.tables = self._extract_tables(ast)
            if not parsed.tables:
                tbl_match = re.search(r'\b(?:create\s+table|alter\s+table|drop\s+table|truncate\s+table|from|into|update)\s+(?:if\s+(?:not\s+)?exists\s+)?([`\'"]?[a-zA-Z0-9_\-]+[`\'"]?)', sql_clean, re.IGNORECASE)
                if tbl_match:
                    tb_name = tbl_match.group(1).strip("`\"' ")
                    if tb_name and tb_name.lower() not in ("table", "if", "exists"):
                        parsed.tables.append(tb_name)
            self._regex_fallback_create_table_props(sql_clean, parsed)

        return parsed

    # ── 正则预解析（补充sqlglot遗漏的信息） ──────────────────

    def _regex_pre_parse(self, sql: str, parsed: ParsedSQL) -> ParsedSQL:
        """用正则做快速预解析，补充sqlglot可能遗漏的信息"""
        sql_lower = sql.lower()

        # 检测 DELAYED / LOW_PRIORITY
        if re.search(r"\b(delayed|low_priority)\b", sql_lower):
            parsed.has_delayed_keyword = True

        # 检测 INTO OUTFILE
        if "into outfile" in sql_lower or "into dumpfile" in sql_lower:
            parsed.has_into_outfile = True

        # 检测 LOAD DATA / LOAD XML
        clean_no_comment = re.sub(r'--[^\n]*', '', sql_lower)
        clean_no_comment = re.sub(r'/\*.*?\*/', '', clean_no_comment, flags=re.DOTALL).strip()
        if clean_no_comment.startswith(("load data", "load xml")) or bool(re.search(r'\bload\s+(?:data|xml)\b', clean_no_comment)):
            parsed.has_load_data = True
            if parsed.sql_type == "UNKNOWN":
                parsed.sql_type = "LOAD"

        # 检测 HANDLER ... OPEN/READ/CLOSE
        if re.match(r"\bhandler\b", sql_lower):
            parsed.has_handler_do = True

        # 检测 FLUSH
        if re.match(r"\bflush\b", sql_lower):
            parsed.has_flush = True

        # 检测 LOCK TABLES / UNLOCK TABLES
        if re.match(r"\block\s+tables\b", sql_lower):
            parsed.has_lock_tables = True

        # 检测 FOR UPDATE / FOR SHARE
        if "for update" in sql_lower or "for share" in sql_lower:
            parsed.has_for_update = True

        # 检测 DROP DATABASE
        if re.match(r"\bdrop\s+(database|schema)\b", sql_lower):
            parsed.has_drop_database = True

        # 检测 IN 列表大小
        in_match = re.findall(r"\bin\s*\(([^)]+)\)", sql_lower)
        for m in in_match:
            count = len([x for x in m.split(",") if x.strip()])
            if count > parsed.in_list_size:
                parsed.in_list_size = count

        # 检测 LIMIT offset
        limit_match = re.search(r"\blimit\s+(\d+)\s*,\s*(\d+)", sql_lower)
        if limit_match:
            parsed.limit_offset = int(limit_match.group(1))
        else:
            limit_offset_match = re.search(r"\blimit\s+(\d+)\s+offset\s+(\d+)", sql_lower)
            if limit_offset_match:
                parsed.limit_offset = int(limit_offset_match.group(2))

        # 检测 BEGIN / COMMIT / ROLLBACK
        if re.match(r"\b(begin|start\s+transaction)\b", sql_lower):
            parsed.is_begin = True
        if re.match(r"\bcommit\b", sql_lower):
            parsed.is_commit = True
        if re.match(r"\brollback\b", sql_lower):
            parsed.is_rollback = True

        # 检测 WHERE 中的 OR
        if " where " in sql_lower:
            where_part = sql_lower.split(" where ")[1].split(" group by ")[0].split(" order by ")[0].split(" limit ")[0]
            if re.search(r"\bor\b", where_part):
                parsed.or_in_where = True
            # 检测 != / <>
            if "!=" in where_part or "<>" in where_part or "is not null" in where_part or "is null" in where_part:
                parsed.where_has_not_equal = True

        # 检测 TEMPORARY（含Oracle GTT: CREATE GLOBAL TEMPORARY TABLE）
        if re.match(r"\bcreate\s+(global\s+)?temporary\s+table\b", sql_lower):
            parsed.is_temporary_table = True
        # Oracle GTT ON COMMIT DELETE|PRESERVE ROWS
        if re.search(r"\bon\s+commit\s+(delete|preserve)\s+rows\b", sql_lower):
            parsed.is_temporary_table = True

        # 检测 CREATE TABLE ... SELECT
        if re.match(r"\bcreate\s+(temporary\s+)?table\b.*\b(as\s+)?select\b", sql_lower):
            parsed.is_create_table_select = True

        # 检测联表更新 / 联表删除
        clean_sql_no_comm = re.sub(r'--[^\n]*', '', sql_lower)
        clean_sql_no_comm = re.sub(r'/\*.*?\*/', '', clean_sql_no_comm, flags=re.DOTALL).strip()
        # UPDATE：取 UPDATE 与 SET 之间的目标表段，段内含逗号或 JOIN 即联表。
        # 旧写法 [^set]+ 是否定"字符"组（排除字母 s/e/t），并非排除单词 SET——
        # 对 t_xxx 等含 s/e/t 的表名恒不匹配，导致逗号式联表 UPDATE 静默漏报（P2-03）。
        # 同时旧 JOIN 分支会把 SET 子句里子查询的 JOIN 误判进来，限定目标段后一并修正。
        m_upd = re.search(r"\bupdate\b(.*?)\bset\b", clean_sql_no_comm, re.DOTALL)
        upd_multi = bool(m_upd and ("," in m_upd.group(1)
                                    or re.search(r"\bjoin\b", m_upd.group(1))))
        # DELETE：同样只看目标段（DELETE 到第一个 WHERE 之间），理由与 UPDATE 一致。
        # 旧写法 `\bdelete\s+.*?\bjoin\b` 满句扫，会把 WHERE 子查询里的 JOIN
        # 误判成联表 DELETE —— `DELETE FROM t WHERE id IN (SELECT .. JOIN ..)`
        # 是合法单表删除，却命中 ERROR 级 R043，并被质量门禁（ERROR 阈值默认 0）
        # 挡住合法变更。该误报为 v1.2.0.9 之后引入，升级会带给内网。
        m_del = re.search(r"\bdelete\b(.*?)(?:\bwhere\b|$)", clean_sql_no_comm, re.DOTALL)
        del_seg = m_del.group(1) if m_del else ""
        # 先剥掉 DELETE 的合法修饰词，否则它们会被下面的"别名列表"正则当成别名：
        # `DELETE LOW_PRIORITY FROM t` 的目标段是 ` low_priority from t`，
        # 形态与 `DELETE a FROM t` 完全一致，会把单表删除误判成联表（O 复核发现）。
        # 只剥段首连续出现的修饰词，不碰后面的表名（表名可以叫 low_priority）。
        del_seg = re.sub(r"^\s*(?:(?:low_priority|quick|ignore)\s+)+", " ", del_seg)
        del_multi = bool(del_seg and (
            re.search(r"\bjoin\b", del_seg)                         # DELETE a FROM t1 JOIN t2
            or re.search(r"\busing\b", del_seg)                     # DELETE FROM t1,t2 USING ...
            # DELETE a, b FROM ...：FROM 之前必须先出现【标识符】。
            # 不能写成 [\w`\s,]+ —— 空白也在字符组里，` from t` 的前导空格
            # 自身即可满足 +，会把普通单表 DELETE FROM 全部误判成联表。
            or re.search(r"^\s*[a-zA-Z0-9_`][a-zA-Z0-9_`\s,]*\bfrom\b", del_seg)
        ))
        if upd_multi or del_multi:
            parsed.is_multi_table_update = True

        # 检测 INDEX HINT (USE INDEX / FORCE INDEX / IGNORE INDEX)
        if re.search(r"\b(use|force|ignore)\s+index\b", sql_lower):
            parsed.has_index_hint = True

        # 检测 SQL hint
        if re.search(r"\b(sql_no_cache|sql_calc_found_rows|sql_buffer_result)\b", sql_lower):
            parsed.has_hint = True

        return parsed

    def _detect_sql_type_regex(self, sql: str) -> str:
        """正则检测SQL类型（解析失败时的回退方案）"""
        clean_sql = re.sub(r'--[^\n]*', '', sql)
        clean_sql = re.sub(r'/\*.*?\*/', '', clean_sql, flags=re.DOTALL).strip()
        sql_upper = clean_sql.upper()
        if "CREATE TABLE" in sql_upper:
            return "CREATE TABLE"
        if "CREATE PROCEDURE" in sql_upper:
            return "CREATE PROCEDURE"
        if "CREATE TRIGGER" in sql_upper:
            return "CREATE TRIGGER"
        if "CREATE VIEW" in sql_upper:
            return "CREATE VIEW"
        if "CREATE FUNCTION" in sql_upper:
            return "CREATE FUNCTION"
        for keyword in ("SELECT", "INSERT", "REPLACE", "UPDATE", "DELETE",
                        "CREATE", "ALTER", "DROP", "LOAD", "HANDLER", "FLUSH",
                        "LOCK", "UNLOCK", "BEGIN", "START", "COMMIT", "ROLLBACK",
                        "GRANT", "REVOKE", "TRUNCATE"):
            if sql_upper.startswith(keyword) or f"\n{keyword}" in sql_upper or f" {keyword} " in sql_upper:
                return keyword
        return "UNKNOWN"

    def _regex_fallback_create_table_props(self, sql: str, parsed: ParsedSQL):
        """当 AST 解析失败或回退时，通过正则预防护航 DDL 关键属性（主键/引擎/字符集/注释）"""
        clean = re.sub(r'--[^\n]*', '', sql)
        clean = re.sub(r'/\*.*?\*/', '', clean, flags=re.DOTALL).strip()
        clean_lower = clean.lower()
        if "create table" in clean_lower:
            parsed.is_create_table = True
            if parsed.sql_type == "UNKNOWN":
                parsed.sql_type = "CREATE TABLE"
            # 提取主键
            if re.search(r'\bprimary\s+key\b', clean, re.IGNORECASE):
                parsed.has_primary_key = True
            # 提取引擎
            eng_m = re.search(r'\bengine\s*=\s*([a-zA-Z0-9_]+)\b', clean, re.IGNORECASE)
            if eng_m:
                parsed.engine = eng_m.group(1)
            # 提取字符集
            cs_m = re.search(r'\b(?:default\s+)?charset\s*=\s*([a-zA-Z0-9_]+)\b', clean, re.IGNORECASE)
            if cs_m:
                parsed.charset = cs_m.group(1)
            # 提取表级注释
            if re.search(r'\bcomment\s*=\s*[\'"]', clean, re.IGNORECASE):
                parsed.has_table_comment = True

    def _get_sql_type(self, ast) -> str:
        """从AST获取SQL类型"""
        if isinstance(ast, exp.Select):
            return "SELECT"
        elif isinstance(ast, exp.Insert):
            kind = ast.args.get("kind", "")
            return "REPLACE" if kind == "REPLACE" else "INSERT"
        elif isinstance(ast, exp.Update):
            return "UPDATE"
        elif isinstance(ast, exp.Delete):
            return "DELETE"
        elif isinstance(ast, exp.Create):
            kind = ast.args.get("kind", "")
            return f"CREATE {kind}".strip().upper() if kind else "CREATE"
        elif isinstance(ast, exp.Alter):
            return "ALTER"
        elif isinstance(ast, exp.Drop):
            return "DROP"
        return "UNKNOWN"

    # ── SELECT 解析 ──────────────────────────────────────

    def _parse_select(self, ast: exp.Select, parsed: ParsedSQL):
        """解析 SELECT 语句"""
        parsed.tables = self._extract_tables(ast)

        parsed.select_fields = []
        for e in ast.expressions:
            if isinstance(e, exp.Star):
                parsed.has_wildcard_select = True
                parsed.select_fields.append("*")
            else:
                parsed.select_fields.append(e.sql(dialect=self.dialect))

        where = ast.args.get("where")
        if where:
            parsed.has_where = True
            parsed.where_clause = where.sql(dialect=self.dialect)
            parsed.where_columns = self._extract_where_columns(where)
            parsed.where_has_function = self._check_where_has_function(where)

        order = ast.args.get("order")
        if order:
            parsed.has_order_by = True
            parsed.order_by_random = self._check_order_by_random(order)

        parsed.subquery_depth = self._calc_subquery_depth(ast)
        parsed.join_count = self._count_joins(ast)
        if parsed.join_count > 0:
            parsed.has_explicit_join = True

    # ── INSERT 解析 ──────────────────────────────────────

    def _parse_insert(self, ast: exp.Insert, parsed: ParsedSQL):
        """解析 INSERT 语句"""
        target = ast.args.get("this")
        if target:
            if isinstance(target, exp.Schema):
                table_obj = target.this
                if table_obj:
                    table_name = table_obj.sql(dialect=self.dialect)
                    parsed.tables.append(table_name)
                # 提取INSERT列名
                for col_expr in target.expressions:
                    if isinstance(col_expr, exp.ColumnDef):
                        parsed.insert_columns.append(col_expr.name)
                    elif isinstance(col_expr, exp.Identifier):
                        parsed.insert_columns.append(col_expr.name)
                    elif isinstance(col_expr, exp.Column):
                        parsed.insert_columns.append(col_expr.name)
                # 如果Schema有expressions但是没有提取到列名
                if not parsed.insert_columns and target.expressions:
                    for expr in target.expressions:
                        name = expr.name if hasattr(expr, 'name') else str(expr)
                        if name and name not in parsed.insert_columns:
                            parsed.insert_columns.append(name)
            else:
                parsed.tables.append(target.sql(dialect=self.dialect))

        # 如果没有提取到列名，标记为 unnamed insert
        if not parsed.insert_columns and target and not isinstance(target, exp.Schema):
            parsed.has_unnamed_insert = True
        elif isinstance(target, exp.Schema) and not target.expressions:
            parsed.has_unnamed_insert = True

        # INSERT ... SELECT
        select = ast.args.get("expression")
        if isinstance(select, exp.Select):
            self._parse_select(select, parsed)
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

        schema = ast.args.get("this")

        # 提取表名
        table_name = ""
        if isinstance(schema, exp.Schema):
            table_obj = schema.this
            if table_obj:
                table_name = table_obj.sql(dialect=self.dialect)
                parsed.tables.append(table_name)
        elif schema:
            table_name = schema.sql(dialect=self.dialect)
            parsed.tables.append(table_name)

        # 复数检查
        if table_name:
            parsed.table_name_plural = self._check_plural(table_name)

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
                    # 提取列注释
                    comment = self._extract_column_comment(col_def)
                    if comment:
                        parsed.column_comments[col_info["name"]] = comment
                elif isinstance(col_def, exp.PrimaryKey):
                    parsed.has_primary_key = True
                elif isinstance(col_def, exp.IndexColumnConstraint):
                    idx_info = self._parse_index_constraint(col_def)
                    if idx_info:
                        parsed.indexes.append(idx_info)
                        parsed.index_definitions.append(idx_info)
                elif type(col_def).__name__ == "UniqueColumnConstraint":
                    # sqlglot UniqueColumnConstraint: 表级 UNIQUE KEY/INDEX
                    idx_info = self._parse_unique_constraint(col_def)
                    if idx_info:
                        parsed.indexes.append(idx_info)
                        parsed.index_definitions.append(idx_info)
                # 检查表级 COMMENT
                elif type(col_def).__name__ in ("CommentColumnConstraint", "CommentColumnConstraint"):
                    parsed.has_table_comment = True

        # 检查约束中的主键和外键
        if isinstance(schema, exp.Schema):
            for pk in schema.find_all(exp.PrimaryKey):
                parsed.has_primary_key = True
            for fk in schema.find_all(exp.ForeignKey):
                parsed.has_foreign_key = True

        # 检查列定义中的主键标记
        for col in parsed.columns:
            if col.get("is_primary_key"):
                parsed.has_primary_key = True

        # 解析表选项 (ENGINE, CHARSET, COMMENT 等)
        properties = ast.args.get("properties")
        if properties:
            self._parse_table_properties(properties, parsed)

        # 检查表级COMMENT（如果table_options或properties中存在）
        if not parsed.has_table_comment and properties and hasattr(properties, "expressions"):
            for prop in properties.expressions:
                try:
                    if isinstance(prop, exp.Property) and getattr(prop, "name", "").upper() == "COMMENT":
                        parsed.has_table_comment = True
                        break
                except Exception:
                    continue

    def _parse_index_constraint(self, col_def) -> dict:
        """解析 IndexColumnConstraint"""
        idx_name_node = col_def.args.get("this")
        idx_name = idx_name_node.sql(dialect=self.dialect) if idx_name_node else ""
        idx_cols = []
        idx_type = "NORMAL"
        for ordered_expr in col_def.expressions:
            col_node = ordered_expr.args.get("this") if hasattr(ordered_expr, 'args') else None
            if col_node:
                col_name = col_node.sql(dialect=self.dialect).strip('`"')
                if col_name:
                    idx_cols.append(col_name)
        # 判断索引类型
        def_str = str(col_def).upper()
        if "PRIMARY" in def_str:
            idx_type = "PRIMARY"
        elif "UNIQUE" in def_str:
            idx_type = "UNIQUE"
        elif "FULLTEXT" in def_str:
            idx_type = "FULLTEXT"
        if idx_cols:
            return {"name": idx_name, "columns": idx_cols, "type": idx_type}
        return {}

    def _parse_unique_constraint(self, col_def) -> dict:
        """解析 UniqueColumnConstraint (表级 UNIQUE KEY/INDEX)"""
        idx_name = ""
        idx_cols = []
        # UniqueColumnConstraint 的 this 可能是 IndexColumnConstraint 或直接的列列表
        this_node = col_def.args.get("this")
        if this_node:
            if type(this_node).__name__ == "IndexColumnConstraint":
                # UNIQUE KEY uk_name (col1, col2)
                name_node = this_node.args.get("this")
                if name_node:
                    idx_name = name_node.sql(dialect=self.dialect) if hasattr(name_node, 'sql') else str(name_node)
                for ordered_expr in this_node.expressions:
                    col_node = ordered_expr.args.get("this") if hasattr(ordered_expr, 'args') else None
                    if col_node:
                        col_name = col_node.sql(dialect=self.dialect).strip('`"')
                        if col_name:
                            idx_cols.append(col_name)
            else:
                # 直接的列引用
                name_str = this_node.sql(dialect=self.dialect) if hasattr(this_node, 'sql') else str(this_node)
                idx_name = name_str
        # 从 expressions 中提取列名
        for expr in col_def.expressions:
            if hasattr(expr, 'args'):
                col_node = expr.args.get("this")
                if col_node:
                    col_name = col_node.sql(dialect=self.dialect).strip('`"')
                    if col_name:
                        idx_cols.append(col_name)
        if idx_cols:
            return {"name": idx_name or "UNIQUE", "columns": idx_cols, "type": "UNIQUE"}
        return {}

    def _extract_column_comment(self, col_def: exp.ColumnDef) -> str:
        """提取列注释"""
        for constraint in col_def.find_all(exp.ColumnConstraint):
            c_kind = constraint.args.get("kind")
            if type(c_kind).__name__ == "CommentColumnConstraint":
                if c_kind.this:
                    return c_kind.this.sql(dialect=self.dialect).strip("'\"")
        return ""

    def _check_plural(self, name: str) -> bool:
        """检查表名是否为复数"""
        name = name.strip('`"').lower()
        if not name or name in self.PLURAL_IGNORE:
            return False
        if name.endswith("ies"):
            return True
        if name.endswith("ses") or name.endswith("es"):
            base = name[:-2]
            return base not in self.PLURAL_IGNORE
        if name.endswith("s") and not name.endswith("ss"):
            return True
        return False

    def _parse_column_def(self, col_def: exp.ColumnDef) -> dict:
        """解析单个列定义"""
        col_name = col_def.name
        data_type = col_def.args.get("kind")
        raw_type = data_type.sql(dialect=self.dialect) if data_type else ""

        type_name = ""
        if data_type and data_type.this is not None:
            dtype = data_type.this
            if hasattr(dtype, 'name'):
                type_name = dtype.name.upper()
            elif hasattr(dtype, 'value'):
                type_name = str(dtype.value).upper()

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
            "has_comment": False,
            "comment": "",
        }

        for constraint in col_def.find_all(exp.ColumnConstraint):
            c_kind = constraint.args.get("kind")
            if isinstance(c_kind, exp.PrimaryKeyColumnConstraint):
                info["is_primary_key"] = True
            elif isinstance(c_kind, exp.NotNullColumnConstraint):
                info["is_not_null"] = True
            elif isinstance(c_kind, exp.DefaultColumnConstraint):
                info["has_default"] = True
                info["default_value"] = c_kind.this.sql(dialect=self.dialect) if c_kind.this else None

        if data_type:
            size = data_type.args.get("expressions")
            if size and len(size) > 0:
                try:
                    info["length"] = int(size[0].sql(dialect=self.dialect))
                except (ValueError, IndexError):
                    pass

        return info

    def _parse_table_properties(self, properties, parsed: ParsedSQL):
        """解析表选项 (ENGINE, CHARSET, COMMENT 等)"""
        for prop in getattr(properties, "expressions", []):
            try:
                if isinstance(prop, exp.EngineProperty):
                    engine_var = prop.this
                    if engine_var:
                        parsed.engine = engine_var.name.upper() if hasattr(engine_var, 'name') else str(engine_var).upper()
                        parsed.table_options["engine"] = parsed.engine
                elif isinstance(prop, exp.CharacterSetProperty):
                    charset_var = prop.this
                    if charset_var:
                        parsed.charset = charset_var.name.upper() if hasattr(charset_var, 'name') else str(charset_var).upper()
                        parsed.table_options["charset"] = parsed.charset
                elif isinstance(prop, exp.SchemaCommentProperty):
                    parsed.has_table_comment = True
                    c_val = prop.this
                    if c_val:
                        parsed.table_options["COMMENT"] = c_val.this if hasattr(c_val, 'this') else str(c_val)
                elif isinstance(prop, exp.Property):
                    key = prop.name.upper() if hasattr(prop, 'name') else ""
                    val = prop.args.get("value")
                    if key and val is not None:
                        try:
                            parsed.table_options[key] = val.sql(dialect=self.dialect)
                        except Exception:
                            parsed.table_options[key] = str(val)
                    if key == "COMMENT" or "COMMENT" in prop.__class__.__name__.upper():
                        parsed.has_table_comment = True
            except Exception:
                continue

    # ── ALTER TABLE 解析 ─────────────────────────────────

    def _parse_alter(self, ast: exp.Alter, parsed: ParsedSQL):
        """解析 ALTER TABLE 语句"""
        parsed.is_alter_table = True
        table = ast.args.get("this")
        if table:
            parsed.tables.append(table.sql(dialect=self.dialect))

        # 尝试提取ALTER操作
        for action in ast.expressions if hasattr(ast, 'expressions') else []:
            action_info = {"action": "modify", "column": "", "old_type": "", "new_type": ""}
            if isinstance(action, exp.AlterColumn):
                action_info["action"] = "modify"
                if hasattr(action, 'this') and action.this:
                    action_info["column"] = action.this.name
            elif isinstance(action, exp.RenameColumn):
                action_info["action"] = "rename"
                if hasattr(action, 'this') and action.this:
                    action_info["column"] = action.this.name
            parsed.alter_actions.append(action_info)

    # ── DROP 解析 ────────────────────────────────────────

    def _parse_drop(self, ast: exp.Drop, parsed: ParsedSQL):
        """解析 DROP 语句"""
        table = ast.args.get("this")
        if table:
            parsed.tables.append(table.sql(dialect=self.dialect))

    # ── 通用解析 ─────────────────────────────────────────

    def _parse_common(self, ast, parsed: ParsedSQL):
        """通用解析：JOIN类型、子查询等"""
        # 检测显式JOIN
        for join in ast.find_all(exp.Join):
            parsed.has_explicit_join = True
            break

    # ── 通用辅助方法 ─────────────────────────────────────

    def _extract_tables(self, ast) -> list[str]:
        """从AST提取所有表名（不含别名）"""
        tables = []
        for table in ast.find_all(exp.Table):
            name = table.name
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
        """检查WHERE条件中是否包含函数调用或索引失效模式"""
        _op_names = {
            'And', 'Or', 'Not', 'EQ', 'NEQ', 'GT', 'GTE', 'LT', 'LTE',
            'Is', 'IsNot', 'In', 'Between', 'Like', 'ILike',
            'Paren', 'Condition',
        }
        for node in where_node.walk():
            node_type = type(node).__name__
            if isinstance(node, exp.Func) and node_type not in _op_names:
                return True
            if isinstance(node, exp.Like):
                pattern = node.args.get("expression")
                if pattern:
                    pattern_sql = pattern.sql().strip("'\"")
                    if pattern_sql.startswith("%"):
                        return True
            if isinstance(node, exp.Or):
                return True
        return False

    def _check_order_by_random(self, order_node) -> bool:
        """检查 ORDER BY 中是否包含 RAND()"""
        for expression in order_node.expressions:
            expr = expression.this
            if isinstance(expr, exp.Anonymous) and expr.name.upper() in ("RAND", "RANDOM"):
                return True
            if isinstance(expr, exp.Func) and expr.sql(dialect=self.dialect).upper().startswith("RAND"):
                return True
        return False

    def _calc_subquery_depth(self, ast) -> int:
        """计算子查询嵌套深度"""
        max_depth = 0
        stack = [(ast, 0)]
        while stack:
            node, depth = stack.pop()
            new_depth = depth
            if isinstance(node, exp.Subquery):
                new_depth = depth + 1
                max_depth = max(max_depth, new_depth)
            elif isinstance(node, exp.Select) and depth > 0:
                new_depth = depth + 1
                max_depth = max(max_depth, new_depth)
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
