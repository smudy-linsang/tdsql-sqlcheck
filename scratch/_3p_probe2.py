# -*- coding: utf-8 -*-
"""探测用户管理 / 审计日志 / 连接管理 API 契约"""
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
tok = login["token"]

# 创建用户
st, r = req("POST", "/api/v1/auth/users", {"username": "t3p_probe", "password": "T3p#Passw0rd2026", "role": "developer", "display_name": "probe"}, tok)
print("create user:", st, json.dumps(r, ensure_ascii=False)[:300])

# 重复创建
st, r = req("POST", "/api/v1/auth/users", {"username": "t3p_probe", "password": "T3p#Passw0rd2026", "role": "developer"}, tok)
print("create dup:", st, json.dumps(r, ensure_ascii=False)[:300])

# 重置口令
st, r = req("POST", "/api/v1/auth/users/t3p_probe/reset-password", {"password": "T3p#Passw0rd2026"}, tok)
print("reset pwd:", st, json.dumps(r, ensure_ascii=False)[:200])

# 解锁
st, r = req("POST", "/api/v1/auth/users/t3p_probe/unlock", None, tok)
print("unlock:", st, json.dumps(r, ensure_ascii=False)[:200])

# 新用户登录
st, r = req("POST", "/api/v1/auth/login", {"username": "t3p_probe", "password": "T3p#Passw0rd2026"})
print("probe login:", st, json.dumps(r, ensure_ascii=False)[:250])

# 操作审计日志
st, r = req("GET", "/api/v1/admin/operation-logs?limit=5", None, tok)
print("op logs:", st, json.dumps(r, ensure_ascii=False)[:500])

# 删除探测用户
st, r = req("DELETE", "/api/v1/auth/users/t3p_probe", None, tok)
print("delete user:", st, json.dumps(r, ensure_ascii=False)[:200])

# 弱口令创建
st, r = req("POST", "/api/v1/auth/users", {"username": "t3p_weak", "password": "123456", "role": "developer"}, tok)
print("weak pwd:", st, json.dumps(r, ensure_ascii=False)[:300])

# 非法角色
st, r = req("POST", "/api/v1/auth/users", {"username": "t3p_bad", "password": "T3p#Passw0rd2026", "role": "superroot"}, tok)
print("bad role:", st, json.dumps(r, ensure_ascii=False)[:300])
