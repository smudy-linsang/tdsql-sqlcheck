"""UAT v1.6.3.5: 产物分页/SQL/HTML 验证。
SUCCEEDED 任务 job_id=71d3fe095e474f1e9647e0fa28e360d3
"""
import urllib.request, json
base = 'http://127.0.0.1:8003'
data = json.dumps({'username':'admin','password':'UatV1635@M!'}).encode()
req = urllib.request.Request(base+'/api/v1/auth/login', data=data, headers={'Content-Type':'application/json'}, method='POST')
r = urllib.request.urlopen(req, timeout=5)
token = json.loads(r.read().decode())['token']
headers = {'Authorization':'Bearer '+token}
job_id = '71d3fe095e474f1e9647e0fa28e360d3'

# 1) 任务详情（完整）
req = urllib.request.Request(base+f'/api/v1/audit/metadata-jobs/{job_id}', headers=headers)
r = urllib.request.urlopen(req, timeout=5)
out = json.loads(r.read().decode())
print('=== 1) 任务详情 ===')
for k in ('state','phase','connection_name','database','progress','error_code','error_message','report_id','snapshot_id','exit_code','cleanup_ok','finished_at'):
    print(f'  {k}: {out.get(k)}')

# 2) 产物分页
print('\n=== 2) 产物分页 (limit=3, offset=0) ===')
req = urllib.request.Request(base+f'/api/v1/audit/metadata-jobs/{job_id}/results?limit=3&offset=0', headers=headers)
r = urllib.request.urlopen(req, timeout=5)
out = json.loads(r.read().decode())
print(f'  total: {out.get("total")}')
print(f'  items 长度: {len(out.get("items", []))}')
if out.get('items'):
    item0 = out['items'][0]
    print(f'  items[0] keys: {list(item0.keys())}')

# 3) SQL 全文导出
print('\n=== 3) SQL 全文导出 ===')
req = urllib.request.Request(base+f'/api/v1/audit/metadata-jobs/{job_id}/sql', headers=headers)
try:
    r = urllib.request.urlopen(req, timeout=5)
    sql_text = r.read().decode('utf-8', errors='replace')
    print(f'  SQL 长度: {len(sql_text)}')
    print(f'  前 300 字符: {sql_text[:300]}')
    with open('C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/evidence/v1.6.3.5-uat-m/uat_v1635_extracted.sql','w',encoding='utf-8') as f:
        f.write(sql_text)
except urllib.error.HTTPError as e:
    print(f'  err: {e.code} {e.read().decode()[:200]}')

# 4) HTML 报告
print('\n=== 4) HTML 报告 ===')
req = urllib.request.Request(base+f'/api/v1/audit/metadata-jobs/{job_id}/html', headers=headers)
try:
    r = urllib.request.urlopen(req, timeout=5)
    html = r.read().decode('utf-8', errors='replace')
    print(f'  HTML 长度: {len(html)}')
    print(f'  含"实例连接名称": {"实例连接名称" in html}')
    print(f'  含 smoke-g14-local: {"smoke-g14-local" in html}')
    print(f'  含 tdsql_sqlcheck: {"tdsql_sqlcheck" in html}')
    with open('C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/evidence/v1.6.3.5-uat-m/uat_v1635_report.html','w',encoding='utf-8') as f:
        f.write(html)
except urllib.error.HTTPError as e:
    print(f'  err: {e.code} {e.read().decode()[:200]}')

# 5) 取消测试
print('\n=== 5) 取消已 SUCCEEDED 的任务（应 409 或 200 + no-op）===')
req = urllib.request.Request(base+f'/api/v1/audit/metadata-jobs/{job_id}/cancel', data=b'{}',
                              headers={**headers,'Content-Type':'application/json'}, method='POST')
try:
    r = urllib.request.urlopen(req, timeout=5)
    print(f'  status={r.status} body={r.read().decode()[:200]}')
except urllib.error.HTTPError as e:
    print(f'  err: {e.code} {e.read().decode()[:200]}')
