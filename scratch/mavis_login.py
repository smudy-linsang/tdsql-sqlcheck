import sys, json, urllib.request, urllib.error
sys.path.insert(0, '.')
# 重置密码
from backend.services.auth_service import auth_service, hash_password
from backend.services.database import _get_connection, ensure_db
ensure_db()
new_pwd = 'Admin@1234'
pw_hash, salt = hash_password(new_pwd)
conn = _get_connection()
conn.execute('UPDATE users SET password_hash=%s, salt=%s, status=%s, must_change_password=0 WHERE username=%s',
             (pw_hash, salt, 'active', 'admin'))
conn.commit()
# 验证一下
user, err = auth_service.authenticate('admin', 'Admin@1234', '127.0.0.1')
print('authenticate:', 'OK' if user else f'FAIL: {err}')
conn.close()

# 直接 HTTP 登录
req = urllib.request.Request('http://127.0.0.1:8000/api/v1/auth/login',
    data=json.dumps({'username':'admin','password':'Admin@1234'}).encode(),
    headers={'Content-Type':'application/json'}, method='POST')
try:
    resp = urllib.request.urlopen(req, timeout=10)
    print('HTTP login:', resp.status, resp.read().decode()[:200])
except urllib.error.HTTPError as e:
    print('HTTP login FAIL:', e.code, e.read().decode()[:200])
