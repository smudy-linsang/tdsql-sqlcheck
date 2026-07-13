"""Execute the 3 diagnostic SQL queries through the system"""
import pymysql

# Connection info (from the API test results)
HOST = '119.45.220.89'
PORT = 15005  # Proxy port
USER = 'tdsql_check_user'
PASSWORD = 'Abcd1234'
DATABASE = 'tdsql_check'

print("=== Connecting to TDSQL ===")
print(f"Host: {HOST}:{PORT}")
print(f"User: {USER}")
print(f"Database: {DATABASE}")
print()

try:
    conn = pymysql.connect(
        host=HOST,
        port=PORT,
        user=USER,
        password=PASSWORD,
        database=DATABASE,
        charset='utf8mb4',
        connect_timeout=10,
        read_timeout=30
    )
    print("✓ Connected successfully")
    print()
    
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    
    # Query 1: Check each partition's raw values
    print("=" * 80)
    print("Query 1: Check each partition's raw values")
    print("=" * 80)
    cursor.execute("""
        SELECT 
            TABLE_NAME,
            PARTITION_NAME,
            DATA_LENGTH,
            INDEX_LENGTH,
            DATA_LENGTH + INDEX_LENGTH AS total_size
        FROM information_schema.PARTITIONS
        WHERE TABLE_SCHEMA = 'tdsql_check'
          AND TABLE_NAME = 'big_audit_trail'
        ORDER BY PARTITION_NAME
    """)
    rows = cursor.fetchall()
    print(f"Rows returned: {len(rows)}")
    print()
    if rows:
        print(f"{'TABLE_NAME':<20} {'PARTITION_NAME':<15} {'DATA_LENGTH':<15} {'INDEX_LENGTH':<15} {'total_size':<15}")
        print("-" * 80)
        for row in rows:
            print(f"{row['TABLE_NAME']:<20} {row['PARTITION_NAME'] or 'NULL':<15} {row['DATA_LENGTH'] or 0:<15} {row['INDEX_LENGTH'] or 0:<15} {row['total_size'] or 0:<15}")
    
    print()
    print()
    
    # Query 2: Check aggregated values
    print("=" * 80)
    print("Query 2: Check aggregated values")
    print("=" * 80)
    cursor.execute("""
        SELECT 
            TABLE_NAME,
            SUM(DATA_LENGTH) AS total_data,
            SUM(INDEX_LENGTH) AS total_index,
            SUM(DATA_LENGTH) + SUM(INDEX_LENGTH) AS total_size,
            ROUND((SUM(DATA_LENGTH) + SUM(INDEX_LENGTH)) / 1024 / 1024 / 1024, 2) AS size_gb
        FROM information_schema.PARTITIONS
        WHERE TABLE_SCHEMA = 'tdsql_check'
          AND TABLE_NAME = 'big_audit_trail'
        GROUP BY TABLE_NAME
    """)
    rows = cursor.fetchall()
    print(f"Rows returned: {len(rows)}")
    print()
    if rows:
        for row in rows:
            print(f"TABLE_NAME: {row['TABLE_NAME']}")
            print(f"total_data: {row['total_data']} bytes ({row['total_data'] / 1024 / 1024 / 1024:.2f} GB)")
            print(f"total_index: {row['total_index']} bytes ({row['total_index'] / 1024 / 1024 / 1024:.2f} GB)")
            print(f"total_size: {row['total_size']} bytes ({row['total_size'] / 1024 / 1024 / 1024:.2f} GB)")
            print(f"size_gb: {row['size_gb']} GB")
    
    print()
    print()
    
    # Query 3: Check information_schema.TABLES
    print("=" * 80)
    print("Query 3: Check information_schema.TABLES")
    print("=" * 80)
    cursor.execute("""
        SELECT 
            TABLE_NAME,
            DATA_LENGTH,
            INDEX_LENGTH,
            TABLE_ROWS,
            TABLE_TYPE
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = 'tdsql_check'
          AND TABLE_NAME = 'big_audit_trail'
    """)
    rows = cursor.fetchall()
    print(f"Rows returned: {len(rows)}")
    print()
    if rows:
        for row in rows:
            print(f"TABLE_NAME: {row['TABLE_NAME']}")
            print(f"DATA_LENGTH: {row['DATA_LENGTH']} bytes ({row['DATA_LENGTH'] / 1024 / 1024:.2f} MB)")
            print(f"INDEX_LENGTH: {row['INDEX_LENGTH']} bytes ({row['INDEX_LENGTH'] / 1024 / 1024:.2f} MB)")
            print(f"TABLE_ROWS: {row['TABLE_ROWS']}")
            print(f"TABLE_TYPE: {row['TABLE_TYPE']}")
    else:
        print("big_audit_trail NOT found in information_schema.TABLES")
    
    conn.close()
    print()
    print("✓ Connection closed")
    
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()

print()
print("Test completed.")
