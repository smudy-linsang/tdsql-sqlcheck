import pymysql
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
conn = pymysql.connect(host='127.0.0.1', port=13306, user='root', password='tdsql_test_2024', database='tdsql_test', charset='utf8mb4', use_unicode=True)
cur = conn.cursor()
# 查字符集
for var in ['character_set_client', 'character_set_connection', 'character_set_database', 'character_set_results', 'character_set_server', 'character_set_system', 'character_sets_dir']:
    cur.execute(f"SHOW VARIABLES LIKE '{var}'")
    r = cur.fetchone()
    sys.stdout.write(f'{r}\n')
# 直接查 raw bytes
sys.stdout.write('\n--- raw bytes (latin1) ---\n')
cur.execute("SELECT COLUMN_NAME, COLUMN_COMMENT, COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='tdsql_test' AND TABLE_NAME='t_config' AND COLUMN_NAME='config_key'")
for r in cur.fetchall():
    sys.stdout.write(f'repr: {r}\n')
# 测试 set names
cur.execute("SET NAMES utf8mb4")
cur.execute("SELECT COLUMN_NAME, COLUMN_COMMENT FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='tdsql_test' AND TABLE_NAME='t_config' AND COLUMN_NAME='config_key'")
for r in cur.fetchall():
    sys.stdout.write(f'after SET NAMES: {r}\n')
