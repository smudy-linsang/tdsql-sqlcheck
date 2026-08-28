"""Generate the exhaustive 13-class change ledger and unchanged-file evidence."""
import collections
import hashlib
import json
import subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
a=json.loads((HERE/'rule_probe_baseline.json').read_text(encoding='utf-8'))
b=json.loads((HERE/'rule_probe_current.json').read_text(encoding='utf-8'))
groups=collections.defaultdict(list)
for old,new in zip(a['rows'],b['rows']):
    assert old['id']==new['id']
    if old['fired']!=new['fired']:
        key=(bool(old['parse_error']),bool(new['parse_error']),tuple(sorted(set(new['fired'])-set(old['fired']))),tuple(sorted(set(old['fired'])-set(new['fired']))))
        groups[key].append(new['id'])
out={'categories':[{'baseline_parse_error':k[0],'current_parse_error':k[1],'added':k[2],'removed':k[3],'count':len(v),'case_ids':v} for k,v in sorted(groups.items(),key=lambda x:-len(x[1]))]}
out['unchanged_files']=[]
for name in ['backend/engine/checker.py','backend/services/audit_service.py','backend/services/database.py','backend/services/gateway_log_service.py','backend/services/gateway_log_analysis/analyze_gateway_log.py','backend/services/ppt_report_service.py','frontend/index.html','frontend/static/js/app.js']:
    old=subprocess.check_output(['git','show','0079300:'+name],cwd=REPO)
    # Git blob hash ignores local CRLF checkout conversion.
    new=subprocess.check_output(['git','show','HEAD:'+name],cwd=REPO)
    out['unchanged_files'].append({'file':name,'unchanged':old==new,'sha256':hashlib.sha256(new).hexdigest()})
(HERE/'delta_classification.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print('CATEGORIES',len(groups),'TOTAL_CHANGED',sum(map(len,groups.values())))
print('BASELINE_IDENTICAL_FILES',sum(r['unchanged'] for r in out['unchanged_files']))
