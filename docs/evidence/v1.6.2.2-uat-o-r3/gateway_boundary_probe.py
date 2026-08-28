"""Synthetic upload validity and concurrency controls; do not contact TDSQL."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import requests
HERE=Path(__file__).resolve().parent;BASE='http://127.0.0.1:8003'
s=requests.Session();r=s.post(BASE+'/api/v1/auth/login',json={'username':'admin','password':os.environ['UAT_O_PASSWORD']},timeout=15);r.raise_for_status();s.headers['Authorization']='Bearer '+r.json()['token']
header=dict(s.headers);rows=[]
def upload(name,content,kind='interf'):
    r=requests.post(BASE+'/api/v1/gateway-log/upload',headers=header,data={'connection_id':'uat_o_offline','log_type':kind},files={'file':(name,content,'text/plain')},timeout=40)
    row={'name':name,'bytes':len(content),'type':kind,'status':r.status_code,'response':r.json()}
    if r.ok:
        rid=r.json()['report_id'];res=s.get(BASE+f'/api/v1/gateway-log/reports/{rid}',timeout=10);html=res.json()['report_html']
        (HERE/(name+'.html')).write_text(html,encoding='utf-8');row['report_chars']=len(html);row['has_overview']='日志概览' in html;row['has_0_requests']='>0</' in html
    return row
for args in [('gateway_empty',b''),('gateway_invalid',b'not a gateway log\nnot another log\n')]:rows.append(upload(*args))
source=(HERE.parent/'v1.6.2.2-uat-o-r1/uat_gateway_interf.txt').read_bytes()
with ThreadPoolExecutor(max_workers=2) as executor:rows+=list(executor.map(lambda args:upload(*args),[('gateway_concurrent_a',source),('gateway_concurrent_b',source*3)]))
r=s.get(BASE+'/',timeout=10)
(HERE/'browser_document_headers.json').write_text(json.dumps({k:v for k,v in r.headers.items() if k.lower() in ('content-security-policy','x-frame-options','content-type')},indent=2),encoding='utf-8')
(HERE/'gateway_boundary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(rows,ensure_ascii=False))
