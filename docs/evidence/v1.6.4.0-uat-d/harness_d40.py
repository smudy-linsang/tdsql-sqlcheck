"""智能体D / v1.6.4.0 UAT 进程夹具：web / runner / gw。

用法（仓库根目录）：
  python docs/evidence/v1.6.4.0-uat-d/harness_d40.py web
  python docs/evidence/v1.6.4.0-uat-d/harness_d40.py runner
  python docs/evidence/v1.6.4.0-uat-d/harness_d40.py gw [8443 8444 8445]
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNTIME = ROOT / "data/reports/uat_d_1640"
WEB_PORT = 8025

sys.path.insert(0, str(HERE))
import _boot  # noqa: F401,E402  （补入用户级 site-packages）

os.environ.update({
    "SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
    "SQLCHECK_DB_USER": "root", "SQLCHECK_DB_PASSWORD": "tdsql_test_2024",
    "SQLCHECK_DB_NAME": "uat_d_1640_meta",
    "AUTH_ENABLED": "true", "SCHEDULER_ENABLED": "false",
    "DATA_MASKING_ENABLED": "false",
    "REPORT_OUTPUT_DIR": str(RUNTIME / "reports"),
    "COPILOT_ENABLED": os.getenv("COPILOT_ENABLED", "true"),
    "COPILOT_ALLOW_SCHEMA_IDENTIFIERS": os.getenv(
        "COPILOT_ALLOW_SCHEMA_IDENTIFIERS", "true"),
    "COPILOT_KEYRING_FILE": str(RUNTIME / "copilot-keyring.json"),
    "COPILOT_ENDPOINTS_FILE": str(RUNTIME / "copilot-endpoints.json"),
    "PYTHONPATH": os.pathsep.join([str(ROOT)] + [str(p) for p in _boot.CANDIDATES]),
    "UAT_D40_RUNTIME": str(RUNTIME),
    "PYTHONIOENCODING": "utf-8",
})
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "help"
    if mode == "web":
        import uvicorn
        uvicorn.run("backend.main:app", host="127.0.0.1", port=WEB_PORT,
                    log_level="info")
    elif mode == "runner":
        from backend.workers.copilot_runner import main as runner_main
        runner_main()
    elif mode == "gw":
        sys.argv = [sys.argv[0]] + (sys.argv[2:] or ["8443", "8444", "8445"])
        exec((HERE / "mock_llm_gateway.py").read_text(encoding="utf-8"),
             {"__name__": "__main__", "__file__": str(HERE / "mock_llm_gateway.py")})
    else:
        print(__doc__)
