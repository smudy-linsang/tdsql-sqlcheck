"""Controlled regression runs in separate disposable QC databases, sequentially.

Stop only the owned QC copilot runner first: MySQL named runner locks are
server-wide even when the application databases differ.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
RT = ROOT / 'data/reports/qc_o_1640'
EVID = Path(__file__).resolve().parent
import pymysql

def run(label, code, db, args):
    with pymysql.connect(host='127.0.0.1',port=13306,user='root',password='tdsql_test_2024') as c:
        with c.cursor() as q:q.execute('CREATE DATABASE IF NOT EXISTS '+db+' DEFAULT CHARSET utf8mb4')
        c.commit()
    env=os.environ.copy()
    env.update({'SQLCHECK_DB_HOST':'127.0.0.1','SQLCHECK_DB_PORT':'13306',
        'SQLCHECK_DB_USER':'root','SQLCHECK_DB_PASSWORD':'tdsql_test_2024','SQLCHECK_DB_NAME':db,
        'AUTH_ENABLED':'false','DATA_MASKING_ENABLED':'false','SCHEDULER_ENABLED':'false',
        'G14_ALLOW_DESTRUCTIVE_TESTS':'1','G14_TEST_DB_NAME':db,
        'PYTHONIOENCODING':'utf-8',
        'PYTHONPATH':str(code)+os.pathsep+r'C:\Users\linsa\AppData\Roaming\Python\Python314\site-packages',
        'REPORT_OUTPUT_DIR':str(RT/(label+'-reports'))})
    xml=RT/(label+'.xml')
    cmd=[sys.executable,'-m','pytest',*args,'-q','--tb=short',
         '--basetemp='+str(RT/(label+'-temp')),'--junitxml='+str(xml)]
    with (RT/(label+'.log')).open('w',encoding='utf-8') as out:
        rc=subprocess.run(cmd,cwd=code,env=env,stdout=out,stderr=subprocess.STDOUT).returncode
    print(label,'exit',rc,flush=True)
    if xml.exists():
        root=ET.parse(xml).getroot(); cases=[]
        for t in root.iter('testcase'):
            child=t.find('failure')
            if child is None:child=t.find('error')
            status='failure' if child is not None else ('skipped' if t.find('skipped') is not None else 'passed')
            cases.append({'id':t.get('classname','')+'::'+t.get('name',''),'status':status,
                'message':child.get('message','')[:800] if child is not None else ''})
        result={'label':label,'command':cmd,'code_path':str(code),'database':db,'exit_code':rc,
            'suite':root[0].attrib,'cases':cases}
        (EVID/(label+'-summary.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result['suite']),flush=True)

if __name__=='__main__':
    run('copilot-focused',ROOT,'qc_o_1640_focus',['tests/copilot'])
    run('head-original-regression',ROOT,'qc_o_1640_head_compare',['tests','--ignore=tests/copilot'])
    run('base-original-regression',RT/'baseline-code','qc_o_1640_base_compare',['tests'])
