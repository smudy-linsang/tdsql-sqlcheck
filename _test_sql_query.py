"""Test the exact SQL query used by check_large_tables"""
import pymysql

# Connect to TDSQL
conn = pymysql.connect(
    host='119.45.220.89',
    port=15005,
    user='root',
    password='Abcd1234',
    database='tdsql_check',
    charset='utf8mb4',
    connect_timeout=10,
    read_timeout=30
)

cursor = conn.cursor(pymysql.cursors.DictCursor)

# Test the exact query from check_large_tables
print("=== Testing check_large_tables SQL query ===")
print("Threshold: 1GB")
print()

threshold_bytes = int(1.0 * 1024 * 1024 * 1024)

sql = """
SELECT
    t.TABLE_NAME,
    ROUND(GREATEST(
        COALESCE(t.DATA_LENGTH, 0) + COALESCE(t.INDEX_LENGTH, 0),
        COALESCE(p.total_data, 0) + COALESCE(p.total_index, 0)
    ) / 1024 / 1024 / 1024, 2) AS size_gb,
    t.TABLE_ROWS,
    ROUND(GREATEST(COALESCE(t.DATA_LENGTH, 0), COALESCE(p.total_data, 0)) / 1024 / 1024, 2) AS data_mb,
    ROUND(GREATEST(COALESCE(t.INDEX_LENGTH, 0), COALESCE(p.total_index, 0)) / 1024 / 1024, 2) AS index_mb,
    CASE
        WHEN GREATEST(
            COALESCE(t.DATA_LENGTH, 0) + COALESCE(t.INDEX_LENGTH, 0),
            COALESCE(p.total_data, 0) + COALESCE(p.total_index, 0)
        ) >= 50*1024*1024*1024
        OR t.TABLE_ROWS >= 200000000 THEN 'L3 特大表'
        WHEN GREATEST(
            COALESCE(t.DATA_LENGTH, 0) + COALESCE(t.INDEX_LENGTH, 0),
            COALESCE(p.total_data, 0) + COALESCE(p.total_index, 0)
        ) >= 10*1024*1024*1024
        OR t.TABLE_ROWS >= 30000000 THEN 'L2 重点大表'
        WHEN GREATEST(
            COALESCE(t.DATA_LENGTH, 0) + COALESCE(t.INDEX_LENGTH, 0),
            COALESCE(p.total_data, 0) + COALESCE(p.total_index, 0)
        ) >= 1*1024*1024*1024
        OR t.TABLE_ROWS >= 3000000 THEN 'L1 一般大表'
        ELSE '一般表'
    END AS level
FROM information_schema.TABLES t
LEFT JOIN (
    SELECT TABLE_SCHEMA, TABLE_NAME,
           SUM(DATA_LENGTH) AS total_data,
           SUM(INDEX_LENGTH) AS total_index
    FROM information_schema.PARTITIONS
    WHERE TABLE_SCHEMA = %s
    GROUP BY TABLE_SCHEMA, TABLE_NAME
) p ON t.TABLE_SCHEMA = p.TABLE_SCHEMA AND t.TABLE_NAME = p.TABLE_NAME
WHERE t.TABLE_SCHEMA = %s
  AND t.TABLE_TYPE = 'BASE TABLE'
  AND GREATEST(
      COALESCE(t.DATA_LENGTH, 0) + COALESCE(t.INDEX_LENGTH, 0),
      COALESCE(p.total_data, 0) + COALESCE(p.total_index, 0)
  ) >= %s
ORDER BY GREATEST(
    COALESCE(t.DATA_LENGTH, 0) + COALESCE(t.INDEX_LENGTH, 0),
    COALESCE(p.total_data, 0) + COALESCE(p.total_index, 0)
) DESC
"""

cursor.execute(sql, ('tdsql_check', 'tdsql_check', threshold_bytes))
tables = cursor.fetchall()

print(f"Tables found (>= 1GB): {len(tables)}")
for t in tables:
    print(f"  {t['TABLE_NAME']}: {t['size_gb']}GB, {t['TABLE_ROWS']} rows, level={t['level']}")

# Now test with lower threshold
print()
print("=== Testing with 0.01GB threshold ===")
threshold_bytes_low = int(0.01 * 1024 * 1024 * 1024)
cursor.execute(sql, ('tdsql_check', 'tdsql_check', threshold_bytes_low))
tables_low = cursor.fetchall()
print(f"Tables found (>= 0.01GB): {len(tables_low)}")
for t in tables_low[:10]:
    print(f"  {t['TABLE_NAME']}: {t['size_gb']}GB, {t['TABLE_ROWS']} rows, level={t['level']}")

# Check specifically for big_audit_trail and big_order_log
print()
print("=== Checking for big_audit_trail and big_order_log ===")
cursor.execute("""
    SELECT TABLE_NAME,
           COALESCE(DATA_LENGTH, 0) AS data_length,
           COALESCE(INDEX_LENGTH, 0) AS index_length,
           ROUND((COALESCE(DATA_LENGTH, 0) + COALESCE(INDEX_LENGTH, 0)) / 1024 / 1024 / 1024, 2) AS size_gb
    FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = 'tdsql_check'
      AND TABLE_NAME IN ('big_audit_trail', 'big_order_log')
""")
tables_check = cursor.fetchall()
print(f"Tables found in information_schema.TABLES: {len(tables_check)}")
for t in tables_check:
    print(f"  {t['TABLE_NAME']}: data={t['data_length']} bytes, index={t['index_length']} bytes, total={t['size_gb']}GB")

conn.close()
print()
print("Test completed.")
