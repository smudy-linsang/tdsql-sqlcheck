"""
TDSQL SQL审核工具 - 数据库上线前Schema检查引擎 (V1.0)

将 tdsql_12.sh 脚本的12项上线前检查集成到系统中，
通过连接池直连目标实例 information_schema 执行检查。

检查项涵盖：字符集/排序规则、表名规范、索引数量、主键、字段长度、
注释完整性、字段数量、timestamp类型等。
"""
import logging
from typing import Optional

logger = logging.getLogger("tdsql.schema_inspector")

# 系统数据库排除列表
SYSTEM_DBS = (
    "'__tencentdb__','information_schema','mysql','performance_schema',"
    "'query_rewrite','sys','sysdb','test','xa'"
)


class SchemaInspector:
    """数据库上线前Schema检查引擎"""

    CHECKS = [
        {
            "id": "C01",
            "name": "字符编码非utf8mb4的表",
            "severity": "ERROR",
            "sql": (
                "SELECT table_schema AS `数据库`, table_collation AS `排序规则`, "
                "COUNT(*) AS `表数量` "
                "FROM information_schema.TABLES "
                "WHERE table_collation IS NOT NULL "
                "AND table_schema NOT IN ({sys}) "
                "AND table_schema NOT LIKE '%%nacos%%' "
                "GROUP BY table_schema, table_collation "
                "HAVING table_collation NOT LIKE 'utf8mb4%%'"
            ),
            "suggestion": "建议将表字符集统一为utf8mb4，排序规则根据业务需求选择utf8mb4_general_ci或utf8mb4_bin",
        },
        {
            "id": "C02",
            "name": "字符编码非utf8mb4的列",
            "severity": "ERROR",
            "sql": (
                "SELECT TABLE_SCHEMA AS `数据库`, TABLE_NAME AS `表名`, "
                "COLUMN_NAME AS `列名`, COLUMN_TYPE AS `类型`, "
                "COLLATION_NAME AS `排序规则` "
                "FROM information_schema.COLUMNS "
                "WHERE table_schema NOT IN ({sys}) "
                "AND table_schema NOT LIKE '%%nacos%%' "
                "AND DATA_TYPE NOT IN ('int','datetime','bigint','tinyint','double',"
                "'timestamp','longblob','date','decimal','blob','smallint','float',"
                "'mediumtext','bit') "
                "AND COLLATION_NAME NOT LIKE 'utf8mb4%%'"
            ),
            "suggestion": "建议将列字符集统一为utf8mb4，确保与表级排序规则一致",
        },
        {
            "id": "C03",
            "name": "大小写敏感未设置的表",
            "severity": "WARNING",
            "sql": (
                "SELECT table_schema AS `数据库`, table_name AS `表名`, "
                "table_collation AS `排序规则` "
                "FROM information_schema.TABLES "
                "WHERE table_collation IS NOT NULL "
                "AND table_schema NOT IN ({sys}) "
                "AND table_schema NOT LIKE '%%nacos%%' "
                "AND TABLE_COLLATION <> 'utf8mb4_bin'"
            ),
            "suggestion": "如需大小写敏感，建议将表排序规则改为utf8mb4_bin",
        },
        {
            "id": "C04",
            "name": "大小写敏感未设置的列",
            "severity": "WARNING",
            "sql": (
                "SELECT TABLE_SCHEMA AS `数据库`, TABLE_NAME AS `表名`, "
                "COLUMN_NAME AS `列名`, COLUMN_TYPE AS `类型`, "
                "COLLATION_NAME AS `排序规则` "
                "FROM information_schema.COLUMNS "
                "WHERE table_schema NOT IN ({sys}) "
                "AND table_schema NOT LIKE '%%nacos%%' "
                "AND DATA_TYPE NOT IN ('int','datetime','bigint','tinyint','double',"
                "'timestamp','longblob','date','decimal','blob','smallint','float',"
                "'mediumtext','bit') "
                "AND COLLATION_NAME <> 'utf8mb4_bin'"
            ),
            "suggestion": "如需大小写敏感，建议将列排序规则改为utf8mb4_bin",
        },
        {
            "id": "C05",
            "name": "表名超过32个字符",
            "severity": "WARNING",
            "sql": (
                "SELECT table_schema AS `数据库`, table_name AS `表名`, "
                "LENGTH(table_name) AS `字符数` "
                "FROM information_schema.TABLES "
                "WHERE LENGTH(table_name) >= 32 "
                "AND table_schema NOT IN ({sys}) "
                "ORDER BY LENGTH(table_name) DESC"
            ),
            "suggestion": "TDSQL建议表名不超过32个字符，请缩短表名",
        },
        {
            "id": "C06",
            "name": "索引数量>=5的表",
            "severity": "WARNING",
            "sql": (
                "SELECT TABLE_SCHEMA AS `数据库`, TABLE_NAME AS `表名`, "
                "COUNT(DISTINCT INDEX_NAME) AS `索引数` "
                "FROM information_schema.STATISTICS "
                "WHERE table_schema NOT IN ({sys}) "
                "AND TABLE_NAME NOT LIKE '%%_tdsql_subp_auto_%%' "
                "GROUP BY TABLE_SCHEMA, TABLE_NAME "
                "HAVING COUNT(DISTINCT INDEX_NAME) >= 5 "
                "ORDER BY COUNT(DISTINCT INDEX_NAME) DESC"
            ),
            "suggestion": "单表索引过多（>=5）会影响写入性能，建议评估并合并冗余索引",
        },
        {
            "id": "C07",
            "name": "无主键的表",
            "severity": "ERROR",
            "sql": (
                "SELECT table_schema AS `数据库`, table_name AS `表名` "
                "FROM information_schema.TABLES "
                "WHERE table_type = 'BASE TABLE' "
                "AND table_name NOT IN ("
                "  SELECT table_name FROM information_schema.TABLE_CONSTRAINTS "
                "  WHERE CONSTRAINT_TYPE = 'PRIMARY KEY'"
                ") "
                "AND table_schema NOT IN ({sys})"
            ),
            "suggestion": "TDSQL分布式架构要求所有表必须有主键，请添加主键列",
        },
        {
            "id": "C08",
            "name": "varchar字段长度>500",
            "severity": "WARNING",
            "sql": (
                "SELECT table_schema AS `数据库`, table_name AS `表名`, "
                "column_name AS `列名`, column_type AS `类型` "
                "FROM information_schema.COLUMNS "
                "WHERE data_type = 'varchar' "
                "AND CHARACTER_MAXIMUM_LENGTH > 500 "
                "AND table_schema NOT IN ({sys}) "
                "AND table_schema NOT LIKE '%%nacos%%'"
            ),
            "suggestion": "varchar长度过大（>500）建议评估是否需要，过长字段可考虑改为TEXT或拆分表",
        },
        {
            "id": "C09",
            "name": "无注释的列",
            "severity": "INFO",
            "sql": (
                "SELECT table_schema AS `数据库`, table_name AS `表名`, "
                "column_name AS `列名`, column_comment AS `当前注释` "
                "FROM information_schema.COLUMNS "
                "WHERE column_comment = '' "
                "AND table_schema NOT IN ({sys}) "
                "AND table_schema NOT LIKE '%%nacos%%'"
            ),
            "suggestion": "建议为所有列添加注释，便于维护和交接",
        },
        {
            "id": "C10",
            "name": "无注释的表",
            "severity": "INFO",
            "sql": (
                "SELECT table_schema AS `数据库`, table_name AS `表名`, "
                "table_comment AS `当前注释` "
                "FROM information_schema.TABLES "
                "WHERE table_comment = '' "
                "AND table_type = 'BASE TABLE' "
                "AND table_schema NOT IN ({sys}) "
                "AND table_schema NOT LIKE '%%nacos%%'"
            ),
            "suggestion": "建议为所有表添加注释，说明表的用途和业务含义",
        },
        {
            "id": "C11",
            "name": "字段数>50的表",
            "severity": "WARNING",
            "sql": (
                "SELECT TABLE_SCHEMA AS `数据库`, TABLE_NAME AS `表名`, "
                "COUNT(*) AS `字段数` "
                "FROM information_schema.COLUMNS "
                "WHERE table_schema NOT IN ({sys}) "
                "AND table_schema NOT LIKE '%%nacos%%' "
                "GROUP BY TABLE_SCHEMA, TABLE_NAME "
                "HAVING COUNT(*) > 50 "
                "ORDER BY COUNT(*) DESC"
            ),
            "suggestion": "单表字段过多（>50）建议拆分为多表或使用JSON字段存储非核心字段",
        },
        {
            "id": "C12",
            "name": "timestamp类型字段",
            "severity": "WARNING",
            "sql": (
                "SELECT TABLE_SCHEMA AS `数据库`, TABLE_NAME AS `表名`, "
                "COLUMN_NAME AS `列名`, COLUMN_TYPE AS `类型` "
                "FROM information_schema.COLUMNS "
                "WHERE DATA_TYPE = 'timestamp' "
                "AND TABLE_SCHEMA NOT IN ({sys})"
            ),
            "suggestion": "TDSQL建议使用datetime替代timestamp，timestamp存在时区和范围限制（1970-2038）",
        },
    ]

    def inspect(self, pool, database_filter: str = "") -> list[dict]:
        """
        执行全部12项检查，返回检查结果列表。

        Args:
            pool: TDSQLConnectionPool 连接池实例
            database_filter: 可选，仅检查指定数据库

        Returns:
            list[dict]: 每项检查的结果，包含:
                - id: 检查ID (C01-C12)
                - name: 检查名称
                - severity: ERROR/WARNING/INFO
                - count: 问题数量
                - rows: 问题明细列表
                - columns: 列标题列表
                - suggestion: 修复建议
                - error: 执行错误信息（如有）
        """
        results = []
        for check in self.CHECKS:
            result = self._run_check(pool, check, database_filter)
            results.append(result)
        return results

    def _run_check(self, pool, check: dict, database_filter: str = "") -> dict:
        """执行单项检查"""
        sql = check["sql"].format(sys=SYSTEM_DBS)

        # 可选：按数据库过滤
        if database_filter:
            # 在WHERE条件中追加数据库过滤
            sql = sql.replace(
                f"table_schema NOT IN ({SYSTEM_DBS})",
                f"table_schema NOT IN ({SYSTEM_DBS}) AND table_schema = '{database_filter}'"
            )
            # 处理TABLE_SCHEMA大写形式
            sql = sql.replace(
                f"TABLE_SCHEMA NOT IN ({SYSTEM_DBS})",
                f"TABLE_SCHEMA NOT IN ({SYSTEM_DBS}) AND TABLE_SCHEMA = '{database_filter}'"
            )

        result = {
            "id": check["id"],
            "name": check["name"],
            "severity": check["severity"],
            "suggestion": check["suggestion"],
            "count": 0,
            "rows": [],
            "columns": [],
            "error": None,
        }

        try:
            rows = pool._execute(sql)
            if rows:
                result["columns"] = list(rows[0].keys())
                result["rows"] = rows
                result["count"] = len(rows)
            logger.info(f"Schema检查 {check['id']} {check['name']}: {result['count']}个问题")
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"Schema检查 {check['id']} 执行失败: {e}")

        return result

    def get_summary(self, results: list[dict]) -> dict:
        """生成检查摘要统计"""
        summary = {"total": 0, "error": 0, "warning": 0, "info": 0, "checks_passed": 0, "checks_failed": 0}
        for r in results:
            summary["total"] += r["count"]
            if r["severity"] == "ERROR":
                summary["error"] += r["count"]
            elif r["severity"] == "WARNING":
                summary["warning"] += r["count"]
            else:
                summary["info"] += r["count"]
            if r["count"] > 0:
                summary["checks_failed"] += 1
            else:
                summary["checks_passed"] += 1
        return summary
