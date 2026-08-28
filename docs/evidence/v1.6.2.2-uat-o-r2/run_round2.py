"""Isolated round-two runners; preserve the signed round-one evidence."""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
R1 = HERE.parent / 'v1.6.2.2-uat-o-r1'


def run(args, log, env=None):
    with (HERE / log).open('w', encoding='utf-8', newline='\n') as out:
        result = subprocess.run(args, cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT, text=True)
    print(log, 'EXIT', result.returncode, flush=True)
    return result.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['full', 'matrix', 'compare'])
    a = ap.parse_args()
    # Follow tests/conftest.py: legacy tests do not carry tokens; security tests
    # turn authentication on themselves. The live browser server stays enabled.
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', AUTH_ENABLED='false', SCHEDULER_ENABLED='false')
    if a.mode in ('full', 'matrix'):
        env['SQLCHECK_DB_NAME'] = 'tdsql_uat_o_reg_r2_' + a.mode + '_20260828'
        if run([sys.executable, str(R1 / 'prepare_regression.py')], a.mode + '_prepare.txt', env):
            raise SystemExit('Fixture preparation failed')
        cmd = ([sys.executable, '-m', 'pytest', 'tests', '-q', '--junitxml=' + str(HERE / 'full_regression.xml')]
               if a.mode == 'full' else
               [sys.executable, '-u', 'docs/evidence/v1.6.2.2/run_all.py', '--mode', 'implementation', '--matrix', '--keep'])
        raise SystemExit(run(cmd, 'full_regression.txt' if a.mode == 'full' else 'implementation_matrix.txt', env))
    temp = Path(tempfile.mkdtemp(prefix='uat-o-r2-before-'))
    with zipfile.ZipFile(io.BytesIO(subprocess.check_output(['git','archive','--format=zip','6957499'], cwd=ROOT))) as z:
        z.extractall(temp)
    code = run([sys.executable, str(HERE / 'edge_probe.py'), '--repo', str(temp), '--out', str(HERE / 'edge_baseline.json')], 'edge_baseline.txt', env)
    if code:
        raise SystemExit(code)
    before = json.loads((R1 / 'rule_probe_current.json').read_text(encoding='utf-8'))
    after = json.loads((HERE / 'rule_probe_current.json').read_text(encoding='utf-8'))
    old = {r['id']:r for r in before['rows']}
    changes = [{'id':r['id'], 'before':old[r['id']]['fired'], 'after':r['fired']} for r in after['rows'] if r['fired'] != old[r['id']]['fired']]
    eb = json.loads((HERE / 'edge_baseline.json').read_text(encoding='utf-8'))
    ec = json.loads((HERE / 'edge_current.json').read_text(encoding='utf-8'))
    ebm = {r['id']:r for r in eb['rows']}
    edge_changes = [{'id':r['id'], 'sql':r['sql'], 'before':ebm[r['id']]['fired'], 'after':r['fired'], 'sql_type':r['sql_type'], 'parse_error':r['parse_error']} for r in ec['rows'] if r['fired'] != ebm[r['id']]['fired']]
    obj = {'before_commit':'6957499', 'tested_commit':subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(), 'baseline_temp':str(temp), 'rules_equal':before['rules']==after['rules'], 'case_count':1000, 'changes':changes, 'remaining_failures':after['failures'], 'edge_changes':edge_changes}
    (HERE / 'round2_diff.json').write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    print('CORPUS_CHANGED', len(changes), 'EDGE_CHANGED',len(edge_changes), 'BASELINE',temp, flush=True)


if __name__ == '__main__':
    main()
