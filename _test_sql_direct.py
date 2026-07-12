"""Direct SQL test for big tables"""
import pymysql

# Connect to TDSQL
conn = pymysql.connect(
    host='119.45.220.89',
    port=15005,
    user='root',
    password='Abcd1234',
    database='tdsql_check',
    charset='utf8mb4'
)

cursor = conn.cursor(pymysql.cursors.DictCursor)

# Test the new query
print("=== Testing new query with information_schema.PARTITIONS ===")
cursor.execute("""
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
        WHERE TABLE_SCHEMA = 'tdsql_check'
        GROUP BY TABLE_SCHEMA, TABLE_NAME
    ) p ON t.TABLE_SCHEMA = p.TABLE_SCHEMA AND t.TABLE_NAME = p.TABLE_NAME
    WHERE t.TABLE_SCHEMA = 'tdsql_check'
      AND t.TABLE_TYPE = 'BASE TABLE'
      AND (p.total_data + p.total_index) >= 1*1024*1024*1024
    ORDER BY (p.total_data + p.total_index) DESC
""")
tables = cursor.fetchall()
print(f'Tables found with >= 1GB: {len(tables)}')
for t in tables:
    print(f'  {t["TABLE_NAME"]}: {t["size_gb"]}GB, {t["TABLE_ROWS"]} rows, level={t["level"]}')

# Test with lower threshold
print("\n=== Testing with 0.1GB threshold ===")
cursor.execute("""
    SELECT t.TABLE_NAME,
           ROUND((p.total_data + p.total_index)/1024/1024/1024, 2) AS size_gb,
           t.TABLE_ROWS
    FROM information_schema.TABLES t
    JOIN (
        SELECT TABLE_SCHEMA, TABLE_NAME,
               SUM(DATA_LENGTH) AS total_data,
               SUM(INDEX_LENGTH) AS total_index
        FROM information_schema.PARTITIONS
        WHERE TABLE_SCHEMA = 'tdsql_check'
        GROUP BY TABLE_SCHEMA, TABLE_NAME
    ) p ON t.TABLE_SCHEMA = p.TABLE_SCHEMA AND t.TABLE_NAME = p.TABLE_NAME
    WHERE t.TABLE_SCHEMA = 'tdsql_check'
      AND t.TABLE_TYPE = 'BASE TABLE'
      AND (p.total_data + p.total_index) >= 0.1*1024*1024*1024
    ORDER BY (p.total_data + p.total_index) DESC
""")
tables = cursor.fetchall()
print(f'Tables found with >= 0.1GB: {len(tables)}')
for t in tables:
    print(f'  {t["TABLE_NAME"]}: {t["size_gb"]}GB, {t["TABLE_ROWS"]} rows')

# Check specifically for big_order_log and big_audit_trail
print("\n=== Checking for big_order_log and big_audit_trail ===")
cursor.execute("""
    SELECT TABLE_NAME,
           SUM(DATA_LENGTH)/1024/1024 AS data_mb,
           SUM(INDEX_LENGTH)/1024/1024 AS index_mb,
           (SUM(DATA_LENGTH) + SUM(INDEX_LENGTH))/1024/1024/1024 AS size_gb
    FROM information_schema.PARTITIONS
    WHERE TABLE_SCHEMA = 'tdsql_check'
      AND TABLE_NAME IN ('big_order_log', 'big_audit_trail')
    GROUP BY TABLE_NAME
""")
tables = cursor.fetchall()
print(f'Tables found: {len(tables)}')
for t in tables:
    print(f'  {t["TABLE_NAME"]}: data={t["data_mb"]}MB, index={t["index_mb"]}MB, total={t["size_gb"]}GB')

conn.close()
