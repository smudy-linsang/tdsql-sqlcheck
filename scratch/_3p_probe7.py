# -*- coding: utf-8 -*-
"""深挖 dashboard 统计口径 + 分页 total 准确性"""
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
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}


_, login = req("POST", "/api/v1/auth/login", {"username": "admin", "password": "Admin@1234"})
tok = login["token"]

# dashboard 完整数据
st, dash = req("GET", "/api/v1/dashboard/summary", None, tok)
print(json.dumps(dash.get("audit", {}), ensure_ascii=False, indent=1))
print("slow:", json.dumps(dash.get("slow_queries", {}), ensure_ascii=False)[:300])

# 审核历史分页 total 与实际条目核对
st, r1 = req("GET", "/api/v1/audit/extracted-reports?limit=2&offset=0", None, tok)
total = r1.get("total")
items = r1.get("reports", [])
print(f"extracted-reports: total={total}, page_items={len(items)}")

# 慢SQL 分页核对
st, sq = req("GET", "/api/v1/slow-queries?page=1&page_size=5", None, tok)
print("slow-queries keys:", list(sq.keys())[:10])
print("slow-queries total:", sq.get("total"), "items:", len(sq.get("items", sq.get("slow_queries", []))))

# 大页容量探测：page_size 是否有上限保护
st, big = req("GET", "/api/v1/slow-queries?page=1&page_size=100000", None, tok)
print("page_size=100000:", st, "items:", len(big.get("items", big.get("slow_queries", []))))

# 用户列表是否分页
st, users = req("GET", "/api/v1/auth/users", None, tok)
print("users count:", len(users.get("users", [])), "(no pagination field)" if "total" not in users else f"total={users['total']}")
