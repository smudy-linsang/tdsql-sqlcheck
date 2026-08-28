"""Read-only EXPLAIN controls in isolated UAT environment; no audited SQL runs."""
import json
import os
from pathlib import Path
import requests
HERE=Path(__file__).resolve().parent
BASE='http://127.0.0.1:8003'
s=requests.Session(); r=s.post(BASE+'/api/v1/auth/login',json={'username':'admin','password':os.environ['UAT_O_PASSWORD']},timeout=15);r.raise_for_status();s.headers['Authorization']='Bearer '+r.json()['token']
rows=[]
for db in (None,'uat_o_r3_workflow','tdsql_uat_o_target_1622','information_schema'):
    req={'connection_id':'uat_o_local','sql':'SELECT id FROM tdsql_uat_o_target_1622.t_uat_order WHERE customer_id = 1'}
    if db: req['db_name']=db
    r=s.post(BASE+'/api/v1/slow-queries/analyze-explain-by-sql',json=req,timeout=30)
    rows.append({'request':req,'status':r.status_code,'response':r.json()})
(HERE/'explain_context_results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
for cid in ('uat_o_local','uat_o_offline'):
    r=s.get(BASE+'/api/v1/ppt-report/dashboard',params={'connection_id':cid},timeout=20)
    (HERE/('dashboard_initial_'+cid+'.json')).write_text(json.dumps(r.json(),ensure_ascii=False,indent=2),encoding='utf-8')
    r=s.get(BASE+'/api/v1/ppt-report/generate',params={'connection_id':cid},timeout=30)
    (HERE/('initial_'+cid+'.pdf')).write_bytes(r.content)
print(json.dumps(rows,ensure_ascii=False))
