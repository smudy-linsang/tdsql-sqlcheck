import json
with open(r'C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\scratch\mavis_api_probe.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
# 输出每个 RBAC 结果
print("=== RBAC 结果 ===")
for r in data['results']:
    if r['tag'].startswith('RBAC:'):
        print(f"  [{r['result']:4s}] {r['status']} {r['tag']}")

print()
print("=== 失败明细 (再次) ===")
for r in data['results']:
    if r['result'] == 'FAIL':
        print(f"  [{r['result']:4s}] {r['method']:6s} {r['path']:50s} status={r['status']} {r['body_first'][:200]}")
