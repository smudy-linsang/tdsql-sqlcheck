"""UAT 辅助：登录 admin 并把 token 写入工作目录的 _uat_token.txt。"""
import urllib.request, json
data = json.dumps({'username':'admin','password':'Uat@2026M!'}).encode()
req = urllib.request.Request('http://127.0.0.1:8003/api/v1/auth/login',
                              data=data, headers={'Content-Type':'application/json'}, method='POST')
r = urllib.request.urlopen(req, timeout=5)
body = json.loads(r.read().decode())
with open('C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/_uat_token.txt', 'w', encoding='utf-8') as f:
    f.write(body['token'])
print('token len:', len(body['token']))
print('user:', body.get('user', {}).get('username'), 'role:', body.get('user', {}).get('role'))
