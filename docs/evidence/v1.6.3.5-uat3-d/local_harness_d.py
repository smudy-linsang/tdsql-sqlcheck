"""智能体D / v1.6.3.5 第三轮 UAT 独立夹具（loopback only；不碰 O 的夹具与生产）。

设计边界：
  · 只用 127.0.0.1:13306 与本脚本命名的 `uat_d_1635_r3_*` 专用库；
  · 不修改产品源码、不重置任何既有账号或业务连接；
  · 目标库为合成两列表，无业务数据；
  · 浏览器操作由 docs/evidence/v1.6.3.5-uat3-d/browser_uat_d.py 真实点击驱动。

用法（仓库根目录，使用带依赖的解释器）：
  python docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py setup
  python docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py web      # 127.0.0.1:8015
  python docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py runner
  python docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py proxy    # 127.0.0.1:8016 -> 8015
  python docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py inspect <label>
  python docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py add-table
  python docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py drop-post <off|once|all>
  python docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py delay-child <0-25>
"""
import json
import os
import socket
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNTIME = ROOT / "data/reports/uat_d_1635_r3"
RUNTIME.mkdir(parents=True, exist_ok=True)
PW_FILE = RUNTIME / "admin.password"
DROP_FILE = RUNTIME / "drop-post.txt"
DELAY_FILE = RUNTIME / "delay-child.txt"

META_DB = "uat_d_1635_r3_meta"
TARGET_DB = "uat_d_1635_r3_target"
MISSING_DB = "uat_d_1635_r3_missing"
ACCOUNT = "uat_d_1635_r3"
WEB_PORT = 8015
PROXY_PORT = 8016

os.environ.update({
    "SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
    "SQLCHECK_DB_NAME": META_DB,
    "AUTH_ENABLED": "true", "SCHEDULER_ENABLED": "false",
    "DATA_MASKING_ENABLED": "false",
    "REPORT_OUTPUT_DIR": str(RUNTIME / "reports"),
    "PYTHONPATH": str(ROOT),
})
sys.path.insert(0, str(ROOT))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")


def _password() -> str:
    if PW_FILE.exists():
        return PW_FILE.read_text(encoding="utf-8").strip()
    import secrets
    pw = "D3" + secrets.token_urlsafe(12) + "!x"
    PW_FILE.write_text(pw, encoding="utf-8")
    return pw


def setup():
    """建立 D 专用元数据库/账号/目标库/连接登记（幂等）。"""
    import pymysql
    from backend.services.database import ensure_db, _get_connection, MYSQL_CONFIG
    from backend.services.auth_service import hash_password
    from backend.services.connection_registry import registry

    ensure_db()
    pw = _password()
    cx = _get_connection()
    row = cx.execute("SELECT 1 FROM users WHERE username=?", (ACCOUNT,)).fetchone()
    if not row:
        h, salt = hash_password(pw)
        cx.execute("INSERT INTO users (username,display_name,role,password_hash,salt,"
                   "status,must_change_password,created_by) VALUES (?,?,?,?,?,'active',0,?)",
                   (ACCOUNT, "D-UAT3测试员", "admin", h, salt, "D-UAT3 fixture"))
        cx.commit()
        print(f"已创建测试账号 {ACCOUNT}（口令见 {PW_FILE}）")
    else:
        print(f"测试账号 {ACCOUNT} 已存在（口令沿用 {PW_FILE}）")
    cx.close()

    cfg = dict(MYSQL_CONFIG)
    cfg.pop("database")
    with pymysql.connect(**cfg) as conn:
        with conn.cursor() as c:
            c.execute(f"CREATE DATABASE IF NOT EXISTS {TARGET_DB} "
                      "DEFAULT CHARSET utf8mb4")
            for i in range(63):
                c.execute(
                    f"CREATE TABLE IF NOT EXISTS {TARGET_DB}.t_d_{i:03d} "
                    "(id BIGINT NOT NULL COMMENT 'ID', name VARCHAR(64) NOT NULL "
                    "DEFAULT '' COMMENT 'name', PRIMARY KEY(id)) ENGINE=InnoDB "
                    "DEFAULT CHARSET=utf8mb4 COMMENT='D synthetic UAT fixture'")
        conn.commit()

    for cid, name, port, db, is_def in (
        ("d-r3-local", "D-UAT3-63表-本机模拟目标", 13306, TARGET_DB, True),
        ("d-r3-offline", "D-UAT3-不可连接-本机", 13307, TARGET_DB, False),
        ("d-r3-missing", "D-UAT3-不存在库-本机", 13306, MISSING_DB, False),
    ):
        registry.save_connection(name=name, host="127.0.0.1", port=port,
                                 username=MYSQL_CONFIG["user"],
                                 password=MYSQL_CONFIG["password"],
                                 database=db, is_distributed=False, conn_id=cid,
                                 is_default=is_def, operator="D-UAT3")
    print(f"夹具就绪：仅 loopback；{TARGET_DB} 63 张合成表；既有库未改动。")
    print("DRIVER: " + ("pythoncore-3.14-64" if "pythoncore" in sys.executable else sys.executable))


def inspect(label):
    from backend.services.database import _get_connection
    from backend.services.metadata_audit_repository import repository
    from backend.api.metadata_audit import _job_summary, _check_runner_ready
    cx = _get_connection()
    jobs = cx.execute("SELECT * FROM metadata_audit_jobs ORDER BY created_at").fetchall()
    n = cx.execute("SELECT COUNT(*) AS n FROM information_schema.TABLES "
                   f"WHERE TABLE_SCHEMA='{TARGET_DB}'").fetchone()["n"]
    hist = cx.execute("SELECT id,total_sql,passed,failed,connection_id,db_name "
                      "FROM audit_history ORDER BY id DESC LIMIT 5").fetchall()
    cx.close()
    try:
        _check_runner_ready()
        ready = "ACCEPTS"
    except Exception as e:  # noqa: BLE001
        ready = {"code": getattr(e, "code", type(e).__name__),
                 "message": getattr(e, "message", str(e)),
                 "http_status": getattr(e, "http_status", None)}
    out = {"utc": datetime.now(timezone.utc).isoformat(), "label": label,
           "target_tables": n, "readiness": ready,
           "slot": repository.slot_state(),
           "jobs": [_job_summary(j) for j in jobs], "history": hist}
    write_json(HERE / f"{label}.json", out)
    print(json.dumps(out, ensure_ascii=False, default=str))
    return out


def add_table():
    from backend.services.database import _get_connection
    cx = _get_connection()
    cx.execute(f"CREATE TABLE IF NOT EXISTS {TARGET_DB}.t_d_added_after_scan "
               "(id BIGINT NOT NULL COMMENT 'ID', note VARCHAR(32) NOT NULL DEFAULT '' "
               "COMMENT 'note', PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 "
               "COMMENT='D rescan freshness fixture'")
    cx.commit()
    n = cx.execute("SELECT COUNT(*) AS n FROM information_schema.TABLES "
                   f"WHERE TABLE_SCHEMA='{TARGET_DB}'").fetchone()["n"]
    cx.close()
    print(f"已新增第 {n} 张表 t_d_added_after_scan（{TARGET_DB}）")


def runner():
    """runner 进程；支持通过 delay-child.txt 注入 child 启动延迟（仅测试用）。"""
    from backend.services import metadata_job_process as jp
    from backend.workers.metadata_runner import main
    original = jp.run_metadata_worker

    def delayed(cmd, **kw):
        seconds = int(DELAY_FILE.read_text().strip()) if DELAY_FILE.exists() else 0
        assert 0 <= seconds <= 25, "delay 仅允许 0-25 秒（测试用途）"
        if seconds:
            assert cmd[1:3] == ["-m", "backend.workers.metadata_audit_worker"], cmd
            cmd = [cmd[0], "-c",
                   "import sys,time,runpy;"
                   f"time.sleep({seconds});"
                   "sys.argv=['backend.workers.metadata_audit_worker']+sys.argv[1:];"
                   "runpy.run_module('backend.workers.metadata_audit_worker',run_name='__main__')"
                   ] + cmd[3:]
        return original(cmd, **kw)

    jp.run_metadata_worker = delayed
    main()


class Proxy(BaseHTTPRequestHandler):
    """8016 -> 8015 反向代理；可丢弃一次/全部已收到的 POST 受理响应（故障注入）。"""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        import requests
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "connection", "accept-encoding",
                                        "content-length")}
        resp = requests.request(self.command, f"http://127.0.0.1:{WEB_PORT}{self.path}",
                                headers=headers, data=body, timeout=120,
                                allow_redirects=False)
        entry = {"utc": datetime.now(timezone.utc).isoformat(), "method": self.command,
                 "path": self.path.split("?")[0], "status": resp.status_code}
        if "/metadata-jobs" in self.path:
            try:
                d = resp.json()
                entry.update(job_id=d.get("job_id"), state=d.get("state"),
                             total=d.get("total"),
                             progress=d.get("progress"), elapsed=d.get("elapsed_seconds"))
            except ValueError:
                pass
        is_accept = (self.command == "POST"
                     and self.path == "/api/v1/audit/metadata-jobs")
        mode = DROP_FILE.read_text().strip() if DROP_FILE.exists() else "off"
        drop = is_accept and (mode == "all" or (mode == "once" and
                                                not (RUNTIME / ".drop-once-used").exists()))
        if drop:
            if mode == "once":
                (RUNTIME / ".drop-once-used").write_text("1", encoding="utf-8")
            entry["injection"] = f"discard accepted POST response ({mode})"
            with (RUNTIME / "proxy-events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if "/metadata-jobs" in self.path:
            with (RUNTIME / "proxy-events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.send_response(resp.status_code)
        for k, v in resp.headers.items():
            if k.lower() not in ("content-length", "transfer-encoding",
                                 "connection", "content-encoding"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp.content)))
        self.end_headers()
        try:
            self.wfile.write(resp.content)
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "help"
    if mode == "setup":
        setup()
    elif mode == "web":
        import uvicorn
        uvicorn.run("backend.main:app", host="127.0.0.1", port=WEB_PORT, log_level="info")
    elif mode == "runner":
        runner()
    elif mode == "proxy":
        ThreadingHTTPServer(("127.0.0.1", PROXY_PORT), Proxy).serve_forever()
    elif mode == "inspect":
        inspect(sys.argv[2] if len(sys.argv) > 2 else "inspect")
    elif mode == "add-table":
        add_table()
    elif mode == "drop-post":
        arg = sys.argv[2]
        DROP_FILE.write_text(arg, encoding="utf-8")
        (RUNTIME / ".drop-once-used").unlink(missing_ok=True)
        print(f"drop-post = {arg}")
    elif mode == "delay-child":
        DELAY_FILE.write_text(sys.argv[2], encoding="utf-8")
        print(f"delay-child = {sys.argv[2]}s")
    else:
        print(__doc__)
