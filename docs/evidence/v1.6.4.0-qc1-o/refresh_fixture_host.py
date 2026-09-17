"""Refresh ONLY QC gateway TLS after laptop network changes; no database writes."""
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[3]
RT=ROOT/'data/reports/qc_o_1640'
D=ROOT/'docs/evidence/v1.6.4.0-uat-d'
sys.path.insert(0,str(D))
import _boot
import certifi
original=Path(certifi.where()); before=original.read_bytes()
host=sys.argv[1]
src=(D/'prepare_uat_d40.py').read_text(encoding='utf-8').replace('uat_d_1640','qc_o_1640')
scope={'__name__':'qc_cert_refresh','__file__':str(D/'prepare_uat_d40.py')}
exec(compile(src,str(D/'prepare_uat_d40.py'),'exec'),scope)
scope['GW_HOST']=host
certifi.where=lambda:str(RT/'trust/certifi/cacert.pem')
scope['cmd_certs']()
assert original.read_bytes()==before
p=RT/'copilot-endpoints.json'; obj=json.loads(p.read_text())
for ep in obj['endpoints']:
    ep['canonical_host']=host
    ep['allowed_resolved_cidrs']=[host+'/32']
p.write_text(json.dumps(obj,indent=2))
(RT/'gateway-host.txt').write_text(host)
print('Only QC runtime endpoint and process-local CA updated; installed certifi unchanged.')
