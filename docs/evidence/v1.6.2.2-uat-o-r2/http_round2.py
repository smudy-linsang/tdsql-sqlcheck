"""Authenticated loopback API tests, supplementary to actual browser clicks."""
import json
import os
from pathlib import Path
import requests

HERE = Path(__file__).resolve().parent
BASE = 'http://127.0.0.1:8002'
PW = os.environ['UAT_O_PASSWORD']


def session(username):
    s = requests.Session()
    r = s.post(BASE + '/api/v1/auth/login', json={'username':username,'password':PW},timeout=15)
    r.raise_for_status()
    s.headers['Authorization'] = 'Bearer ' + r.json()['token']
    return s


def main():
    s = session('admin')
    core = json.loads((HERE / 'rule_probe_current.json').read_text(encoding='utf-8'))
    edge = json.loads((HERE / 'edge_current.json').read_text(encoding='utf-8'))
    rows = []
    for path in ['/health','/api/v1/audit/rules','/api/v1/tdsql/connections/options']:
        r = s.get(BASE + path,timeout=20)
        rows.append({'endpoint':path,'status':r.status_code,'body':r.json()})
    selected = [r for r in core['rows'] if r['id'].startswith(('corpus:','fixture:','kfn_'))]
    for row in selected + edge['rows']:
        r = s.post(BASE + '/api/v1/audit/sql', json={'sql':row['sql'],'instance_type':row.get('instance_type','distributed')},timeout=30)
        body = r.json()
        fired = sorted({v['rule_id'] for v in body.get('violations',[])})
        rows.append({'id':row['id'],'endpoint':'/api/v1/audit/sql','status':r.status_code,'fired':fired,'passed':body.get('passed'),'engine_equals_http':fired==row['fired']})
    controls = [next(r for r in core['rows'] if r['id']==name) for name in ('kfn_comment:0:block','kfn_literal:19','kfn_literal:20')]
    for row in controls:
        for entry in ('file','upload'):
            if entry == 'file':
                r = s.post(BASE + '/api/v1/audit/file',json={'content':row['sql'],'file_path':'uat_r2_guard.sql','instance_type':'distributed'},timeout=30)
            else:
                r = s.post(BASE + '/api/v1/audit/upload',files={'file':('uat_r2_guard.sql',row['sql'],'text/plain')},data={'instance_type':'distributed'},timeout=30)
            rows.append({'id':row['id'],'endpoint':'/api/v1/audit/'+entry,'status':r.status_code,'body':r.json()})
    for user in ('uat_o_developer','uat_o_auditor','uat_o_dba'):
        role_s = session(user)
        for path in ('/api/v1/tdsql/connections/options','/api/v1/tdsql/connections'):
            r = role_s.get(BASE+path,timeout=20)
            body = r.json()
            rows.append({'user':user,'endpoint':path,'status':r.status_code,'body':body})
        if user != 'uat_o_dba':
            r = role_s.post(BASE+'/api/v1/tdsql/connections',json={'name':'uat_r2_forbidden','host':'127.0.0.1','port':1,'username':'synthetic','password':''},timeout=20)
            rows.append({'user':user,'endpoint':'/api/v1/tdsql/connections','method':'POST','status':r.status_code})
    (HERE / 'http_results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    print('HTTP_RECORDS',len(rows),'5XX',sum(r['status']>=500 for r in rows),'ENGINE_MISMATCH',sum(r.get('engine_equals_http') is False for r in rows))


if __name__ == '__main__':
    main()
