# -*- coding: utf-8 -*-
"""探测 change-password / 登录锁定 / 审核历史契约"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8899"


def req(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}


_, login = req("POST", "/api/v1/auth/login", {"username": "admin", "password": "Admin@1234"})
admin_tok = login["token"]

# 建一个新用户看首登改密全流程
req("POST", "/api/v1/auth/users", {"username": "t3p_flow", "password": "Init#Pass123", "role": "auditor"}, admin_tok)
_, lg = req("POST", "/api/v1/auth/login", {"username": "t3p_flow", "password": "Init#Pass123"})
tok = lg["token"]
print("must_change:", lg["user"].get("must_change_password"))

# 未改密前访问业务接口
st, r = req("GET", "/api/v1/rules", token=tok)
print("before change rules:", st, json.dumps(r, ensure_ascii=False)[:200])

# 改密（探测字段名）
st, r = req("POST", "/api/v1/auth/change-password", {"old_password": "Init#Pass123", "new_password": "Changed#Pass456"}, tok)
print("change pwd:", st, json.dumps(r, ensure_ascii=False)[:250])

# 旧 token 改密后是否仍有效？
st, r = req("GET", "/api/v1/rules", token=tok)
print("old token after change:", st, "(rules ok)" if st == 200 else json.dumps(r, ensure_ascii=False)[:200])

# 新口令登录
st, lg2 = req("POST", "/api/v1/auth/login", {"username": "t3p_flow", "password": "Changed#Pass456"})
print("relogin:", st, lg2.get("user", {}).get("must_change_password"))

# 错误口令锁定测试（独立用户）
req("POST", "/api/v1/auth/users", {"username": "t3p_lock", "password": "Lock#Pass123", "role": "auditor"}, admin_tok)
req("POST", "/api/v1/auth/users/t3p_lock/unlock", None, admin_tok)
for i in range(5):
    st, r = req("POST", "/api/v1/auth/login", {"username": "t3p_lock", "password": "wrong"})
    print(f"  wrong attempt {i+1}:", st, json.dumps(r, ensure_ascii=False)[:150])
st, r = req("POST", "/api/v1/auth/login", {"username": "t3p_lock", "password": "Lock#Pass123"})
print("after 5 wrong, correct pwd:", st, json.dumps(r, ensure_ascii=False)[:200])

# 审核历史列表契约
st, r = req("GET", "/api/v1/audit/extracted-reports?limit=3", None, admin_tok)
print("extracted-reports:", st, json.dumps(r, ensure_ascii=False)[:400])

# 清理
req("DELETE", "/api/v1/auth/users/t3p_flow", None, admin_tok)
req("DELETE", "/api/v1/auth/users/t3p_lock", None, admin_tok)
print("cleaned")
