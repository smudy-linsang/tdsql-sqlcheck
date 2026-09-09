"""UAT v1.6.3.5 第二步：检查刚才 WAITING 任务是否被 runner 认领。"""
import urllib.request, json
base = 'http://127.0.0.1:8003'
data = json.dumps({'username':'admin','password':'UatV1635@M!'}).encode()
req = urllib.request.Request(base+'/api/v1/auth/login', data=data, headers={'Content-Type':'application/json'}, method='POST')
r = urllib.request.urlopen(req, timeout=5)
token = json.loads(r.read().decode())['token']
headers = {'Authorization':'Bearer '+token}

# 查刚才 9d9e1fc4 任务
req = urllib.request.Request(base+'/api/v1/audit/metadata-jobs/9d9e1fc4fcff458a9fe40cfdab40050b', headers=headers)
r = urllib.request.urlopen(req, timeout=5)
out = json.loads(r.read().decode())
print('task status:')
for k in ('state','phase','connection_name','database','elapsed_seconds','progress','error_code','error_message','report_id','snapshot_id','exit_code','created_at','updated_at'):
    print(f'  {k}: {out.get(k)}')

# 列最新 jobs
print('\njobs list:')
req = urllib.request.Request(base+'/api/v1/audit/metadata-jobs?limit=5', headers=headers)
r = urllib.request.urlopen(req, timeout=5)
print(r.read().decode()[:1500])
