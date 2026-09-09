"""UAT v1.6.3.5：用本地元数据库作为 target (Q 自报路径)。
Q 文档：'建任务 ACCEPTED → runner 认领 RUNNING → worker 提取 tdsql_sqlcheck 63 对象 → SUCCEEDED'
"""
import urllib.request, json, time
base = 'http://127.0.0.1:8003'

# 登录
data = json.dumps({'username':'admin','password':'UatV1635@M!'}).encode()
req = urllib.request.Request(base+'/api/v1/auth/login', data=data, headers={'Content-Type':'application/json'}, method='POST')
r = urllib.request.urlopen(req, timeout=5)
token = json.loads(r.read().decode())['token']
headers = {'Content-Type':'application/json','Authorization':'Bearer '+token}

# 查连接，找 database=tdsql_sqlcheck 的连接（如果有）
req = urllib.request.Request(base+'/api/v1/tdsql/connections', headers=headers)
r = urllib.request.urlopen(req, timeout=10)
conns = json.loads(r.read().decode()).get('connections', [])
print(f'connections={len(conns)}')
for c in conns:
    print(f"  id={c['id']:20s} name='{c['name']}' host={c['host']:15s} port={c['port']:6d} dist={c.get('is_distributed',0)} db={c.get('database','')}")

# 试 smoke-g14-local (127.0.0.1:13306, tdsql_sqlcheck) 或者 smoke_uat_g14
local_target = next((c for c in conns if 'smoke' in c['name'].lower() or c.get('host')=='127.0.0.1'), None)
if not local_target:
    print('无本地目标，使用内网分布式实例')
    local_target = conns[0]
print(f'\n使用 target: {local_target["id"]} {local_target["name"]} ({local_target["host"]}:{local_target["port"]}, db={local_target.get("database","")})')

# 受理任务
print('\n受理元数据任务：')
idem = 'uat-v1635-target-' + str(int(time.time()))
body = json.dumps({
    'connection_id': local_target['id'],
    'instance_type': 'centralized' if local_target.get('is_distributed')==0 else 'distributed',
    'database': 'tdsql_sqlcheck',
    'enable_metadata': True,
    'include_views': False,
    'include_partition': True,
    'scopes': ['TABLE','INDEX','VIEW','SHARDKEY'],
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
except urllib.error.HTTPError as e:
    print(f'  status={e.code} body={e.read().decode()[:500]}')
    job_id = None

# 进度查询
if job_id:
    print(f'\n进度查询 job_id={job_id}：')
    for i in range(60):
        time.sleep(3)
        try:
            req = urllib.request.Request(base+f'/api/v1/audit/metadata-jobs/{job_id}', headers=headers)
            r = urllib.request.urlopen(req, timeout=5)
            out = json.loads(r.read().decode())
            st = out.get('state'); ph = out.get('phase')
            pg = out.get('progress',{})
            print(f'  [{(i+1)*3:3d}s] state={st} phase={ph} {pg.get("extracted_objects")}/{pg.get("selected_objects")} err={out.get("error_code")}')
            if st in ('SUCCEEDED','FAILED','CANCELLED'):
                print(f'\n  最终: {st} | err={out.get("error_code")} | {out.get("error_message","")[:200]}')
                print(f'  report_id={out.get("report_id")} snapshot_id={out.get("snapshot_id")} exit_code={out.get("exit_code")}')
                print(f'  完整progress: {out.get("progress")}')
                break
        except Exception as e:
            print(f'  [{(i+1)*3:3d}s] err: {e}')
