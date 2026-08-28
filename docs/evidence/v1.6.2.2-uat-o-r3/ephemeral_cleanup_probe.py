"""White-box diagnostic of latent cleanup after replacing ONLY the missing symbol.
No product edits. This is not proof that current HTTP reaches this branch.
Retain pools to inspect explicit close calls, then always close test connections.
"""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[2]))
if os.environ.get('SQLCHECK_DB_NAME')!='tdsql_uat_o_r3_1622_20260828':raise SystemExit('Wrong database')
import backend.services.slow_query_service as module
from backend.services.tdsql_connector import TDSQLConnectionPool
from backend.services.connection_registry import registry
pools=[]
class TrackingPool(TDSQLConnectionPool):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.close_calls=0;pools.append(self)
    def close_all(self):
        self.close_calls+=1;super().close_all()
svc=module.SlowQueryService();rows=[]
with patch.object(module,'TDSQLConnectionPool',TrackingPool,create=True):
    for sql in ('SELECT id FROM tdsql_uat_o_target_1622.t_uat_order WHERE id=1','SELECT missing_column FROM tdsql_uat_o_target_1622.t_uat_order'):
        try:
            try:r=svc.analyze_explain_by_sql(sql,'uat_o_local','information_schema');out={'ok':True,'executed_sql':r['executed_sql']}
            except Exception as exc:out={'ok':False,'error':str(exc)}
            p=pools[-1];conn=getattr(p._local,'conn',None)
            rows.append({'sql':sql,'result':out,'explicit_close_calls':p.close_calls,'connection_open_after_return':bool(conn and conn.open),'saved_default_unchanged':registry.get_saved('uat_o_local')['database']=='tdsql_uat_o_target_1622'})
        finally:
            for p in pools: TDSQLConnectionPool.close_all(p)
(HERE/'ephemeral_cleanup.json').write_text(json.dumps({'diagnostic_only_missing_import_supplied':True,'rows':rows},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(rows,ensure_ascii=False))
