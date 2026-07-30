import pymysql
conn = pymysql.connect(host='119.45.220.89', port=15005, user='tdsql_check_user',
                      password='Abcd1234', connect_timeout=10, charset='utf8mb4', database='tdsql_check')
with conn.cursor() as cur:
    cur.execute("""
        SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA='tdsql_check' AND TABLE_NAME='big_audit_trail'
        ORDER BY ORDINAL_POSITION
    """)
    print("big_audit_trail 字段:")
    for c in cur.fetchall():
        print(" ", c)
    cur.execute("SHOW CREATE TABLE big_audit_trail")
    print("\n完整 DDL:")
    print(cur.fetchone()[1])
