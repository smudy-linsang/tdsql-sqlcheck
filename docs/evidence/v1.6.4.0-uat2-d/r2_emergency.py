"""智能体D / v1.6.4.0 第二轮 UAT：M05 应急脚本复演 + N02 持久停用标记。

步骤：
  1. 起沙箱 + venv 垫片，真实执行 deploy/copilot_emergency_disable.sh
  2. 判定：无 systemd/pkill 平台应 exit 3、事件日志 runner_stopped=manual-required、
     runner 进程仍在（不谎报成功）
  3. N02：在标记存在的前提下把 DB 设置改回 enabled=true，重启 Web →
     bootstrap 必须强制不就绪（DENY 自动恢复）
  4. 删除标记 + 重启 → 恢复 READY
"""
import json
import os
import shutil
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

BASH = r"C:\Program Files\Git\bin\bash.exe"
SANDBOX = RUNTIME / "emg"
PERSIST = SANDBOX.parent / "conf/copilot-disabled.json"
INCIDENT = "UATD40R2-EMG-001"


def _sandbox():
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX, ignore_errors=True)
    (SANDBOX / "current/venv/bin").mkdir(parents=True)
    (SANDBOX / "logs").mkdir(parents=True)
    (SANDBOX.parent / "conf").mkdir(parents=True, exist_ok=True)
    py = SANDBOX / "current/venv/bin/python"
    repo = str(ROOT).replace("\\", "/")
    key = str(RUNTIME / "copilot-keyring.json").replace("\\", "/")
    eps = str(RUNTIME / "copilot-endpoints.json").replace("\\", "/")
    py.write_text(
        "#!/usr/bin/env bash\n"
        f'cd "{repo}"\n'
        "export SQLCHECK_DB_HOST=127.0.0.1\nexport SQLCHECK_DB_PORT=13306\n"
        "export SQLCHECK_DB_USER=root\nexport SQLCHECK_DB_PASSWORD=tdsql_test_2024\n"
        "export SQLCHECK_DB_NAME=uat_d_1640_meta\nexport AUTH_ENABLED=true\n"
        f'export COPILOT_KEYRING_FILE="{key}"\n'
        f'export COPILOT_ENDPOINTS_FILE="{eps}"\n'
        "export PYTHONIOENCODING=utf-8\n"
        f'export PYTHONPATH="{repo};C:/Users/linsa/AppData/Roaming/Python/Python314/site-packages"\n'
        'exec "C:/Python314/python.exe" "$@"\n',
        encoding="utf-8", newline="\n")
    py.chmod(0o755)


def _run_script():
    env = dict(os.environ)
    env["TDSQL_SQLCHECK_DIR"] = "/" + str(SANDBOX).replace("\\", "/").replace(":", "", 1)
    p = subprocess.run([BASH, str(ROOT / "deploy/copilot_emergency_disable.sh"),
                        "--incident-id", INCIDENT],
                       capture_output=True, env=env, timeout=180, cwd=str(ROOT))
    return {"rc": p.returncode,
            "stdout": (p.stdout or b"").decode("utf-8", "replace")[-2000:],
            "stderr": (p.stderr or b"").decode("utf-8", "replace")[-800:]}


def _runner_pids():
    o = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                        "Where-Object { $_.CommandLine -match 'copilot_runner' } | "
                        "Select-Object -ExpandProperty ProcessId"],
                       capture_output=True, text=True, timeout=60)
    return [x.strip() for x in (o.stdout or "").splitlines() if x.strip()]


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


def _restart_web():
    subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File",
                    str(R1 / "restart_web.ps1")], capture_output=True,
                   timeout=180, cwd=str(ROOT))
    time.sleep(4)


def _login_retry():
    for _ in range(25):
        try:
            a = Api("uat_d_1640")
            a.relogin_after(2)
            return a
        except Exception:
            time.sleep(2)
    return None


def main():
    out = {"utc": utc()}
    _sandbox()
    a = _login_retry()
    out["before_mode"] = (a.get("/api/v1/copilot/capabilities").get("body")
                          or {}).get("mode") if a else None

    out["m05_script"] = _run_script()
    time.sleep(3)
    out["m05_persist_exists"] = PERSIST.exists()
    if PERSIST.exists():
        out["m05_persist"] = PERSIST.read_text(encoding="utf-8")[:300]
    ev = SANDBOX / "logs/copilot-emergency.log"
    out["m05_event_log"] = ev.read_text(encoding="utf-8")[:300] if ev.exists() else None
    out["m05_runner_pids"] = _runner_pids()
    out["m05_runner_still_running"] = bool(out["m05_runner_pids"])
    out["m05_rc_is_3"] = out["m05_script"]["rc"] == 3
    out["m05_claims_success"] = "Copilot 已停用" in out["m05_script"]["stdout"]

    # ── N02：标记在 → 即便 DB 被改回 enabled=true，重启后仍须不就绪 ──
    a = _login_retry()
    out["after_disable_mode"] = (a.get("/api/v1/copilot/capabilities").get("body")
                                 or {}).get("mode") if a else None
    _set_enabled(True)          # 模拟"有人手工把开关打开了"
    _restart_web()
    a = _login_retry()
    out["n02_db_enabled_true"] = True
    out["n02_mode_after_restart_with_marker"] = (
        a.get("/api/v1/copilot/capabilities").get("body") or {}).get("mode") \
        if a else None
    h = (a.get("/api/v1/copilot-admin/health").get("body") or {}) if a else {}
    out["n02_health"] = {"ready": h.get("ready"),
                         "runner": h.get("runner")}

    # ── 恢复：删标记 + 重启 ──
    if PERSIST.exists():
        PERSIST.unlink()
    _set_enabled(True)
    _restart_web()
    a = _login_retry()
    out["recovered_mode"] = (a.get("/api/v1/copilot/capabilities").get("body")
                             or {}).get("mode") if a else None
    (HERE / "r2_emergency.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "m05_script"},
                     ensure_ascii=False, indent=2, default=str)[:2600])
    print("--- script stdout ---")
    print(out["m05_script"]["stdout"][-1200:])


if __name__ == "__main__":
    main()
