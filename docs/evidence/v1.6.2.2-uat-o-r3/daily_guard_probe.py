"""Verify real no-monitordb failure behind the browser completion message."""
import json
from pathlib import Path
from http_round3 import session, BASE
HERE=Path(__file__).resolve().parent
s=session('admin'); rows=[]
for date in ('2026-08-28','2026-08-29'):
    r=s.post(BASE+'/api/v1/daily-inspect/run',json={'connection_id':'uat_o_local','inspect_date':date},timeout=30)
    rows.append({'date':date,'status':r.status_code,'body':r.json()})
r=s.get(BASE+'/api/v1/daily-inspect/compare',params={'connection_id':'uat_o_local','date1':'2026-08-28','date2':'2026-08-29'},timeout=15)
rows.append({'action':'compare','status':r.status_code,'body':r.json()})
(HERE/'daily_guard.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print([(r.get('date',r.get('action')),r['status']) for r in rows])
