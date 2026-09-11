"""智能体D / v1.6.3.6 UAT 独立夹具（loopback only；不碰 v1.6.3.5 夹具与生产）。

专用资源：元数据库 uat_d_1636_meta、目标库 uat_d_1636_*、账号 uat_d_1636、
Web 127.0.0.1:8025、注入代理 127.0.0.1:8026。

用法（仓库根目录）：
  python docs/evidence/v1.6.3.6-uat-d/local_harness_d36.py setup
  python docs/evidence/v1.6.3.6-uat-d/local_harness_d36.py web|runner|proxy
  python docs/evidence/v1.6.3.6-uat-d/local_harness_d36.py inspect <label>
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
RUNTIME = ROOT / "data/reports/uat_d_1636"
RUNTIME.mkdir(parents=True, exist_ok=True)
PW_FILE = RUNTIME / "admin.password"
DROP_FILE = RUNTIME / "drop-post.txt"

META_DB = "uat_d_1636_meta"
ACCOUNT = "uat_d_1636"
WEB_PORT = 8025
PROXY_PORT = 8026

DIST_DB = "uat_d_1636_dist"       # 模拟分布式库：含"像子表命名"的真实表
CENT_DB = "uat_d_1636_cent"       # 模拟集中式库：含同样命名的真实表
MISSING_DB = "uat_d_1636_missing"

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
    pw = "D6" + secrets.token_urlsafe(12) + "!x"
    PW_FILE.write_text(pw, encoding="utf-8")
    return pw


def setup():
    """建立 v1.6.3.6 专用元数据库/账号/目标库/连接登记（幂等）。"""
    import pymysql
    from backend.services.database import ensure_db, _get_connection, MYSQL_CONFIG
    from backend.services.auth_service import hash_password
    from backend.services.connection_registry import registry

    ensure_db()
    pw = _password()
    cx = _get_connection()
    if not cx.execute("SELECT 1 FROM users WHERE username=?", (ACCOUNT,)).fetchone():
        h, salt = hash_password(pw)
        cx.execute("INSERT INTO users (username,display_name,role,password_hash,salt,"
                   "status,must_change_password,created_by) VALUES (?,?,?,?,?,'active',0,?)",
                   (ACCOUNT, "D-UAT36测试员", "admin", h, salt, "D-UAT36 fixture"))
        cx.commit()
        print(f"已创建测试账号 {ACCOUNT}（口令见 {PW_FILE}）")
    else:
        print(f"测试账号 {ACCOUNT} 已存在")
    cx.close()

    cfg = dict(MYSQL_CONFIG)
    cfg.pop("database")
    with pymysql.connect(**cfg) as conn:
        with conn.cursor() as c:
            # 分布式库：6 张普通表 + 3 张"命名像 TDSQL 物理子表"的真实可读表（零误杀用）
            c.execute(f"CREATE DATABASE IF NOT EXISTS {DIST_DB} DEFAULT CHARSET utf8mb4")
            for i in range(6):
                c.execute(f"CREATE TABLE IF NOT EXISTS {DIST_DB}.biz_tab_{i:02d} "
                          "(id BIGINT NOT NULL COMMENT 'ID', name VARCHAR(64) NOT NULL "
                          "DEFAULT '' COMMENT 'name', PRIMARY KEY(id)) ENGINE=InnoDB "
                          "DEFAULT CHARSET=utf8mb4 COMMENT='D36 real table'")
            # 父表 + 其"物理子表命名"表（本地可读 → 必须被正常提取，零误杀）
            c.execute(f"CREATE TABLE IF NOT EXISTS {DIST_DB}.cus_bas_merge_log "
                      "(id BIGINT NOT NULL COMMENT 'ID', payload VARCHAR(128) NOT NULL "
                      "DEFAULT '' COMMENT 'p', PRIMARY KEY(id)) ENGINE=InnoDB "
                      "DEFAULT CHARSET=utf8mb4 COMMENT='parent table'")
            for suffix in ("190001", "190002"):
                c.execute(f"CREATE TABLE IF NOT EXISTS {DIST_DB}.cus_bas_merge_log_tdsql_subp{suffix} "
                          "(id BIGINT NOT NULL COMMENT 'ID', seg VARCHAR(32) NOT NULL "
                          "DEFAULT '' COMMENT 'seg', PRIMARY KEY(id)) ENGINE=InnoDB "
                          "DEFAULT CHARSET=utf8mb4 COMMENT='named-like-subpartition (readable)'")
            # 集中式库：2 张普通表 + 1 张同样命名的可读表
            c.execute(f"CREATE DATABASE IF NOT EXISTS {CENT_DB} DEFAULT CHARSET utf8mb4")
            c.execute(f"CREATE TABLE IF NOT EXISTS {CENT_DB}.cent_tab_a "
                      "(id BIGINT NOT NULL COMMENT 'ID', v VARCHAR(32) NOT NULL DEFAULT '' "
                      "COMMENT 'v', PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 "
                      "COMMENT='D36 cent table'")
            c.execute(f"CREATE TABLE IF NOT EXISTS {CENT_DB}.cent_tab_b_tdsql_subp202601 "
                      "(id BIGINT NOT NULL COMMENT 'ID', w VARCHAR(32) NOT NULL DEFAULT '' "
                      "COMMENT 'w', PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 "
                      "COMMENT='cent named-like-subpartition (readable)'")
        conn.commit()

    for cid, name, port, db, dist, is_def in (
        ("d36-dist", "D36-分布式库-含子表命名", 13306, DIST_DB, True, True),
        ("d36-cent", "D36-集中式库-含子表命名", 13306, CENT_DB, False, False),
        ("d36-offline", "D36-不可连接-本机", 13307, DIST_DB, True, False),
        ("d36-missing", "D36-不存在库-本机", 13306, MISSING_DB, True, False),
    ):
        registry.save_connection(name=name, host="127.0.0.1", port=port,
                                 username=MYSQL_CONFIG["user"],
                                 password=MYSQL_CONFIG["password"], database=db,
                                 is_distributed=dist, conn_id=cid,
                                 is_default=is_def, operator="D-UAT36")
    print(f"夹具就绪：{DIST_DB}（9 对象，含 2 张子表命名）/ {CENT_DB}（2 对象）")


def inspect(label):
    from backend.services.database import _get_connection
    from backend.services.metadata_audit_repository import repository
    from backend.api.metadata_audit import _job_summary, _check_runner_ready
    cx = _get_connection()
    jobs = cx.execute("SELECT * FROM metadata_audit_jobs ORDER BY created_at").fetchall()
    hist = cx.execute("SELECT id,total_sql,passed,failed,pass_rate,db_name,connection_id,"
                      "skipped_objects,skipped_benign,skipped_abnormal,omitted_results "
                      "FROM audit_history ORDER BY id DESC LIMIT 6").fetchall()
    cx.close()
    try:
        _check_runner_ready()
        ready = "ACCEPTS"
    except Exception as e:  # noqa: BLE001
        ready = {"code": getattr(e, "code", type(e).__name__), "http_status": getattr(e, "http_status", None)}
    out = {"utc": datetime.now(timezone.utc).isoformat(), "label": label,
           "readiness": ready, "slot": repository.slot_state(),
           "jobs": [_job_summary(j) for j in jobs], "history": hist}
    write_json(HERE / f"{label}.json", out)
    print(json.dumps(out, ensure_ascii=False, default=str))


def runner():
    from backend.workers.metadata_runner import main
    main()


class Proxy(BaseHTTPRequestHandler):
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
                   if k.lower() not in ("host", "connection", "accept-encoding", "content-length")}
        resp = requests.request(self.command, f"http://127.0.0.1:{WEB_PORT}{self.path}",
                                headers=headers, data=body, timeout=120, allow_redirects=False)
        entry = {"utc": datetime.now(timezone.utc).isoformat(), "method": self.command,
                 "path": self.path.split("?")[0], "status": resp.status_code}
        if "/metadata-jobs" in self.path:
            try:
                d = resp.json()
                entry.update(job_id=d.get("job_id"), state=d.get("state"), total=d.get("total"))
            except ValueError:
                pass
        mode = DROP_FILE.read_text().strip() if DROP_FILE.exists() else "off"
        if (self.command == "POST" and self.path == "/api/v1/audit/metadata-jobs"
                and mode in ("once", "all")):
            if mode == "once":
                DROP_FILE.write_text("off", encoding="utf-8")
            entry["injection"] = f"drop POST response ({mode})"
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
            if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding"):
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
    elif mode == "drop-post":
        DROP_FILE.write_text(sys.argv[2], encoding="utf-8")
        print(f"drop-post = {sys.argv[2]}")
    else:
        print(__doc__)
