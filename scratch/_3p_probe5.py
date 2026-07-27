# -*- coding: utf-8 -*-
"""探测 scan-tasks POST 契约 + 清理残留连接"""
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
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {"raw": e.read().decode("utf-8", "replace")[:200]}


_, login = req("POST", "/api/v1/auth/login", {"username": "admin", "password": "Admin@1234"})
tok = login["token"]

# 清理 probe4 创建的连接
st, r = req("DELETE", "/api/v1/tdsql/connections/b0231f3b", None, tok)
print("cleanup b0231f3b:", st)

# openapi 看 scan-tasks POST 的入参 schema
st, spec = req("GET", "/openapi.json")
op = spec["paths"].get("/api/v1/slow-queries/scan-tasks", {})
for method, meta in op.items():
    print(f"--- {method.upper()} /api/v1/slow-queries/scan-tasks")
    rb = meta.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
    ref = rb.get("$ref", "")
    if ref:
        model = ref.split("/")[-1]
        schema = spec["components"]["schemas"].get(model, {})
        print("  body model:", model, json.dumps(schema.get("properties", {}), ensure_ascii=False)[:800])
        print("  required:", schema.get("required"))
    params = meta.get("parameters", [])
    for p in params:
        print("  param:", p.get("name"), p.get("required"))

# 也看 GET scan-tasks 的参数
get_op = spec["paths"].get("/api/v1/slow-queries/scan-tasks", {}).get("get", {})
print("GET params:", json.dumps([(p.get("name"), p.get("required")) for p in get_op.get("parameters", [])], ensure_ascii=False))
