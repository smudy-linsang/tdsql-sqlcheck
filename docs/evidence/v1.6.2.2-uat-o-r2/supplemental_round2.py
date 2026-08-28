"""Read-only report content/API validation after real browser operations."""
import json
import os
from pathlib import Path
import sys
import requests
HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[2]))
if os.environ.get('SQLCHECK_DB_NAME') != 'tdsql_uat_o_r2_1622_20260828':
    raise SystemExit('Wrong UAT database')
from backend.services.database import _get_connection
BASE = 'http://127.0.0.1:8002'
s = requests.Session()
r = s.post(BASE+'/api/v1/auth/login',json={'username':'admin','password':os.environ['UAT_O_PASSWORD']},timeout=15)
r.raise_for_status()
s.headers['Authorization'] = 'Bearer '+r.json()['token']
rows = []
c = _get_connection()
for query in ['SELECT VERSION() AS database_version',"SELECT COUNT(*) AS inspection_rows FROM daily_inspection WHERE connection_id='uat_o_local'","SELECT COUNT(*) AS bigtable_rows FROM bigtable_history WHERE connection_id='uat_o_local'"]:
    rows.append(dict(c.execute(query).fetchone()))
c.close()
for path in ['/health','/api/v1/ppt-report/dashboard?connection_id=uat_o_local','/api/v1/gateway-log/reports?connection_id=uat_o_local','/api/v1/audit/file-reports','/api/v1/audit/extracted-reports']:
    r = s.get(BASE+path,timeout=30)
    data = r.json()
    rows.append({'endpoint':path,'status':r.status_code,'body':data})
    if path.startswith('/api/v1/gateway-log/reports') and r.ok:
        for record in data:
            html = s.get(BASE+'/api/v1/gateway-log/reports/'+str(record['id']),timeout=20).json().get('report_html','')
            (HERE/'gateway_report.html').write_text(html,encoding='utf-8',newline='\n')
            rows.append({'gateway_report_id':record['id'],'html_chars':len(html),'has_h1':'<h1' in html})
    if path == '/api/v1/audit/file-reports' and r.ok:
        reports = data.get('items',data.get('reports',[])) if isinstance(data,dict) else data
        for report in reports[:3]:
            exported = s.get(BASE+f"/api/v1/audit/file-reports/{report['id']}/html",timeout=20)
            rows.append({'file_report_id':report['id'],'html_export_status':exported.status_code,'bytes':len(exported.content)})
            (HERE/f"file_report_{report['id']}.html").write_bytes(exported.content)
for path in ['/api/v1/cluster-inspect/run','/api/v1/sql-stats/analyze']:
    r = s.post(BASE+path,json={'connection_id':'uat_o_local'},timeout=30)
    rows.append({'endpoint':path,'status':r.status_code,'body':r.json()})
r = s.get(BASE+'/api/v1/ppt-report/generate?connection_id=uat_o_local',timeout=45)
rows.append({'pdf_status':r.status_code,'bytes':len(r.content),'is_pdf':r.content.startswith(b'%PDF-')})
if r.ok:
    (HERE/'ops_report.pdf').write_bytes(r.content)
(HERE/'supplemental_results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
print(json.dumps([{k:v for k,v in row.items() if k!='body'} for row in rows],ensure_ascii=False))
