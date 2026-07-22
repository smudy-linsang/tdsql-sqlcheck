"""创建/重置 4 角色测试用户，密码统一为 Test@1234"""
import sys
sys.path.insert(0, '.')
from backend.services.auth_service import hash_password, auth_service
from backend.services.database import _get_connection, ensure_db
ensure_db()
from backend.services.auth_service import auth_service as svc
conn = _get_connection()
test_users = [
    ("test_admin",     "admin",     "Test@1234"),
    ("test_dba",       "dba",       "Test@1234"),
    ("test_developer", "developer", "Test@1234"),
    ("test_auditor",   "auditor",   "Test@1234"),
]
for username, role, pwd in test_users:
    pw_hash, salt = hash_password(pwd)
    existing = conn.execute("SELECT id FROM users WHERE username=%s", (username,)).fetchone()
    if existing:
        conn.execute("""UPDATE users SET password_hash=%s, salt=%s, role=%s,
            status='active', must_change_password=0, failed_attempts=0,
            locked_until=NULL WHERE username=%s""",
            (pw_hash, salt, role, username))
        print(f'  updated {username} ({role})')
    else:
        conn.execute("""INSERT INTO users(username, role, password_hash, salt,
            status, must_change_password, created_by) VALUES(%s,%s,%s,%s,'active',0,'system')""",
            (username, role, pw_hash, salt))
        print(f'  created {username} ({role})')
    # 检查 role 是否在 roles 表
    role_row = conn.execute("SELECT 1 FROM roles WHERE role_id=%s", (role,)).fetchone()
    if not role_row:
        conn.execute("INSERT IGNORE INTO roles(role_id, role_name, is_builtin) VALUES(%s,%s,1)",
                     (role, role))
conn.commit()
conn.close()
# 验证 4 个用户都能登录
import urllib.request, json
for username, role, pwd in test_users:
    req = urllib.request.Request('http://127.0.0.1:8000/api/v1/auth/login',
        data=json.dumps({'username':username,'password':pwd}).encode(),
        headers={'Content-Type':'application/json'}, method='POST')
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        d = json.loads(resp.read().decode())
        print(f'  login {username}: {resp.status} role={d.get("user",{}).get("role")}')
    except Exception as e:
        print(f'  login {username} FAIL: {e}')
