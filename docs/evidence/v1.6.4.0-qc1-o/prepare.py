"""QC1 fixture: isolated synthetic databases and process-local test CA only.

Reuses D's committed fixture builder after changing every D resource namespace.
Does not edit product code, D's databases, OS trust or installed certifi bundle.
"""
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
RT = ROOT / 'data/reports/qc_o_1640'
RT.mkdir(parents=True, exist_ok=True)
D = ROOT / 'docs/evidence/v1.6.4.0-uat-d'
sys.path[:0] = [str(ROOT), str(D)]
import _boot  # noqa
import certifi

original_certifi = Path(certifi.where())
trust = RT / 'trust/certifi'
if not trust.exists():
    shutil.copytree(original_certifi.parent, trust, ignore=shutil.ignore_patterns('__pycache__'))

source = (D / 'prepare_uat_d40.py').read_text(encoding='utf-8')
source = source.replace('uat_d_1640', 'qc_o_1640').replace('d40-dist', 'q40-dist').replace('d40-cent', 'q40-cent')
source = source.replace('D40-', 'QC1-').replace('D-UAT40', 'O-QC1')
scope = {'__name__': 'qc_fixture', '__file__': str(D / 'prepare_uat_d40.py')}
exec(compile(source, str(D / 'prepare_uat_d40.py'), 'exec'), scope)
for e, port in zip(scope['ENDPOINTS']['endpoints'], (8453, 8454, 8455)):
    e['port'] = port
scope['ACCOUNTS']['qc_o_1640dev2'] = ('QC1 Developer Two', 'developer')
scope['PW_FILE'].write_text(json.dumps({u: 'Test@2026Admin' for u in scope['ACCOUNTS']}), encoding='utf-8')

# cmd_certs writes only the copied trust bundle in this short-lived process.
before = original_certifi.read_bytes()
certifi.where = lambda: str(trust / 'cacert.pem')
scope['cmd_certs']()
assert original_certifi.read_bytes() == before
scope['cmd_db']()
scope['cmd_config']()

import pymysql
with pymysql.connect(host='127.0.0.1', port=13306, user='root', password='tdsql_test_2024') as db:
    with db.cursor() as cur:
        for name in ('qc_o_1640_regression', 'qc_o_1640_baseline'):
            cur.execute('CREATE DATABASE IF NOT EXISTS ' + name + ' DEFAULT CHARSET utf8mb4')
    db.commit()
print('QC fixture ready; installed certifi unchanged; no production data used')
