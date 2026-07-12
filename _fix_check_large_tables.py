"""Fix check_large_tables to use information_schema.PARTITIONS"""
import re

with open('backend/services/tdsql_connector.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_func = '''    def check_large_tables(self, database: str = None, threshold_gb: float = 1.0) -> list[dict]:
        """检查大表（参考大表治理规范）"""
        db = database or self.config.database
        threshold_bytes = int(threshold_gb * 1024 * 1024 * 1024)

        return self._execute("""
            SELECT TABLE_NAME,
                   ROUND((DATA_LENGTH + INDEX_LENGTH)/1024/1024/1024, 2) AS size_gb,
                   TABLE_ROWS,
                   ROUND(DATA_LENGTH/1024/1024, 2) AS data_mb,
                   ROUND(INDEX_LENGTH/1024/1024, 2) AS index_mb,
                   CASE
                     WHEN (DATA_LENGTH + INDEX_LENGTH) >= 50*1024*1024*1024
                          OR TABLE_ROWS >= 200000000 THEN 'L3 特大表'
                     WHEN (DATA_LENGTH + INDEX_LENGTH) >= 10*1024*1024*1024
                          OR TABLE_ROWS >= 30000000 THEN 'L2 重点大表'
                     WHEN (DATA_LENGTH + INDEX_LENGTH) >= 1*1024*1024*1024
                          OR TABLE_ROWS >= 3000000 THEN 'L1 一般大表'
                     ELSE '一般表'
                   END AS level
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s
              AND TABLE_TYPE = 'BASE TABLE'
              AND (DATA_LENGTH + INDEX_LENGTH) >= %s
            ORDER BY (DATA_LENGTH + INDEX_LENGTH) DESC
        """, (db, threshold_bytes))'''

new_func = '''    def check_large_tables(self, database: str = None, threshold_gb: float = 1.0) -> list[dict]:
        """检查大表（参考大表治理规范）"""
        db = database or self.config.database
        threshold_bytes = int(threshold_gb * 1024 * 1024 * 1024)

        # Use information_schema.PARTITIONS to get accurate size for partitioned tables
        return self._execute("""
            SELECT t.TABLE_NAME,
                   ROUND((p.total_data + p.total_index)/1024/1024/1024, 2) AS size_gb,
                   t.TABLE_ROWS,
                   ROUND(p.total_data/1024/1024, 2) AS data_mb,
                   ROUND(p.total_index/1024/1024, 2) AS index_mb,
                   CASE
                     WHEN (p.total_data + p.total_index) >= 50*1024*1024*1024
                          OR t.TABLE_ROWS >= 200000000 THEN 'L3 特大表'
                     WHEN (p.total_data + p.total_index) >= 10*1024*1024*1024
                          OR t.TABLE_ROWS >= 30000000 THEN 'L2 重点大表'
                     WHEN (p.total_data + p.total_index) >= 1*1024*1024*1024
                          OR t.TABLE_ROWS >= 3000000 THEN 'L1 一般大表'
                     ELSE '一般表'
                   END AS level
            FROM information_schema.TABLES t
            JOIN (
                SELECT TABLE_SCHEMA, TABLE_NAME,
                       SUM(DATA_LENGTH) AS total_data,
                       SUM(INDEX_LENGTH) AS total_index
                FROM information_schema.PARTITIONS
                WHERE TABLE_SCHEMA = %s
                GROUP BY TABLE_SCHEMA, TABLE_NAME
            ) p ON t.TABLE_SCHEMA = p.TABLE_SCHEMA AND t.TABLE_NAME = p.TABLE_NAME
            WHERE t.TABLE_SCHEMA = %s
              AND t.TABLE_TYPE = 'BASE TABLE'
              AND (p.total_data + p.total_index) >= %s
            ORDER BY (p.total_data + p.total_index) DESC
        """, (db, db, threshold_bytes))'''

content = content.replace(old_func, new_func)

with open('backend/services/tdsql_connector.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Function updated successfully')
