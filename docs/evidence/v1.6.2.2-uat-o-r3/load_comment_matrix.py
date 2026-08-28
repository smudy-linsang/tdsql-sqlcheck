"""R042 must be invariant to ordinary comments. Audit only, no LOAD executed."""
import argparse
import json
import logging
from pathlib import Path
import sys
ap=argparse.ArgumentParser();ap.add_argument('--repo',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();sys.path.insert(0,a.repo)
logging.getLogger('sqlglot').setLevel(logging.ERROR)
from backend.engine.checker import RuleChecker
import sqlglot
c=RuleChecker();rows=[]
comments=['',"# operator's note\n",'# double " note\n','# tick ` note\n','# two \' quotes \'\n','-- operator\'s note\n','/* operator\'s note */\n','# ordinary\r\n',"# operator's note\r\n"]
sqls=["LOAD XML INFILE '/tmp/synthetic.xml' INTO TABLE t", "LOAD DATA INFILE '/tmp/synthetic.csv' INTO TABLE t", "LOAD DATA LOCAL INFILE '/tmp/synthetic.csv' INTO TABLE t"]
for i,comment in enumerate(comments):
    for j,body in enumerate(sqls):
        sql=comment+body;p=c.parser.parse(sql);r=c.audit_sql(sql,instance_type='distributed')
        rows.append({'id':f'load:{i}:{j}','sql':sql,'parse_error':p.parse_error,'ast':type(p.ast).__name__,'has_load_data':p.has_load_data,'passed':r.passed,'fired':sorted({v.rule_id for v in r.violations})})
result={'version':sqlglot.__version__,'count':len(rows),'missing_r042':[r['id'] for r in rows if 'R042' not in r['fired']],'false_pass':[r['id'] for r in rows if r['passed']],'rows':rows}
Path(a.out).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k!='rows'}))
