import sys
sys.path.insert(0, '.')
from backend.services.database import _get_connection, ensure_db
ensure_db()
conn = _get_connection()
# 全部用户解锁 + 清理 failed_attempts + 重置密码
from backend.services.auth_service import hash_password
pw_hash, salt = hash_password('Admin@1234')
conn.execute("""UPDATE users SET
    password_hash=%s, salt=%s, status='active',
    must_change_password=0, failed_attempts=0, locked_until=NULL
    WHERE username='admin'""", (pw_hash, salt))
conn.execute("UPDATE users SET failed_attempts=0, locked_until=NULL, status='active'")
conn.commit()
# 验证
row = conn.execute("SELECT username, role, status, failed_attempts, locked_until FROM users WHERE username IN ('admin','dba01','dev01','audit01')").fetchall()
for r in row:
    print(dict(r) if hasattr(r, 'keys') else r)
conn.close()
