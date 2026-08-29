"""Run the full suite while port 8000 is owned by an unrelated HTTP service."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PREPARE = HERE.parent / "v1.6.2.2-uat-o-r1" / "prepare_regression.py"
env = dict(
    os.environ,
    PYTHONUTF8="1",
    PYTHONIOENCODING="utf-8",
    AUTH_ENABLED="false",
    SCHEDULER_ENABLED="false",
    SQLCHECK_DB_NAME="tdsql_uat_o_reg_r7_occupied_20260829",
)
env.pop("SCHEMA_CHECKSUM_RECONCILE", None)
prep = subprocess.run(
    [sys.executable, str(PREPARE)], cwd=ROOT, env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
if prep.returncode:
    raise SystemExit(prep.returncode)

creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
dummy = subprocess.Popen(
    [sys.executable, "-m", "http.server", "8000", "--bind", "127.0.0.1"],
    cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    creationflags=creationflags,
)
try:
    for _ in range(50):
        if dummy.poll() is not None:
            raise RuntimeError(f"dummy service exited early: {dummy.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", 8000), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("dummy service did not bind port 8000")

    path = HERE / "full_regression_occupied_port.txt"
    with path.open("w", encoding="utf-8", newline="\n") as output:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q"],
            cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
    raw = path.read_text(encoding="utf-8")
    path.write_text(
        "\n".join(line.rstrip() for line in raw.splitlines()) + "\n",
        encoding="utf-8", newline="\n",
    )
finally:
    dummy.terminate()
    try:
        dummy.wait(timeout=10)
    except subprocess.TimeoutExpired:
        dummy.kill()
        dummy.wait(timeout=5)

print("OCCUPIED_PORT_FULL_EXIT", result.returncode)
raise SystemExit(result.returncode)
