"""Compare actual browser-created index run with report DTO and PDF."""
import json
import os
from pathlib import Path
import requests
import sys
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parents[2]))
if os.environ.get('SQLCHECK_DB_NAME')!='tdsql_uat_o_r3_1622_20260828':raise SystemExit('Wrong database')
from backend.services.database import _get_connection
c=_get_connection()
try:
    run=dict(c.execute("SELECT * FROM index_audit WHERE connection_id='uat_o_index' ORDER BY id DESC LIMIT 1").fetchone())
    findings=[dict(r) for r in c.execute('SELECT * FROM index_audit_finding WHERE audit_id=?',(run['id'],)).fetchall()]
finally:c.close()
BASE='http://127.0.0.1:8003';s=requests.Session();r=s.post(BASE+'/api/v1/auth/login',json={'username':'admin','password':os.environ['UAT_O_PASSWORD']},timeout=15);r.raise_for_status();s.headers['Authorization']='Bearer '+r.json()['token']
r=s.get(BASE+'/api/v1/ppt-report/dashboard?connection_id=uat_o_index',timeout=20)
(HERE/'index_report_contract.json').write_text(json.dumps({'actual_browser_run':run,'actual_findings':findings,'dashboard_status':r.status_code,'dashboard':r.json()},ensure_ascii=False,indent=2,default=str),encoding='utf-8')
r=s.get(BASE+'/api/v1/ppt-report/generate?connection_id=uat_o_index',timeout=25);r.raise_for_status();(HERE/'actual_duplicate_index.pdf').write_bytes(r.content)
print('index_run',run['id'],'findings',len(findings),'pdf_bytes',len(r.content))
