"""Run the formal implementation gate with no ambient HTTP service."""
import os
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PREPARE = HERE.parent / "v1.6.2.2-uat-o-r1" / "prepare_regression.py"
env = dict(
    os.environ,
    PYTHONUTF8="1",
    PYTHONIOENCODING="utf-8",
    AUTH_ENABLED="false",
    SCHEDULER_ENABLED="false",
    SQLCHECK_DB_NAME="tdsql_uat_o_reg_r7_gate_20260829",
)
env.pop("SCHEMA_CHECKSUM_RECONCILE", None)
prep = subprocess.run(
    [sys.executable, str(PREPARE)], cwd=ROOT, env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
if prep.returncode:
    raise SystemExit(prep.returncode)

path = HERE / "implementation_gate.txt"
with path.open("w", encoding="utf-8", newline="\n") as output:
    result = subprocess.run(
        [sys.executable, "docs/evidence/v1.6.2.2/run_all.py",
         "--mode", "implementation", "--matrix"],
        cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
raw = path.read_text(encoding="utf-8")
path.write_text(
    "\n".join(line.rstrip() for line in raw.splitlines()) + "\n",
    encoding="utf-8", newline="\n",
)
print("IMPLEMENTATION_GATE_EXIT", result.returncode)
raise SystemExit(result.returncode)
