# -*- coding: utf-8 -*-
"""第三方探测脚本：摸清核心 API 契约"""
import json
import urllib.request

BASE = "http://127.0.0.1:8899"


def req(method, path, body=None, token=None, raw=False):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
            return resp.status, (payload if raw else json.loads(payload))
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(payload)
        except Exception:
            return e.code, payload


# 登录
st, login = req("POST", "/api/v1/auth/login", {"username": "admin", "password": "Admin@1234"})
print("login:", st, json.dumps(login, ensure_ascii=False)[:400])
token = login.get("token", "")

# me
st, me = req("GET", "/api/v1/auth/me", token=token)
print("me:", st, json.dumps(me, ensure_ascii=False)[:400])

# 规则列表
st, rules = req("GET", "/api/v1/rules", token=token)
print("rules:", st, ("count=" + str(len(rules)) if isinstance(rules, list) else json.dumps(rules, ensure_ascii=False)[:300]))

# 即时审核
st, audit = req("POST", "/api/v1/audit/sql", {"sql": "SELECT * FROM users"}, token=token)
print("audit:", st, json.dumps(audit, ensure_ascii=False)[:500])

# dashboard
st, dash = req("GET", "/api/v1/dashboard/summary", token=token)
print("dashboard:", st, json.dumps(dash, ensure_ascii=False)[:300])

# 前端首页
st, html = req("GET", "/", raw=True)
print("frontend:", st, "len=", len(html), html[:80].replace("\n", " "))

# metrics
st, m = req("GET", "/metrics", raw=True)
print("metrics:", st, "len=", len(m))

# 连接列表
st, conns = req("GET", "/api/v1/tdsql/connections", token=token)
print("connections:", st, json.dumps(conns, ensure_ascii=False)[:400])

# 用户列表
st, users = req("GET", "/api/v1/auth/users", token=token)
print("users:", st, json.dumps(users, ensure_ascii=False)[:500])
