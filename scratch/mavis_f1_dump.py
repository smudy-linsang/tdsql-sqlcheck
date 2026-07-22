"""看 F1 在线元数据抽取的实际产物 + 检查 visible-menus 401"""
import sys
import json
import urllib.request
sys.path.insert(0, '.')
from backend.services.database import _get_connection, ensure_db

# 1. visible-menus 不带 token 调用
req = urllib.request.Request("http://127.0.0.1:8000/api/v1/auth/visible-menus", method="GET")
try:
    resp = urllib.request.urlopen(req, timeout=10)
    print("visible-menus no auth:", resp.status, resp.read().decode()[:200])
except urllib.error.HTTPError as e:
    print("visible-menus no auth:", e.code, e.read().decode()[:200])

# 2. 找最新一份 F1 报告，把 extracted_sql 拿出来看
ensure_db()
conn = _get_connection()
row = conn.execute("""
    SELECT id, source, results_json FROM audit_history
    WHERE audit_type = 'extracted_schema'
    ORDER BY id DESC LIMIT 1
""").fetchone()
rid = row['id']
results_data = json.loads(row['results_json'] or '[]')
print(f"\n最新 F1 报告 id={rid} source={row['source']}")
print(f"  results 数量: {len(results_data)}")
for i, r in enumerate(results_data[:3]):
    print(f"\n  === Result {i+1} ===")
    print(f"    sql_type: {r.get('sql_type')}")
    print(f"    passed: {r.get('passed')}")
    print(f"    violations ({len(r.get('violations', []))}):")
    for v in r.get('violations', [])[:5]:
        print(f"      [{v.get('rule_id')}] {v.get('severity')}: {v.get('message')[:80]}")
    print(f"    SQL: {r.get('sql', '')[:300]}")

# 3. 查 is_public_path 看 visible-menus
print("\n=== 检查 is_public_path 实现 ===")
from backend.services.auth_service import is_public_path
for p in ["/api/v1/auth/login", "/api/v1/auth/visible-menus",
          "/api/v1/auth/me", "/health", "/"]:
    print(f"  is_public({p}): {is_public_path(p)}")
