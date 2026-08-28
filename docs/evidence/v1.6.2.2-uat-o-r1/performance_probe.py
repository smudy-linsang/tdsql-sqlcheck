"""Small local CPU benchmark, not a production capacity certification."""
import json
import logging
import statistics
import sys
import time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
logging.getLogger('sqlglot').setLevel(logging.ERROR)
from backend.engine.checker import RuleChecker
c=RuleChecker()
rows=[]
for n in [20,100,500]:
    columns=','.join(f'c{i} INT NOT NULL COMMENT \'c\'' for i in range(n))
    sql=f"CREATE TABLE t_perf(id BIGINT NOT NULL COMMENT 'id',{columns},PRIMARY KEY(id),UNIQUE KEY uk_c(c0)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='uat' shardkey=id;"
    for _ in range(3): c.audit_sql(sql)
    timings=[]
    for _ in range(15):
        t=time.perf_counter()
        c.audit_sql(sql)
        timings.append((time.perf_counter()-t)*1000)
    rows.append({'columns':n+1,'sql_chars':len(sql),'median_ms':round(statistics.median(timings),3),'max_ms':round(max(timings),3),'iterations':15})
Path(sys.argv[2]).write_text(json.dumps(rows,indent=2),encoding='utf-8')
print(json.dumps(rows))
