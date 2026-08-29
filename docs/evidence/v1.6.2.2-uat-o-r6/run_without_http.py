"""Reproduce the clean-environment full-suite result without an ambient port-8000 app."""
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
    SQLCHECK_DB_NAME="tdsql_uat_o_reg_r6_no_http_20260829",
)

prep = subprocess.run(
    [sys.executable, str(PREPARE)], cwd=ROOT, env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
if prep.returncode:
    raise SystemExit(prep.returncode)

log_path = HERE / "full_regression_without_service.txt"
with log_path.open(
    "w", encoding="utf-8", newline="\n"
) as output:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )

# Keep the committed evidence diff-clean even when third-party tracebacks emit
# whitespace-only indentation lines.
raw = log_path.read_text(encoding="utf-8")
log_path.write_text(
    "\n".join(line.rstrip() for line in raw.splitlines()) + "\n",
    encoding="utf-8", newline="\n",
)

print("FULL_WITHOUT_HTTP_EXIT", result.returncode)
raise SystemExit(result.returncode)
