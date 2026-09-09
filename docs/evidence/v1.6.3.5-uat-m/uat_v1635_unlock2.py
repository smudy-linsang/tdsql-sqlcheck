"""清 must_change_password。"""
import sys
sys.path.insert(0, 'C:/TDSQL_SQLCHECK/TDSQL-SQLCheck')
import pymysql
cfg = {'host':'127.0.0.1','port':13306,'user':'root','password':'tdsql_test_2024','database':'tdsql_sqlcheck','charset':'utf8mb4'}
conn = pymysql.connect(**cfg)
c = conn.cursor()
c.execute("UPDATE users SET must_change_password=0 WHERE username='admin'")
conn.commit()
c.execute("UPDATE users SET token_version = token_version + 1 WHERE username='admin'")
conn.commit()
c.execute("SELECT username, must_change_password, failed_attempts FROM users WHERE username='admin'")
print('admin:', c.fetchall())
conn.close()
