import sys
sys.path.insert(0, '.')
from backend.services.auth_service import auth_service, hash_password
from backend.services.database import _get_connection, ensure_db
ensure_db()
new_pwd = 'Admin@1234'
pw_hash, salt = hash_password(new_pwd)
conn = _get_connection()
existing = conn.execute('SELECT id FROM users WHERE username=%s', ('admin',)).fetchone()
if existing:
    conn.execute('UPDATE users SET password_hash=%s, salt=%s, status=%s, must_change_password=0 WHERE username=%s',
                 (pw_hash, salt, 'active', 'admin'))
    print('OK: reset admin password to Admin@1234')
else:
    print('admin 不存在')
conn.commit()
conn.close()
