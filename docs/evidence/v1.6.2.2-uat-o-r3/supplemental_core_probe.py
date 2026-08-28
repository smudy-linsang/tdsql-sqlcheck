"""Fill previous coverage gaps and verify comment-boundary regression via HTTP."""
import json
import os
from pathlib import Path
import requests
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[2]))
from backend.engine.checker import RuleChecker
c=RuleChecker();rows=[]
cases=[('R025','ALTER TABLE t_customer MODIFY cust_id BIGINT',{'t_customer':{'shard_key':'cust_id'}}),('R035',"CREATE TABLE t_customer(id BIGINT NOT NULL COMMENT 'id', PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='customer' shardkey=id",{'existing_columns':{'id':'VARCHAR(10)'}}),('R038',"CREATE TABLE t_order_log(id BIGINT NOT NULL AUTO_INCREMENT COMMENT 'id', PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='log' shardkey=id",None),('R049','SELECT a.id FROM t_a a JOIN t_b a ON a.id=a.id',None),('R059','BEGIN',{'t_customer':{'shard_key':'cust_id'}})]
for rid,sql,meta in cases:
    p=c.parser.parse(sql);r=c.audit_sql(sql,table_metadata=meta,instance_type='distributed')
    rows.append({'id':rid,'sql':sql,'metadata':meta,'fired':sorted({v.rule_id for v in r.violations}),'parsed_columns':p.columns,'alter_actions':p.alter_actions})
s=requests.Session();BASE='http://127.0.0.1:8003'
r=s.post(BASE+'/api/v1/auth/login',json={'username':'admin','password':os.environ['UAT_O_PASSWORD']},timeout=15);r.raise_for_status();s.headers['Authorization']='Bearer '+r.json()['token']
heads=json.loads((HERE/'head_current.json').read_text(encoding='utf-8'))['rows']
for entry in [r for r in heads if r['id'].startswith(('head:4:','head:5:'))]:
    for route in ('sql','file','upload'):
        if route=='upload':r=s.post(BASE+'/api/v1/audit/upload',files={'file':('uat_o_load_guard.sql',entry['sql'],'text/plain')},data={'instance_type':'distributed'},timeout=20)
        else:r=s.post(BASE+'/api/v1/audit/'+route,json=({'sql':entry['sql'],'instance_type':'distributed'} if route=='sql' else {'content':entry['sql'],'file_path':'uat_o_load_guard.sql','instance_type':'distributed'}),timeout=20)
        rows.append({'id':entry['id'],'sql':entry['sql'],'route':route,'status':r.status_code,'body':r.json()})
(HERE/'supplemental_core.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print('SUPPLEMENTAL',len(rows),'GAP_FIRES',[(r['id'],r['id'] in r['fired']) for r in rows[:5]])
