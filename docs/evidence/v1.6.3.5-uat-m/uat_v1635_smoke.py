"""UAT v1.6.3.5 第一轮端到端冒烟：修正版（按新 API 要求）。"""
import urllib.request, json, time

base = 'http://127.0.0.1:8003'

# 1. 登录
data = json.dumps({'username':'admin','password':'UatV1635@M!'}).encode()
req = urllib.request.Request(base+'/api/v1/auth/login', data=data, headers={'Content-Type':'application/json'}, method='POST')
r = urllib.request.urlopen(req, timeout=5)
token = json.loads(r.read().decode())['token']
print(f'[1] login OK token_len={len(token)}')
headers = {'Content-Type':'application/json','Authorization':'Bearer '+token}

# 2. 查连接
req = urllib.request.Request(base+'/api/v1/tdsql/connections', headers=headers)
r = urllib.request.urlopen(req, timeout=10)
conns = json.loads(r.read().decode()).get('connections', [])
print(f'[2] connections={len(conns)}')
target = next((c for c in conns if c.get('is_distributed')==1 and 'bad' not in c['name'].lower()), None)
if not target:
    target = next((c for c in conns if c.get('is_distributed')==1), None) or conns[0]
print(f'[2.1] target: {target["id"]} {target["name"]}')

# 3. 旧 API 410
req = urllib.request.Request(base+'/api/v1/audit/extract-and-audit', data=b'{}',
                              headers={**headers, 'Content-Type':'application/json'}, method='POST')
try:
    r = urllib.request.urlopen(req, timeout=5)
    print(f'[3] 旧 API status={r.status} body={r.read().decode()[:200]}')
except urllib.error.HTTPError as e:
    print(f'[3] 旧 API status={e.code} body={e.read().decode()[:200]}')

# 4. 新 API 列表
req = urllib.request.Request(base+'/api/v1/audit/metadata-jobs?limit=10', headers=headers)
r = urllib.request.urlopen(req, timeout=5)
print(f'[4] jobs list: {r.read().decode()[:200]}')

# 5. 受理（按真实 API 契约：Idempotency-Key 头 + client_submission_key 字段）
print(f'\n[5] 受理元数据任务：')
idem = 'uat-v1635-smoke-' + str(int(time.time()))
body = json.dumps({
    'connection_id': target['id'],
    'instance_type': 'distributed',
    'databases': ['tdsql_sqlcheck'],
    'enable_metadata': True,
    'include_views': False,
    'include_partition': True,
    'client_submission_key': idem,
}).encode('utf-8')
req = urllib.request.Request(base+'/api/v1/audit/metadata-jobs', data=body,
                              headers={**headers, 'Idempotency-Key': idem, 'X-Client-Submission-Key': idem},
                              method='POST')
try:
    r = urllib.request.urlopen(req, timeout=15)
    out = json.loads(r.read().decode())
    print(f'  status={r.status} body={json.dumps(out, ensure_ascii=False)[:500]}')
    job_id = out.get('job_id') or out.get('id')
    print(f'  job_id={job_id}')
except urllib.error.HTTPError as e:
    print(f'  status={e.code} body={e.read().decode()[:500]}')
    job_id = None

# 6. 进度查询（最多 60 次 × 2s = 120s）
if job_id:
    print(f'\n[6] 进度查询 job_id={job_id}：')
    for i in range(60):
        time.sleep(2)
        try:
            req = urllib.request.Request(base+f'/api/v1/audit/metadata-jobs/{job_id}', headers=headers)
            r = urllib.request.urlopen(req, timeout=5)
            out = json.loads(r.read().decode())
            st = out.get('state'); ph = out.get('phase')
            pg = out.get('progress',{})
            err = out.get('error_code')
            print(f'  [{(i+1)*2:3d}s] state={st} phase={ph} {pg.get("completed")}/{pg.get("total")} err={err}')
            if st in ('SUCCEEDED','FAILED','CANCELLED'):
                print(f'\n  最终状态: {st}')
                print(f'  完整: {json.dumps(out, ensure_ascii=False)[:1500]}')
                break
        except Exception as e:
            print(f'  [{(i+1)*2:3d}s] err: {e}')
else:
    print('  no job_id, skip polling')
