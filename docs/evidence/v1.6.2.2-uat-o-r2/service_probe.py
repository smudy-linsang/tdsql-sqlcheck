"""Replay HTTP/engine differences through each revision's service layer."""
import json
import os
from pathlib import Path
import sys
if not os.environ.get('SQLCHECK_DB_NAME','').startswith('tdsql_uat_o_reg_r2_'):
    raise SystemExit('Isolated round-two diagnostic database required')
HERE = Path(__file__).resolve().parent
sys.path.insert(0, sys.argv[1])
from backend.services.audit_service import AuditService
from backend.services.database import ensure_db, split_sql_statements
ensure_db()
service = AuditService()
cases = {}
for name in ('rule_probe_current.json','edge_current.json'):
    for row in json.loads((HERE/name).read_text(encoding='utf-8'))['rows']:
        cases[row['id']] = row
records = json.loads((HERE/'http_results.json').read_text(encoding='utf-8'))
rows = []
for rec in records:
    if rec.get('engine_equals_http') is not False:
        continue
    case = cases[rec['id']]
    scope = case.get('instance_type','distributed')
    direct = service.checker.audit_sql(case['sql'],instance_type=scope)
    result, gate, context = service.audit_single_sql(case['sql'],created_by='UAT-O-R2',instance_type=scope)
    rows.append({'id':case['id'],'sql':case['sql'],'scope':scope,'direct':sorted({v.rule_id for v in direct.violations}),'service':sorted({v.rule_id for v in result.violations}),'statement_count':len(split_sql_statements(case['sql']))})
Path(sys.argv[2]).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
print('SERVICE_CASES',len(rows))
