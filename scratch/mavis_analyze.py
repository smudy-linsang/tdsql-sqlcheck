import json
with open('scratch/mavis_api_probe.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
print('=== COUNTERS ===')
print(json.dumps(data['counters'], indent=2, ensure_ascii=False))
print()
print('=== FAILURES (status != expected) ===')
for f in data['failures']:
    print(f"  {f['method']:6s} {f['path']:60s} status={f['status']:4d} {f['body_first'][:200]}")
print()
print('=== ALL RESULTS (sorted by status) ===')
for r in sorted(data['results'], key=lambda x: x['status']):
    flag = 'OK' if r['result']=='PASS' else r['result']
    print(f"  [{flag:4s}] {r['method']:6s} {r['path']:60s} status={r['status']:4d} {r['ms']:4d}ms  {r['tag']}")
