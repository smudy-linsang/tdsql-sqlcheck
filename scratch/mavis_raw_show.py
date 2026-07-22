import pymysql
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
conn = pymysql.connect(host='127.0.0.1', port=13306, user='root', password='tdsql_test_2024', database='tdsql_test', charset='utf8mb4')
cur = conn.cursor()
cur.execute('SHOW CREATE TABLE t_config')
for r in cur.fetchall():
    print(r[1])
