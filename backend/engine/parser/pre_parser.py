"""
正则预解析器 (PreParser)：负责多语句安全切分、注释清洗与 Hint 提取
"""
import re
from backend.engine.parser.base_parser import BaseParser


class PreParser(BaseParser):
    def parse(self, sql: str) -> dict:
        """
        预解析 SQL 文本。

        Returns:
            {"statements": list[str], "hints": list[str], "clean_sql": str}
        """
        clean_sql = self.clean_comments(sql)
        hints = self.extract_hints(sql)
        statements = self.split_statements(clean_sql)
        return {
            "statements": statements,
            "hints": hints,
            "clean_sql": clean_sql
        }

    @staticmethod
    def clean_comments(sql: str) -> str:
        """移除 -- 和 /* */ 注释，但保留 TDSQL 注释 Hint 如 /*proxy*/"""
        # 保护 TDSQL proxy hint
        protected = sql.replace("/*proxy*/", "__TDSQL_PROXY_HINT__")
        # 移除常规单行注释
        lines = []
        for line in protected.splitlines():
            s = line.strip()
            if not s.startswith("--"):
                lines.append(line)
        cleaned = "\n".join(lines)
        # 恢复 hint
        return cleaned.replace("__TDSQL_PROXY_HINT__", "/*proxy*/")

    @staticmethod
    def extract_hints(sql: str) -> list[str]:
        """提取 /*+ ... */ 或 /*proxy*/ 等 Hints"""
        hints = []
        if "/*proxy*/" in sql:
            hints.append("proxy")
        matches = re.findall(r"/\*\+\s*(.*?)\s*\*/", sql)
        hints.extend(matches)
        return hints

    @staticmethod
    def split_statements(sql: str) -> list[str]:
        """安全拆分多条 SQL 语句"""
        from backend.services.database import split_sql_statements
        return [s.strip() for s in split_sql_statements(sql) if s.strip()]
