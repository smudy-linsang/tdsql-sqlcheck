"""Debug SQL query for big_audit_trail"""
import pymysql

# Connect directly to the database
conn = pymysql.connect(
    host='119.45.220.89',
    port=15005,
    user='tdsql_check_user',
    password='Abcd1234',
    database='tdsql_check',
    charset='utf8mb4'
)

cursor = conn.cursor(pymysql.cursors.DictCursor)

print("=== Testing the exact SQL query used by check_large_tables ===")
print()

# Test the subquery first
print("1. Testing PARTITIONS subquery:")
cursor.execute("""
    SELECT TABLE_SCHEMA, TABLE_NAME,
           SUM(DATA_LENGTH) AS total_data,
           SUM(INDEX_LENGTH) AS total_index,
           SUM(DATA_LENGTH) + SUM(INDEX_LENGTH) AS total_size
    FROM information_schema.PARTITIONS
    WHERE TABLE_SCHEMA = 'tdsql_check'
      AND TABLE_NAME = 'big_audit_trail'
    GROUP BY TABLE_SCHEMA, TABLE_NAME
""")
result = cursor.fetchall()
for row in result:
    print(f"  TABLE_NAME: {row['TABLE_NAME']}")
    print(f"  total_data: {row['total_data']} bytes ({row['total_data'] / 1024 / 1024 / 1024:.2f} GB)")
    print(f"  total_index: {row['total_index']} bytes ({row['total_index'] / 1024 / 1024 / 1024:.2f} GB)")
    print(f"  total_size: {row['total_size']} bytes ({row['total_size'] / 1024 / 1024 / 1024:.2f} GB)")

print()
print("2. Testing the full query:")
cursor.execute("""
    SELECT
        COALESCE(p.TABLE_NAME, t.TABLE_NAME) AS TABLE_NAME,
        ROUND(COALESCE(p.total_size, COALESCE(t.DATA_LENGTH, 0) + COALESCE(t.INDEX_LENGTH, 0)) / 1024 / 1024 / 1024, 2) AS size_gb,
        COALESCE(t.TABLE_ROWS, 0) AS TABLE_ROWS,
        ROUND(COALESCE(p.total_data, COALESCE(t.DATA_LENGTH, 0)) / 1024 / 1024, 2) AS data_mb,
        ROUND(COALESCE(p.total_index, COALESCE(t.INDEX_LENGTH, 0)) / 1024 / 1024, 2) AS index_mb,
        CASE
            WHEN COALESCE(p.total_size, COALESCE(t.DATA_LENGTH, 0) + COALESCE(t.INDEX_LENGTH, 0)) >= 50*1024*1024*1024
                 OR COALESCE(t.TABLE_ROWS, 0) >= 200000000 THEN 'L3 特大表'
            WHEN COALESCE(p.total_size, COALESCE(t.DATA_LENGTH, 0) + COALESCE(t.INDEX_LENGTH, 0)) >= 10*1024*1024*1024
                 OR COALESCE(t.TABLE_ROWS, 0) >= 30000000 THEN 'L2 重点大表'
            WHEN COALESCE(p.total_size, COALESCE(t.DATA_LENGTH, 0) + COALESCE(t.INDEX_LENGTH, 0)) >= 1*1024*1024*1024
                 OR COALESCE(t.TABLE_ROWS, 0) >= 3000000 THEN 'L1 一般大表'
            ELSE '一般表'
        END AS level
    FROM (
        SELECT TABLE_SCHEMA, TABLE_NAME,
               SUM(DATA_LENGTH) AS total_data,
               SUM(INDEX_LENGTH) AS total_index,
               SUM(DATA_LENGTH) + SUM(INDEX_LENGTH) AS total_size
        FROM information_schema.PARTITIONS
        WHERE TABLE_SCHEMA = 'tdsql_check'
        GROUP BY TABLE_SCHEMA, TABLE_NAME
    ) p
    LEFT JOIN information_schema.TABLES t
        ON p.TABLE_SCHEMA = t.TABLE_SCHEMA AND p.TABLE_NAME = t.TABLE_NAME
    WHERE COALESCE(p.total_size, COALESCE(t.DATA_LENGTH, 0) + COALESCE(t.INDEX_LENGTH, 0)) >= 0
      AND (t.TABLE_TYPE IS NULL OR t.TABLE_TYPE = 'BASE TABLE')
      AND p.TABLE_NAME = 'big_audit_trail'
    ORDER BY COALESCE(p.total_size, COALESCE(t.DATA_LENGTH, 0) + COALESCE(t.INDEX_LENGTH, 0)) DESC
""")
result = cursor.fetchall()
for row in result:
    print(f"  TABLE_NAME: {row['TABLE_NAME']}")
    print(f"  size_gb: {row['size_gb']} GB")
    print(f"  TABLE_ROWS: {row['TABLE_ROWS']}")
    print(f"  data_mb: {row['data_mb']} MB")
    print(f"  index_mb: {row['index_mb']} MB")
    print(f"  level: {row['level']}")

conn.close()
print()
print("Test completed.")
