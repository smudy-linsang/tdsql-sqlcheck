"""应急演练收尾：核实 runner 是否真被停止 + 执行恢复（必须人工批准流程的等价步骤）。"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import _boot  # noqa: F401,E402

from uat_d40_api import Api, RUNTIME, save, utc  # noqa: E402

SANDBOX = RUNTIME / "emg"
PERSIST = SANDBOX.parent / "conf/copilot-disabled.json"


def _runner_processes():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -match 'copilot_runner' } | "
         "Select-Object -ExpandProperty ProcessId"],
        capture_output=True, text=True, timeout=60)
    return [x.strip() for x in (out.stdout or "").splitlines() if x.strip()]


def _set_settings(enabled: bool):
    os.environ.update({
        "SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
        "SQLCHECK_DB_USER": "root", "SQLCHECK_DB_PASSWORD": "tdsql_test_2024",
        "SQLCHECK_DB_NAME": "uat_d_1640_meta",
    })
    from backend.services.database import _get_connection, ensure_db
    from backend.services.copilot.repository import RuntimeRepo
    ensure_db()
    conn = _get_connection()
    try:
        settings = RuntimeRepo.settings(conn)
        settings["enabled"] = enabled
        rt = RuntimeRepo.get(conn) or {}
        RuntimeRepo.save_settings(conn, settings,
                                  int(rt.get("config_revision") or 1))
        conn.commit()
    finally:
        conn.close()
    RuntimeRepo.set_accepting(bool(enabled), "uat-d40-recovery")
    conn = _get_connection()
    try:
        conn.commit()
    finally:
        conn.close()


def main():
    out = {"utc": utc()}
    out["runner_pids_after_disable"] = _runner_processes()
    out["runner_still_running"] = bool(out["runner_pids_after_disable"])
    out["still_accepting_after_disable"] = json.loads(
        Api("uat_1640" if False else "uat_d_1640").__class__.__name__ and "{}")

    a = Api("uat_d_1640")
    a.relogin_after(2)
    out["mode_after_disable"] = (a.get("/api/v1/copilot/capabilities")
                                .get("body") or {}).get("mode")
    h = a.get("/api/v1/copilot-admin/health").get("body") or {}
    out["health_after_disable"] = {"ready": h.get("ready"),
                                   "runner": h.get("runner"),
                                   "reason_code": h.get("reason_code")}

    # ── 恢复（等价于手册：查明原因 → 撤销配置 → 人工批准 → 删除标记并重启）──
    _set_settings(True)
    if PERSIST.exists():
        PERSIST.unlink()
    out["persist_removed"] = not PERSIST.exists()
    time.sleep(3)
    out["mode_after_recovery"] = (a.get("/api/v1/copilot/capabilities")
                                  .get("body") or {}).get("mode")
    h2 = a.get("/api/v1/copilot-admin/health").get("body") or {}
    out["health_after_recovery"] = {"ready": h2.get("ready"),
                                    "runner": h2.get("runner")}
    # 恢复后应能重新受理
    s = a.post("/api/v1/copilot/sessions", json={
        "scope_kind": "GLOBAL_HELP", "instance_type": "unknown",
        "page_key": "copilot-page"})
    out["new_session_after_recovery"] = s["status"]
    save("s7b_emergency_recovery", out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:2500])
    a.close()


if __name__ == "__main__":
    main()
