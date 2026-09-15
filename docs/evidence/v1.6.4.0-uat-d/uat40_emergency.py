"""智能体D / v1.6.4.0 UAT：GATE §5 第 4 条 —— copilot_emergency_disable.sh 实机演练。

本机无 systemd、无 Linux venv，故：
  · 用 Git Bash 真实执行 `deploy/copilot_emergency_disable.sh --incident-id ...`；
  · 在沙箱 INSTALL_DIR 下放置 `current/venv/bin/python` 垫片（转发到 Windows
    python + UAT 环境变量），使脚本的 **DB 侧停用分支真实执行**（不是跳过）；
  · pkill/systemctl 分支按平台实际结果记录，不假装成功。

验收点（GATE 要求）：
  1) 摘掉后主产品审核全流程仍可用（即时审核 + 在线元数据审核）
  2) 停用后 Copilot 不再新受理（DB settings.enabled=false / accepting=false）
  3) 删除持久标记并恢复设置后，Copilot 能回到 READY
"""
import json
import os
import shutil
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

BASH = r"C:\Program Files\Git\bin\bash.exe"
SANDBOX = RUNTIME / "emg"
INCIDENT = "UATD40-EMG-001"


def _prepare_sandbox():
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
        "export SQLCHECK_DB_HOST=127.0.0.1\n"
        "export SQLCHECK_DB_PORT=13306\n"
        "export SQLCHECK_DB_USER=root\n"
        "export SQLCHECK_DB_PASSWORD=tdsql_test_2024\n"
        "export SQLCHECK_DB_NAME=uat_d_1640_meta\n"
        "export AUTH_ENABLED=true\n"
        f'export COPILOT_KEYRING_FILE="{key}"\n'
        f'export COPILOT_ENDPOINTS_FILE="{eps}"\n'
        'export PYTHONIOENCODING=utf-8\n'
        f'export PYTHONPATH="{repo};C:/Users/linsa/AppData/Roaming/Python/Python314/site-packages"\n'
        'exec "C:/Python314/python.exe" "$@"\n',
        encoding="utf-8", newline="\n")
    py.chmod(0o755)
    return SANDBOX


def _run_script():
    env = dict(os.environ)
    env["TDSQL_SQLCHECK_DIR"] = str(SANDBOX).replace("\\", "/")
    env["TDSQL_SQLCHECK_DIR"] = "/" + env["TDSQL_SQLCHECK_DIR"].replace(":", "").replace("\\", "/")
    # Git Bash 路径形式：/c/TDSQL_SQLCHECK/...
    env["TDSQL_SQLCHECK_DIR"] = "/" + str(SANDBOX).replace("\\", "/").replace(":", "", 1)
    script = str(ROOT / "deploy/copilot_emergency_disable.sh")
    p = subprocess.run([BASH, script, "--incident-id", INCIDENT],
                       capture_output=True, env=env, timeout=120,
                       cwd=str(ROOT))
    stdout = (p.stdout or b"").decode("utf-8", errors="replace")
    stderr = (p.stderr or b"").decode("utf-8", errors="replace")
    return {"rc": p.returncode, "stdout": stdout[-2500:],
            "stderr": stderr[-1500:], "install_dir": env["TDSQL_SQLCHECK_DIR"]}


def _copilot_flags():
    import os as _os
    _os.environ.update({
        "SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
        "SQLCHECK_DB_USER": "root", "SQLCHECK_DB_PASSWORD": "tdsql_test_2024",
        "SQLCHECK_DB_NAME": "uat_d_1640_meta",
    })
    from backend.services.database import _get_connection, ensure_db
    from backend.services.copilot.repository import RuntimeRepo
    ensure_db()
    conn = _get_connection()
    try:
        rt = RuntimeRepo.get(conn) or {}
        return {"settings": RuntimeRepo.settings(conn),
                "accepting": int(rt.get("accepting") or 0),
                "module_schema_state": rt.get("module_schema_state")}
    finally:
        conn.close()


def main():
    out = {"utc": utc(), "incident_id": INCIDENT}
    _prepare_sandbox()
    a = Api("uat_d_1640")
    a.relogin_after(2)
    out["before"] = {"flags": _copilot_flags(),
                     "capabilities": (a.get("/api/v1/copilot/capabilities")
                                      .get("body") or {}).get("mode")}

    out["script_run"] = _run_script()
    time.sleep(3)
    out["after_flags"] = _copilot_flags()
    persist = SANDBOX.parent / "conf/copilot-disabled.json"
    out["persist_file_exists"] = persist.exists()
    if persist.exists():
        out["persist_file_content"] = persist.read_text(encoding="utf-8")[:300]
    evlog = SANDBOX / "logs/copilot-emergency.log"
    out["event_log_exists"] = evlog.exists()
    if evlog.exists():
        out["event_log"] = evlog.read_text(encoding="utf-8")[:300]

    # 主产品是否仍可用：即时审核 + 健康检查
    out["main_instant_audit"] = None
    for body in ({"sql_text": "SELECT id FROM d40_tab_00 WHERE id = 1"},
                 {"sql": "SELECT id FROM d40_tab_00 WHERE id = 1"}):
        r = a.post("/api/v1/audit/sql", json=body)
        if r["status"] == 200:
            out["main_instant_audit"] = {
                "status": 200, "keys": sorted((r.get("body") or {}).keys()),
                "summary": {k: (r["body"] or {}).get(k) for k in
                            ("total", "passed", "violation_count", "summary")}}
            break
        out.setdefault("main_instant_audit_attempts", []).append(
            {"status": r["status"], "detail": str((r.get("body") or {}).get("detail"))[:160]})
    out["main_health"] = a.get("/health")
    out["after_capabilities"] = a.get("/api/v1/copilot/capabilities")

    # 停用后 Copilot 新受理应被拒
    s = a.post("/api/v1/copilot/sessions", json={
        "scope_kind": "GLOBAL_HELP", "instance_type": "unknown",
        "page_key": "copilot-page"})
    out["new_session_after_disable"] = {
        "status": s["status"],
        "detail": (s.get("body") or {}).get("detail")}
    out["help_after_disable"] = {
        "status": a.get("/api/v1/copilot/help").get("status")}

    save("s7_emergency_disable", out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:6000])
    a.close()


if __name__ == "__main__":
    main()
