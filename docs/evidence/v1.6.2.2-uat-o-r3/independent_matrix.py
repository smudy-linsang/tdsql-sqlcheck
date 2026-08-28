"""Run actual current code despite historical design-hash gate; never alter the gate."""
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CACHE = Path('C:/Users/linsa/AppData/Local/Temp/v1622-revq-evidence-h3fdnkee')
env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', AUTH_ENABLED='false', SCHEDULER_ENABLED='false', SQLCHECK_DB_NAME='tdsql_uat_o_reg_r3_matrix_20260828')
results=[]
for v in ('29.0.0', '30.14.0', '30.17.0'):
    py=CACHE / ('venv_'+v.replace('.','_')) / 'Scripts/python.exe'
    actual=subprocess.check_output([str(py), '-c', 'import sqlglot; print(sqlglot.__version__)'],text=True).strip()
    assert actual==v, (actual,v)
    jobs=[('manifest',['-m','pytest','-q','docs/evidence/v1.6.2.2/test_parser_recovery_manifest.py','tests/test_kfn_fail_closed.py']),('edge',[str(HERE.parent/'v1.6.2.2-uat-o-r2/edge_probe.py'),'--repo',str(ROOT),'--out',str(HERE/('edge_'+v+'.json'))])]
    for label,args in jobs:
        with (HERE/(label+'_'+v+'.txt')).open('w',encoding='utf-8') as log:
            r=subprocess.run([str(py),*args],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append({'version':v,'actual':actual,'job':label,'exit':r.returncode})
        print(v,label,r.returncode,flush=True)
(HERE/'independent_matrix.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
