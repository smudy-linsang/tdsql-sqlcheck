"""Check raw values from information_schema.PARTITIONS for big_audit_trail"""
import pymysql

# This test needs to be run from a machine that can connect to TDSQL
# Since we can't connect from here, let's create a SQL query that the user can run

sql_query = """
-- Check raw values for big_audit_trail
SELECT 
    TABLE_NAME,
    PARTITION_NAME,
    DATA_LENGTH,
    INDEX_LENGTH,
    DATA_LENGTH + INDEX_LENGTH AS total_size
FROM information_schema.PARTITIONS
WHERE TABLE_SCHEMA = 'tdsql_check'
  AND TABLE_NAME = 'big_audit_trail'
ORDER BY PARTITION_NAME;

-- Check aggregated values
SELECT 
    TABLE_NAME,
    SUM(DATA_LENGTH) AS total_data,
    SUM(INDEX_LENGTH) AS total_index,
    SUM(DATA_LENGTH) + SUM(INDEX_LENGTH) AS total_size,
    ROUND((SUM(DATA_LENGTH) + SUM(INDEX_LENGTH)) / 1024 / 1024 / 1024, 2) AS size_gb
FROM information_schema.PARTITIONS
WHERE TABLE_SCHEMA = 'tdsql_check'
  AND TABLE_NAME = 'big_audit_trail'
GROUP BY TABLE_NAME;

-- Check if the table exists in information_schema.TABLES
SELECT 
    TABLE_NAME,
    DATA_LENGTH,
    INDEX_LENGTH,
    TABLE_ROWS,
    TABLE_TYPE
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'tdsql_check'
  AND TABLE_NAME = 'big_audit_trail';
"""

print("Please run the following SQL queries on your TDSQL instance:")
print()
print(sql_query)
print()
print("This will help us understand why big_audit_trail is showing 0.0GB")
