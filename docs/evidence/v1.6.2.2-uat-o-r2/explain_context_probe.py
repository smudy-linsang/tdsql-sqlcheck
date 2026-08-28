"""Read-only EXPLAIN controls for the browser's stale database context."""
import json
import os
from pathlib import Path
import requests

HERE = Path(__file__).resolve().parent
base = 'http://127.0.0.1:8002'
s = requests.Session()
r = s.post(base+'/api/v1/auth/login', json={'username':'admin','password':os.environ['UAT_O_PASSWORD']}, timeout=15)
r.raise_for_status()
s.headers['Authorization'] = 'Bearer '+r.json()['token']
rows = []
for db in (None, 'uat_o_r2_workflow', 'tdsql_uat_o_target_1622'):
    body = {'connection_id':'uat_o_local','sql':'SELECT id FROM tdsql_uat_o_target_1622.t_uat_order WHERE customer_id = 1'}
    if db:
        body['db_name'] = db
    r = s.post(base+'/api/v1/slow-queries/analyze-explain-by-sql', json=body, timeout=30)
    rows.append({'request':body,'status':r.status_code,'response':r.json()})
(HERE/'explain_context_results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
print(json.dumps(rows,ensure_ascii=False,indent=2))
