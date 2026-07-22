"""
数据库与表元数据抽取器 (MetadataFetcher)
"""
class MetadataFetcher:
    def __init__(self, pool):
        self.pool = pool

    def fetch_databases(self) -> list[str]:
        """获取物理节点或逻辑集群上的所有数据库名"""
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SHOW DATABASES")
            rows = cursor.fetchall()
            dbs = []
            for r in rows:
                db_name = list(r.values())[0]
                if db_name not in ("information_schema", "mysql", "performance_schema", "sys"):
                    dbs.append(db_name)
            return dbs

    def fetch_table_schema(self, db_name: str, table_name: str) -> dict:
        """获取表结构、字段、引擎及字符集元数据"""
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT TABLE_NAME, ENGINE, TABLE_COLLATION
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            """, (db_name, table_name))
            table_info = cursor.fetchone()

            cursor.execute("""
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_TYPE
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
            """, (db_name, table_name))
            columns = cursor.fetchall()

            return {"table": table_info, "columns": columns}
