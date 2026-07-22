"""检查 F1 抽取的 .sql 实际内容质量"""
import sys
import json
import urllib.request
sys.path.insert(0, '.')
from backend.services.database import _get_connection, ensure_db
ensure_db()
conn = _get_connection()
row = conn.execute("""
    SELECT id, source, results_json FROM audit_history
    WHERE audit_type = 'extracted_schema'
    ORDER BY id DESC LIMIT 1
""").fetchone()
print(f"报告 id={row['id']} source={row['source']}")
# 调用 /audit/report/{id}/sql 拿 .sql
req = urllib.request.Request(f"http://127.0.0.1:8000/api/v1/audit/report/{row['id']}/sql",
    headers={"Authorization": "Bearer eyJzdWIiOiJhZG1pbiIsInJvbGUiOiJhZG1pbiJ9"})
try:
    import requests
except ImportError:
    pass

# 用 urllib
import urllib.error
try:
    resp = urllib.request.urlopen(req, timeout=10)
    sql_content = resp.read().decode('utf-8', errors='replace')
    print(f"\n=== /audit/report/{row['id']}/sql 实际下载内容 ===")
    print(f"总字节: {len(sql_content)}")
    print(f"前 3000 字符:")
    print(sql_content[:3000])
    if len(sql_content) > 3000:
        print(f"\n... 后 500 字符:")
        print(sql_content[-500:])
except urllib.error.HTTPError as e:
    print(f"FAIL: {e.code} {e.read().decode()[:300]}")

# 查 results_json 里的 sql 字段
print("\n\n=== results_json 里 results[*].sql 字段样例 ===")
results = json.loads(row['results_json'] or '[]')
for i, r in enumerate(results[:3]):
    print(f"\n--- result {i+1}: {r.get('sql_type')} (passed={r.get('passed')}) ---")
    print(r.get('sql', '')[:500])
conn.close()
