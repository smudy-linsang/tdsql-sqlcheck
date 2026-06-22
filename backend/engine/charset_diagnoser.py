"""
TDSQL SQL审核工具 - 字符集诊断引擎 (V1.0)

诊断数据库实例、库、表、列四个层级的字符集和排序规则一致性。
"""
from typing import Optional

from backend.models import CharsetDiagnosticReport


class CharsetDiagnoser:
    """字符集/排序规则一致性诊断"""

    # 6套诊断SQL
    DIAGNOSTIC_SQLS = {
        "instance_default": "SHOW VARIABLES WHERE Variable_name IN ('character_set_server', 'collation_server', 'character_set_database', 'collation_database')",
        "database_charset": "SELECT SCHEMA_NAME, DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA",
        "table_charset": "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_TYPE='BASE TABLE'",
        "column_charset": "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, CHARACTER_SET_NAME, COLLATION_NAME, DATA_TYPE FROM information_schema.COLUMNS WHERE DATA_TYPE IN ('varchar', 'char', 'text', 'enum', 'set')",
        "mismatch_columns": """SELECT c1.TABLE_SCHEMA, c1.TABLE_NAME, c1.COLUMN_NAME, c1.COLLATION_NAME as col_collation, t.TABLE_COLLATION as tbl_collation
            FROM information_schema.COLUMNS c1
            JOIN information_schema.TABLES t ON c1.TABLE_SCHEMA=t.TABLE_SCHEMA AND c1.TABLE_NAME=t.TABLE_NAME
            WHERE c1.DATA_TYPE IN ('varchar','char','text')
            AND c1.COLLATION_NAME IS NOT NULL
            AND c1.COLLATION_NAME != t.TABLE_COLLATION""",
        "join_collation_mismatch": """SELECT c1.TABLE_SCHEMA, c1.TABLE_NAME, c1.COLUMN_NAME, c1.COLLATION_NAME as coll1,
                   c2.TABLE_SCHEMA as tbl2, c2.TABLE_NAME as col_tbl2, c2.COLUMN_NAME as col2, c2.COLLATION_NAME as coll2
            FROM information_schema.COLUMNS c1, information_schema.COLUMNS c2
            WHERE c1.COLLATION_NAME != c2.COLLATION_NAME
            AND c1.DATA_TYPE IN ('varchar','char') AND c2.DATA_TYPE IN ('varchar','char')""",
    }

    TARGET_CHARSET = "utf8mb4"
    TARGET_COLLATION = "utf8mb4_general_ci"

    def diagnose_from_query_results(self, results: dict) -> CharsetDiagnosticReport:
        """
        从6套诊断SQL的查询结果生成诊断报告。

        Args:
            results: {"instance_default": [...], "database_charset": [...], ...}

        Returns:
            CharsetDiagnosticReport 诊断报告
        """
        report = CharsetDiagnosticReport()
        issues = []

        # 1. 检查实例默认字符集
        instance_rows = results.get("instance_default", [])
        instance_defaults = {}
        for row in instance_rows:
            if isinstance(row, dict):
                var_name = row.get("Variable_name", row.get("variable_name", ""))
                var_value = row.get("Variable_value", row.get("variable_value", ""))
                instance_defaults[var_name] = var_value
                if "character_set_server" in var_name and var_value.lower() != self.TARGET_CHARSET:
                    issues.append({
                        "level": "instance",
                        "target": var_name,
                        "current": var_value,
                        "expected": self.TARGET_CHARSET,
                        "message": f"实例默认字符集 {var_name}={var_value}，建议改为 {self.TARGET_CHARSET}",
                    })
                if "collation_server" in var_name and self.TARGET_COLLATION not in var_value.lower():
                    issues.append({
                        "level": "instance",
                        "target": var_name,
                        "current": var_value,
                        "expected": self.TARGET_COLLATION,
                        "message": f"实例默认排序规则 {var_name}={var_value}，建议改为 {self.TARGET_COLLATION}",
                    })
        report.instance_defaults = instance_defaults

        # 2. 检查库级别字符集
        db_rows = results.get("database_charset", [])
        for row in db_rows:
            if isinstance(row, dict):
                schema = row.get("SCHEMA_NAME", row.get("schema_name", ""))
                charset = row.get("DEFAULT_CHARACTER_SET_NAME", row.get("default_character_set_name", ""))
                if charset and charset.lower() != self.TARGET_CHARSET and schema not in ("information_schema", "mysql", "performance_schema", "sys"):
                    issues.append({
                        "level": "database",
                        "target": schema,
                        "current": charset,
                        "expected": self.TARGET_CHARSET,
                        "message": f"数据库 {schema} 字符集为 {charset}，建议改为 {self.TARGET_CHARSET}",
                    })

        # 3. 检查表级别字符集不一致
        table_rows = results.get("table_charset", [])
        for row in table_rows:
            if isinstance(row, dict):
                schema = row.get("TABLE_SCHEMA", row.get("table_schema", ""))
                table = row.get("TABLE_NAME", row.get("table_name", ""))
                collation = row.get("TABLE_COLLATION", row.get("table_collation", ""))
                if collation and self.TARGET_COLLATION not in collation.lower() and schema not in ("information_schema", "mysql", "performance_schema", "sys"):
                    issues.append({
                        "level": "table",
                        "target": f"{schema}.{table}",
                        "current": collation,
                        "expected": self.TARGET_COLLATION,
                        "message": f"表 {schema}.{table} 排序规则为 {collation}，建议改为 {self.TARGET_COLLATION}",
                    })

        # 4. 检查列级别不一致
        mismatch_rows = results.get("mismatch_columns", [])
        for row in mismatch_rows:
            if isinstance(row, dict):
                schema = row.get("TABLE_SCHEMA", row.get("table_schema", ""))
                table = row.get("TABLE_NAME", row.get("table_name", ""))
                col = row.get("COLUMN_NAME", row.get("column_name", ""))
                col_coll = row.get("col_collation", row.get("COLLATION_NAME", ""))
                tbl_coll = row.get("tbl_collation", row.get("TABLE_COLLATION", ""))
                issues.append({
                    "level": "column",
                    "target": f"{schema}.{table}.{col}",
                    "current": col_coll,
                    "expected": tbl_coll,
                    "message": f"列 {schema}.{table}.{col} 排序规则({col_coll})与表({tbl_coll})不一致",
                })

        report.issues = issues
        report.summary = {
            "total_issues": len(issues),
            "by_level": {
                level: sum(1 for i in issues if i.get("level") == level)
                for level in ("instance", "database", "table", "column")
            },
            "target_charset": self.TARGET_CHARSET,
            "target_collation": self.TARGET_COLLATION,
        }
        return report

    def get_diagnostic_sqls(self) -> dict:
        """获取6套诊断SQL"""
        return self.DIAGNOSTIC_SQLS.copy()
