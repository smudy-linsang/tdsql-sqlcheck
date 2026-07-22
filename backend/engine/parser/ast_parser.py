"""
AST 语法树解析器 (ASTParser)
"""
from typing import Optional
import sqlglot
import sqlglot.expressions as exp
from backend.engine.parser.base_parser import BaseParser


class ASTParser(BaseParser):
    def parse(self, sql: str) -> Optional[exp.Expression]:
        """使用 sqlglot 将 SQL 解析为 AST 表达式"""
        try:
            return sqlglot.parse_one(sql, read="mysql")
        except Exception:
            return None

    def get_sql_type(self, expression: Optional[exp.Expression]) -> str:
        """从 AST 获取 SQL 类型 (SELECT / INSERT / UPDATE / DELETE / DDL 等)"""
        if not expression:
            return "UNKNOWN"
        if isinstance(expression, exp.Select):
            return "SELECT"
        if isinstance(expression, exp.Insert):
            return "INSERT"
        if isinstance(expression, exp.Update):
            return "UPDATE"
        if isinstance(expression, exp.Delete):
            return "DELETE"
        if isinstance(expression, (exp.Create, exp.Drop, exp.Alter)):
            return "DDL"
        return "OTHER"
