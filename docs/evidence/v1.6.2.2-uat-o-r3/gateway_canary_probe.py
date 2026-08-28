"""Only a generated NON-secret canary; test platform masking contract."""
import json
import os
from pathlib import Path
import requests
import sys
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parents[2]))
if os.environ.get('SQLCHECK_DB_NAME')!='tdsql_uat_o_r3_1622_20260828':raise SystemExit('Wrong database')
from backend import config
BASE='http://127.0.0.1:8003';s=requests.Session()
r=s.post(BASE+'/api/v1/auth/login',json={'username':'admin','password':os.environ['UAT_O_PASSWORD']},timeout=15);r.raise_for_status();s.headers['Authorization']='Bearer '+r.json()['token']
canary='UAT_O_R3_NOT_A_REAL_SECRET'
content=f"[2026-08-29 00:00:02 12346] INFO topic=test&timecost=1500.2&sql=select id from t_uat_order where code='{canary}'&db=tdsql_uat_o_target_1622&user=synthetic\n"
r=s.post(BASE+'/api/v1/gateway-log/upload',data={'connection_id':'uat_o_offline','log_type':'interf'},files={'file':('gateway_mask_canary.log',content,'text/plain')},timeout=40)
row={'data_masking_enabled':config.data_masking_enabled(),'status':r.status_code,'body':r.json()}
if r.ok:
    data=s.get(BASE+f"/api/v1/gateway-log/reports/{r.json()['report_id']}",timeout=15).json();html=data['report_html']
    (HERE/'gateway_mask_canary.html').write_text(html,encoding='utf-8')
    row['canary_occurrences_in_persisted_html']=html.count(canary)
    row['has_masked_fingerprint']='where code=?' in html
(HERE/'gateway_mask_canary.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(row,ensure_ascii=False))
