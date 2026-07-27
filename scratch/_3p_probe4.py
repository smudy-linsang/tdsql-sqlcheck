# -*- coding: utf-8 -*-
"""深挖：连接创建语义 / 扫描端点路径 / 创建响应体"""
import json
import urllib.request
import urllib.error
import uuid

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
            return e.code, {"raw": e.read().decode("utf-8", "replace")[:200]}


_, login = req("POST", "/api/v1/auth/login", {"username": "admin", "password": "Admin@1234"})
tok = login["token"]

# 1. 创建连接（看完整响应体）
cid = "t3p_" + uuid.uuid4().hex[:8]
st, r = req("POST", "/api/v1/tdsql/connections", {
    "id": cid, "name": "探测实例", "host": "127.0.0.1", "port": 13306,
    "username": "root", "password": "tdsql_test_2024", "database": "tdsql_sqlcheck",
    "is_distributed": False}, tok)
print("create conn:", st, json.dumps(r, ensure_ascii=False)[:500])

# 2. 立即查列表
st, lst = req("GET", "/api/v1/tdsql/connections", None, tok)
ids = [c["id"] for c in lst.get("connections", [])]
print("list ids:", ids)
print("created in list:", cid in ids)

# 3. 详情接口
st, detail = req("GET", f"/api/v1/tdsql/connections/{cid}", None, tok)
print("detail:", st, json.dumps(detail, ensure_ascii=False)[:400])

# 4. 清理
st, r = req("DELETE", f"/api/v1/tdsql/connections/{cid}", None, tok)
print("delete:", st, json.dumps(r, ensure_ascii=False)[:200])

# 5. 探测扫描端点可能路径
for path in ("/api/v1/tdsql/scan", "/api/v1/tdsql/scan-slow", "/api/v1/slow-queries/scan",
             "/api/v1/tdsql/slow-queries/scan", "/api/v1/slow-queries/scan-tasks"):
    st, r = req("POST", path, {"connection_id": "x", "source": "digest"}, tok)
    print(f"POST {path}:", st, json.dumps(r, ensure_ascii=False)[:150])

# 6. openapi 找扫描相关路径
st, spec = req("GET", "/openapi.json")
scan_paths = [p for p in spec.get("paths", {}) if "scan" in p.lower()]
print("scan paths in openapi:", scan_paths)
