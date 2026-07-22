import pymysql
import sys
for ch in ['utf8mb4', 'utf8', 'latin1']:
    try:
        conn = pymysql.connect(host='127.0.0.1', port=13306, user='root', password='tdsql_test_2024', database='tdsql_test', charset=ch)
        cur = conn.cursor()
        cur.execute('SHOW CREATE TABLE t_config')
        row = cur.fetchone()
        sql = row[1]
        # 找第一个 COMMENT 字符串
        idx = sql.find("COMMENT '")
        if idx > 0:
            sample = sql[idx:idx+50]
            sys.stdout.write(f'charset={ch}: {sample!r}\n')
        else:
            sys.stdout.write(f'charset={ch}: NO COMMENT FOUND\n')
        conn.close()
    except Exception as e:
        sys.stdout.write(f'charset={ch}: ERR {e}\n')
sys.stdout.flush()
