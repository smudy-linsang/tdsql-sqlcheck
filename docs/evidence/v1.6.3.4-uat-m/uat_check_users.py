"""UAT 辅助：解锁 admin 并重置密码。"""
import sys
sys.path.insert(0, 'C:/TDSQL_SQLCHECK/TDSQL-SQLCheck')
import pymysql
cfg = {'host':'127.0.0.1','port':13306,'user':'root','password':'tdsql_test_2024','database':'tdsql_sqlcheck','charset':'utf8mb4'}
conn = pymysql.connect(**cfg)
c = conn.cursor()
c.execute("UPDATE users SET failed_attempts=0, locked_until=NULL, status='active' WHERE username='admin'")
conn.commit()
from backend.services.auth_service import auth_service
err = auth_service.reset_password('admin', 'Uat@2026M!', operator='UAT')
print('reset:', err)
c.execute("UPDATE users SET token_version = token_version + 1 WHERE username='admin'")
conn.commit()
conn.close()
print('done')
