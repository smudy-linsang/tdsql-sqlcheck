"""N-02 公平复测：把 TDSQL_SQLCHECK_DIR 同时给 Web 进程，验证持久标记是否强制不就绪。

（首测不公平：只给应急脚本设了该变量，Web 仍走默认 /opt/tdsql-sqlcheck。）
本脚本自己起一个带该变量的 Web 进程，验证后清理。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
ROOT = HERE.parents[2]
sys.path.insert(0, str(R1))
sys.path.insert(0, str(ROOT))
import _boot  # noqa: F401,E402

from uat_d40_api import Api, RUNTIME, utc  # noqa: E402

SANDBOX = RUNTIME / "emg"
PERSIST = SANDBOX.parent / "conf/copilot-disabled.json"
PY = r"C:\Python314\python.exe"
USERSITE = r"C:\Users\linsa\AppData\Roaming\Python\Python314\site-packages"


def _kill_web():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-NetTCPConnection -State Listen -LocalPort 8025 "
                    "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty "
                    "OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }"],
                   capture_output=True, timeout=60)
    time.sleep(2)


def _start_web(extra_env: dict, tag: str):
    bat = RUNTIME / f"web_{tag}.cmd"
    env = {
        "SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
        "SQLCHECK_DB_USER": "root", "SQLCHECK_DB_PASSWORD": "tdsql_test_2024",
        "SQLCHECK_DB_NAME": "uat_d_1640_meta", "AUTH_ENABLED": "true",
        "SCHEDULER_ENABLED": "false", "DATA_MASKING_ENABLED": "false",
        "REPORT_OUTPUT_DIR": str(RUNTIME / "reports"),
        "COPILOT_ENABLED": "true", "COPILOT_ALLOW_SCHEMA_IDENTIFIERS": "true",
        "COPILOT_KEYRING_FILE": str(RUNTIME / "copilot-keyring.json"),
        "COPILOT_ENDPOINTS_FILE": str(RUNTIME / "copilot-endpoints.json"),
        "PYTHONPATH": f"{ROOT};{USERSITE}", "PYTHONIOENCODING": "utf-8",
    }
    env.update(extra_env)
    lines = ["@echo off", f"cd /d {ROOT}"] + \
            [f"set {k}={v}" for k, v in env.items()] + \
            [f'"{PY}" -m uvicorn backend.main:app --host 127.0.0.1 --port 8025 '
             f'--log-level info > "{RUNTIME / f"web_{tag}.log"}" '
             f'2> "{RUNTIME / f"web_{tag}.err.log"}"']
    bat.write_text("\r\n".join(lines), encoding="oem")
    subprocess.Popen(["cmd.exe", "/c", str(bat)], cwd=str(ROOT),
                     creationflags=0x00000008)  # DETACHED_PROCESS
    for _ in range(45):
        time.sleep(1)
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "(Invoke-WebRequest 'http://127.0.0.1:8025/health' "
                                "-UseBasicParsing -TimeoutSec 3).StatusCode"],
                               capture_output=True, text=True, timeout=20)
            if "200" in (r.stdout or ""):
                return True
        except Exception:
            pass
    return False


def _mode():
    for _ in range(20):
        try:
            a = Api("uat_d_1640")
            a.relogin_after(2)
            body = a.get("/api/v1/copilot/capabilities").get("body") or {}
            m = body.get("mode")
            a.close()
            return m
        except Exception:
            time.sleep(2)
    return None


def _set_enabled(flag: bool):
    os.environ.update({
        "SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
        "SQLCHECK_DB_USER": "root", "SQLCHECK_DB_PASSWORD": "tdsql_test_2024",
        "SQLCHECK_DB_NAME": "uat_d_1640_meta",
    })
    for m in ("backend.services.database", "backend.services.copilot.repository"):
        sys.modules.pop(m, None)
    from backend.services.database import _get_connection, ensure_db
    from backend.services.copilot.repository import RuntimeRepo
    ensure_db()
    conn = _get_connection()
    try:
        s = RuntimeRepo.settings(conn)
        s["enabled"] = flag
        rt = RuntimeRepo.get(conn) or {}
        RuntimeRepo.save_settings(conn, s, int(rt.get("config_revision") or 1))
        conn.commit()
    finally:
        conn.close()
    RuntimeRepo.set_accepting(flag, "uat2")
    conn = _get_connection()
    try:
        conn.commit()
    finally:
        conn.close()


def main():
    out = {"utc": utc()}
    sandbox_sh = "/" + str(SANDBOX).replace("\\", "/").replace(":", "", 1)
    out["sandbox"] = sandbox_sh
    out["persist_path"] = str(PERSIST)

    # 准备：标记存在 + DB 打开
    PERSIST.parent.mkdir(parents=True, exist_ok=True)
    PERSIST.write_text(json.dumps({"incident_id": "UATD40R2-N02",
                                   "disabled_at": "2026-09-15T00:00:00Z"}) + "\n",
                       encoding="utf-8")
    _set_enabled(True)
    out["marker_exists"] = PERSIST.exists()

    _kill_web()
    out["web_started"] = _start_web({"TDSQL_SQLCHECK_DIR": str(SANDBOX)}, "n02on")
    out["mode_with_marker"] = _mode()

    # 对照：删标记后同一环境应恢复
    PERSIST.unlink(missing_ok=True)
    _kill_web()
    out["web_started_2"] = _start_web({"TDSQL_SQLCHECK_DIR": str(SANDBOX)}, "n02off")
    out["mode_without_marker"] = _mode()

    # 还原成常规夹具（无该变量）
    _kill_web()
    out["web_restored"] = _start_web({}, "normal")
    out["mode_restored"] = _mode()

    (HERE / "r2_n02.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:1600])


if __name__ == "__main__":
    main()
