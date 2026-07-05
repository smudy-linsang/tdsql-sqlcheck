"""测试文件审核报告接口"""
import requests, json

BASE = "http://127.0.0.1:8000"
r = requests.post(f"{BASE}/api/v1/auth/login",
                  json={"username": "admin", "password": "Abcd1234"})
h = {"Authorization": f"Bearer {r.json()['token']}"}
print("登录成功")

# 1. 执行一次文件审核（产生审核历史）
r = requests.post(f"{BASE}/api/v1/audit/file", headers=h,
                  json={"content": "CREATE TABLE t1 (id INT); SELECT * FROM t1 WHERE id=1;"})
print(f"\n[文件审核] status={r.status_code}")

# 2. 获取报告列表
r = requests.get(f"{BASE}/api/v1/audit/file-reports?limit=5", headers=h)
data = r.json()
print(f"\n[报告列表] total={data['total']}, items={len(data['items'])}")
for item in data['items'][:3]:
    print(f"  #{item['id']} {item['source']} | by={item.get('created_by','')} | rate={item.get('pass_rate',0)} | {item.get('created_at','')[:19]}")

# 3. 下载HTML报告（取最新一条）
if data['items']:
    report_id = data['items'][0]['id']
    r = requests.get(f"{BASE}/api/v1/audit/file-reports/{report_id}/html", headers=h)
    print(f"\n[HTML报告] status={r.status_code}, length={len(r.text)}")
    print(f"  content-type: {r.headers.get('content-type')}")
    # 检查HTML关键字段
    html = r.text
    checks = ['审核人', '文件名', '审核时间', 'SQL总数', '通过率', '逐条审核结果']
    for kw in checks:
        found = kw in html
        print(f"  包含'{kw}': {'YES' if found else 'NO'}")
