"""Compare engine versus service for the routine-with-semicolons corpus case."""
import json
import os
import sys
from pathlib import Path
if not os.environ.get('SQLCHECK_DB_NAME','').startswith('tdsql_uat_o_'):
    raise SystemExit('UAT database required')
sys.path.insert(0,sys.argv[1])
from backend.services.audit_service import AuditService
from backend.services.database import split_sql_statements
data=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
sql=next(r['sql'] for r in data['rows'] if r['id']=='corpus:01_naming_ddl.sql:R030_R031_01:distributed')
s=AuditService()
rows=[]
for scope in ['distributed','centralized']:
    direct=s.checker.audit_sql(sql,instance_type=scope)
    result,gate,context=s.audit_single_sql(sql,created_by='UAT-O',instance_type=scope)
    rows.append({'scope':scope,'direct':sorted({v.rule_id for v in direct.violations}),'service':sorted({v.rule_id for v in result.violations}),'statement_count':len(split_sql_statements(sql))})
Path(sys.argv[3]).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(rows,ensure_ascii=False))
