"""Round-three independent probes; no application changes or SQL execution."""
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', AUTH_ENABLED='false', SCHEDULER_ENABLED='false')
for script, name in [('v1.6.2.2-uat-o-r1/rule_probe.py', 'rule_probe_current'), ('v1.6.2.2-uat-o-r2/edge_probe.py', 'edge_current')]:
    with (HERE / (name + '.txt')).open('w', encoding='utf-8') as log:
        r = subprocess.run([sys.executable, str(HERE.parent / script), '--repo', str(ROOT), '--out', str(HERE / (name + '.json'))], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    print(name, r.returncode, flush=True)
    if r.returncode: raise SystemExit(r.returncode)
subprocess.run([sys.executable, str(HERE / 'run_round3.py'), 'compare'], cwd=ROOT, env=env, check=True)
