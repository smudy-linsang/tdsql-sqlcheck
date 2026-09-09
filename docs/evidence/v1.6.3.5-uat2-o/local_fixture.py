"""O UAT2 isolated local fixture; no production source or existing DB mutation.

Run from repository root. Only localhost:13306, dedicated uat_o_1635_r2 DBs.
Web authentication remains enabled; the dedicated DB contains only test identities.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ.update({
    "SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
    "SQLCHECK_DB_NAME": "uat_o_1635_r2_meta", "AUTH_ENABLED": "true",
    "SCHEDULER_ENABLED": "false", "DATA_MASKING_ENABLED": "false",
    "REPORT_OUTPUT_DIR": str(ROOT / "data/reports/uat_o_1635_r2"),
    "PYTHONPATH": str(ROOT),
})


def setup():
    import pymysql
    from backend.services.database import ensure_db, MYSQL_CONFIG
    from backend.services.connection_registry import registry
    ensure_db()
    # New synthetic identity only; never reset any existing user or admin password.
    from backend.services.auth_service import hash_password
    from backend.services.database import _get_connection
    cx = _get_connection()
    if not cx.execute("SELECT 1 FROM users WHERE username=?", ("uat_o_1635_r2",)).fetchone():
        pw, salt = hash_password(os.environ["UAT_FIXTURE_PASSWORD"])
        cx.execute("INSERT INTO users (username,display_name,role,password_hash,salt,"
                   "status,must_change_password,created_by) VALUES (?,?,?,?,?,'active',0,?)",
                   ("uat_o_1635_r2", "O-UAT2测试员", "admin", pw, salt, "O-UAT2 fixture"))
        cx.commit()
    cx.close()
    cfg = dict(MYSQL_CONFIG)
    cfg.pop("database")
    with pymysql.connect(**cfg) as conn:
        with conn.cursor() as c:
            c.execute("CREATE DATABASE IF NOT EXISTS uat_o_1635_r2_target")
            for i in range(63):
                c.execute(f"CREATE TABLE IF NOT EXISTS uat_o_1635_r2_target.t_uat_{i:03d} "
                          "(id BIGINT NOT NULL COMMENT 'ID', name VARCHAR(64) NOT NULL "
                          "DEFAULT '' COMMENT 'name', PRIMARY KEY(id)) ENGINE=InnoDB "
                          "DEFAULT CHARSET=utf8mb4 COMMENT='O synthetic UAT fixture'")
        conn.commit()
    for cid, name, port, db in (
        ("o-r2-local", "O-UAT2-63表-本机模拟目标", 13306, "uat_o_1635_r2_target"),
        ("o-r2-offline", "O-UAT2-不可连接-本机", 13307, "uat_o_1635_r2_target"),
        ("o-r2-missing", "O-UAT2-不存在库-本机", 13306, "uat_o_1635_r2_missing"),
    ):
        registry.save_connection(name=name, host="127.0.0.1", port=port,
                                 username=MYSQL_CONFIG["user"], password=MYSQL_CONFIG["password"],
                                 database=db, is_distributed=False, conn_id=cid,
                                 is_default=(cid == "o-r2-local"), operator="O-UAT2")
    print("Fixture ready: loopback only; 63 synthetic tables; existing DBs untouched.")


def inspect():
    from backend.services.database import _get_connection
    from backend.services.metadata_audit_repository import repository
    from backend.api.metadata_audit import _check_runner_ready
    conn = _get_connection()
    rows = conn.execute("SELECT id,state,phase,progress_json,error_code,error_message,"
                        "exit_code,cleanup_ok,report_id,created_at,finished_at "
                        "FROM metadata_audit_jobs ORDER BY created_at").fetchall()
    conn.close()
    slot = repository.slot_state()
    try:
        _check_runner_ready()
        ready = "ACCEPTS"
    except Exception as e:
        ready = {"code": getattr(e, "code", None), "message": str(e)}
    print(json.dumps({"slot": slot, "readiness": ready, "jobs": rows}, ensure_ascii=False, default=str, indent=2))


def rescan_evidence(add_table=False):
    from backend.services.database import _get_connection
    cx = _get_connection()
    job = cx.execute("SELECT id,state,created_at,report_id FROM metadata_audit_jobs "
                     "WHERE connection_id='o-r2-local' ORDER BY created_at DESC LIMIT 1").fetchone()
    assert job and job["state"] == "SUCCEEDED", "Wait for the baseline scan to finish"
    if add_table:
        cx.execute("CREATE TABLE IF NOT EXISTS uat_o_1635_r2_target.t_uat_added_after_scan "
                   "(id BIGINT NOT NULL, PRIMARY KEY(id)) ENGINE=InnoDB COMMENT='rescan freshness fixture'")
        cx.commit()
    count = cx.execute("SELECT COUNT(*) AS count FROM information_schema.TABLES "
                       "WHERE TABLE_SCHEMA='uat_o_1635_r2_target'").fetchone()["count"]
    jobs_count = cx.execute("SELECT COUNT(*) AS count FROM metadata_audit_jobs").fetchone()["count"]
    history = cx.execute("SELECT total_sql FROM audit_history WHERE id=?", (job["report_id"],)).fetchone()
    cx.close()
    data = {"target_tables": count, "latest_job": job, "total_jobs": jobs_count, "report": history}
    label = "rescan-before-click" if add_table else "rescan-after-click"
    dest = Path(__file__).resolve().parent / (label + ".json")
    dest.write_text(json.dumps(data, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "setup":
        setup()
    elif mode == "web":
        import uvicorn
        uvicorn.run("backend.main:app", host="127.0.0.1", port=8005)
    elif mode == "runner":
        from backend.workers.metadata_runner import main
        main()
    elif mode == "inspect":
        inspect()
    elif mode == "add-table-for-rescan":
        rescan_evidence(True)
    elif mode == "rescan-evidence":
        rescan_evidence(False)
