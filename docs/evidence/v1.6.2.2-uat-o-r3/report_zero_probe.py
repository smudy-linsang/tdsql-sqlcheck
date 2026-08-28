"""Synthetic persisted zero-finding run contract, distinct from live browser run."""
import json
import os
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parents[2]))
if os.environ.get('SQLCHECK_DB_NAME')!='tdsql_uat_o_r3_1622_20260828':raise SystemExit('Wrong database')
from backend.services.database import _get_connection
from backend.services.ppt_report_service import ppt_report_service
cid='uat_o_r3_report_zero_fixture'
c=_get_connection()
try:
    run=c.execute('SELECT * FROM index_audit WHERE connection_id=?',(cid,)).fetchone()
    if not run:
        c.execute("INSERT INTO index_audit(connection_id,database_filter,total_tables,total_indexes,total_findings,created_by) VALUES (?,?,?,?,?,?)",(cid,'synthetic_only',2,4,0,'UAT-O-R3 fixture'))
        c.commit()
    run=dict(c.execute('SELECT * FROM index_audit WHERE connection_id=?',(cid,)).fetchone())
finally:c.close()
data=ppt_report_service.generate_report_data(cid)
(HERE/'report_zero_contract.json').write_text(json.dumps({'fixture_only':True,'persisted_completed_index_run':run,'report_modules':data['modules']},ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print('completed_tables',run['total_tables'],'report_status',data['modules']['index_analysis']['data_status'],'reported_indexes',data['modules']['index_analysis']['summary']['total_indexes'])
