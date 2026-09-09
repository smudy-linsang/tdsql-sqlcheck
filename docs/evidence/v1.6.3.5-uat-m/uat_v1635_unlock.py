"""UAT v1.6.3.5: 解锁 admin + 重置密码 + 清 must_change_password。"""
import sys
sys.path.insert(0, 'C:/TDSQL_SQLCHECK/TDSQL-SQLCheck')
import pymysql
cfg = {'host':'127.0.0.1','port':13306,'user':'root','password':'tdsql_test_2024','database':'tdsql_sqlcheck','charset':'utf8mb4'}
conn = pymysql.connect(**cfg)
c = conn.cursor()
c.execute("UPDATE users SET failed_attempts=0, locked_until=NULL, status='active', must_change_password=1 WHERE username='admin'")
conn.commit()
from backend.services.auth_service import auth_service
err = auth_service.reset_password('admin', 'UatV1635@M!', operator='UAT-V1635')
print('reset:', err)
c.execute("UPDATE users SET token_version = token_version + 1 WHERE username='admin'")
conn.commit()
c.execute("SELECT username, must_change_password, failed_attempts FROM users WHERE username='admin'")
print('admin after reset:', c.fetchall())
conn.close()
