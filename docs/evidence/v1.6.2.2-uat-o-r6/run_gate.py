"""Run the formal implementation gate with its required local HTTP fixture."""
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
R1 = HERE.parent / "v1.6.2.2-uat-o-r1"
sys.path.insert(0, str(HERE))
from run_round6 import start_test_server, stop_test_server

env = dict(
    os.environ,
    PYTHONUTF8="1",
    PYTHONIOENCODING="utf-8",
    AUTH_ENABLED="false",
    SCHEDULER_ENABLED="false",
    SQLCHECK_DB_NAME="tdsql_uat_o_reg_r6_gate_20260829",
)
prep = subprocess.run(
    [sys.executable, str(R1 / "prepare_regression.py")], cwd=ROOT, env=env,
    capture_output=True, text=True, encoding="utf-8", errors="replace",
)
if prep.returncode:
    raise SystemExit(prep.returncode)

server = start_test_server(env)
try:
    with (HERE / "implementation_gate.txt").open("w", encoding="utf-8", newline="\n") as out:
        result = subprocess.run(
            [sys.executable, "docs/evidence/v1.6.2.2/run_all.py",
             "--mode", "implementation", "--matrix"],
            cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace",
        )
finally:
    stop_test_server(server)
print("IMPLEMENTATION_GATE_EXIT", result.returncode)
raise SystemExit(result.returncode)
