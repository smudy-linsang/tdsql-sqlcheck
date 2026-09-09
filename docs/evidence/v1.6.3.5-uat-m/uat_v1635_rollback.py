"""UAT v1.6.3.5 收尾：回滚运维改动 + 清 token。"""
import sys, os
sys.path.insert(0, 'C:/TDSQL_SQLCHECK/TDSQL-SQLCheck')
import pymysql

# 删 token
for p in [
    'docs/evidence/v1.6.3.5-uat-m/uat_v1635_token.txt',
]:
    if os.path.exists(p):
        os.remove(p); print('removed', p)

# admin 锁定 + auth_enabled=true
cfg = {'host':'127.0.0.1','port':13306,'user':'root','password':'tdsql_test_2024','database':'tdsql_sqlcheck','charset':'utf8mb4'}
conn = pymysql.connect(**cfg)
c = conn.cursor()
c.execute("UPDATE system_config SET config_value='true' WHERE config_key='auth_enabled'")
conn.commit()
c.execute("UPDATE users SET failed_attempts=5, locked_until='2030-01-01 00:00:00', must_change_password=0 WHERE username='admin'")
conn.commit()
c.execute("SELECT config_value FROM system_config WHERE config_key='auth_enabled'")
print('auth_enabled:', c.fetchall())
c.execute("SELECT username, status, failed_attempts, locked_until FROM users WHERE username='admin'")
print('admin:', c.fetchall())
conn.close()
print('rollback done')
