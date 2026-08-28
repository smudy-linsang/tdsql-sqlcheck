"""Additional semantic-head boundaries, compared against pre-fix code. Audit only."""
import argparse
import json
import logging
from pathlib import Path
import sys

ap=argparse.ArgumentParser(); ap.add_argument('--repo',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
sys.path.insert(0,a.repo)
logging.getLogger('sqlglot').setLevel(logging.ERROR)
from backend.engine.checker import RuleChecker
import sqlglot
c=RuleChecker(); cases=[]
bodies=["CREATE PROCEDURE p() BEGIN DECLARE x INT DEFAULT 0; WHILE x<3 DO SET x=x+1; END WHILE; SELECT x; END", "CREATE FUNCTION f() RETURNS INT DETERMINISTIC BEGIN DECLARE x INT DEFAULT 1; RETURN x; END", "CREATE TRIGGER tr BEFORE INSERT ON t FOR EACH ROW BEGIN DECLARE x INT DEFAULT 1; SET NEW.id=x; END", "CREATE VIEW v AS SELECT 1 +", "LOAD DATA INFILE '/tmp/synthetic.csv' INTO TABLE t FIELDS TERMINATED BY ','", "LOAD XML INFILE '/tmp/synthetic.xml' INTO TABLE t"]
prefixes=['','# ordinary\n',"# unmatched single quote '\n",'# decoy CREATE VIEW\n','-- ordinary\n','-- ordinary\r','/* ordinary */\n','; ',"'decoy' ",'`decoy` ']
for bi,b in enumerate(bodies):
    for pi,p in enumerate(prefixes): cases.append((f'head:{bi}:{pi}',p+b))
for di,d in enumerate(["'u'@'h'",'`u`@`h`','CURRENT_USER','CURRENT_USER()']):
    for si,sep in enumerate(['',' ', '\n']):
        cases.append((f'definer:{di}:{si}',bodies[0].replace('CREATE PROCEDURE',f'CREATE DEFINER{sep}={sep}{d} PROCEDURE')))
for i,b in enumerate(["CREATE ALGORITHM=MERGE VIEW v AS SELECT 1 +", "CREATE SQL SECURITY INVOKER VIEW v AS SELECT 1 +", "CREATE ALGORITHM=UNDEFINED DEFINER=CURRENT_USER SQL SECURITY DEFINER VIEW v AS SELECT 1 +", "SELECT 1--1 AS x, LOAD DATA", "CREATE TABLE t(id INT, name INT COMMENT 'x') ENGINE=123 COMMENT='CREATE VIEW'"]): cases.append((f'other:{i}',b))
rows=[]
for cid,sql in cases:
    p=c.parser.parse(sql); r=c.audit_sql(sql,instance_type='distributed')
    rows.append({'id':cid,'sql':sql,'sql_type':p.sql_type,'parse_error':p.parse_error,'has_load_data':p.has_load_data,'fired':sorted({v.rule_id for v in r.violations}),'passed':r.passed})
(Path(a.out)).write_text(json.dumps({'version':sqlglot.__version__,'rows':rows},ensure_ascii=False,indent=2),encoding='utf-8')
print('HEAD_CASES',len(rows))
