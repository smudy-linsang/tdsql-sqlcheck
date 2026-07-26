"""重新验证 D1 修复"""
import urllib.request, json

def req(method, path, token=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(f"http://127.0.0.1:8000{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]

code, d = req("POST", "/api/v1/auth/login", body={"username":"admin","password":"Admin@1234"})
tok = d["token"]

# 1. 看 extract-and-audit 后的 audit_history 真实情况
print("=" * 60)
print("D1 修复重新验证")
print("=" * 60)

# 用 adhoc 跑一次 extract-and-audit
code, d = req("POST", "/api/v1/audit/connect", token=tok, body={
    "host": "127.0.0.1", "port": 13306, "username": "root",
    "password": "tdsql_test_2024", "database": "tdsql_test"
})
print(f"重新连接: {code}")

code, d = req("POST", "/api/v1/audit/extract-and-audit", token=tok, body={
    "connection_id": "adhoc",
    "database": "tdsql_test",
    "scopes": ["TABLE"]
})
print(f"extract-and-audit: {code} report_id={d.get('report_id')} snapshot_id={d.get('snapshot_id')}")

# 查列表
code, d = req("GET", "/api/v1/audit/extracted-reports?limit=5", token=tok)
if isinstance(d, dict):
    items = d.get("items", [])
    print(f"\n列表 total={d.get('total')} items={len(items)}")
    for it in items:
        print(f"  id={it.get('id')} conn={it.get('connection_id')!r} db={it.get('db_name')!r} name={it.get('connection_name')!r}")

# 按 connection_id=adhoc 筛选
code, d = req("GET", "/api/v1/audit/extracted-reports?connection_id=adhoc&limit=10", token=tok)
if isinstance(d, dict):
    items = d.get("items", [])
    print(f"\nadhoc 筛选 total={d.get('total')} items={len(items)}")
    for it in items[:5]:
        print(f"  id={it.get('id')} conn={it.get('connection_id')!r} db={it.get('db_name')!r}")
    # 关键判断: 全部 connection_id='adhoc'
    all_adhoc = all(it.get("connection_id") == "adhoc" for it in items)
    print(f"  全部 connection_id=adhoc: {all_adhoc}")

# 验证 db_name 字段
code, d = req("GET", "/api/v1/audit/extracted-reports?connection_id=adhoc&limit=10", token=tok)
if isinstance(d, dict):
    items = d.get("items", [])
    has_db_name = all("db_name" in it for it in items)
    has_conn_id = all("connection_id" in it for it in items)
    has_name = all("connection_name" in it for it in items)
    print(f"\n字段完整性:")
    print(f"  都有 connection_id: {has_conn_id}")
    print(f"  都有 db_name: {has_db_name}")
    print(f"  都有 connection_name: {has_name}")
    if items:
        print(f"  第一条 db_name={items[0].get('db_name')!r} connection_name={items[0].get('connection_name')!r}")
