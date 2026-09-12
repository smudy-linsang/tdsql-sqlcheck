"""智能体D / v1.6.3.7 SIT：在线元数据提取 SQL 文件命名规则还原——验证探针。

被测对象：**工作区未提交的 G 实现**（`metadata_audit_worker.py` 命名还原 + 版本标记 + 时区口径变更）。
本脚本只读/可逆：造数与断言均在本机专用库 `uat_d_1636_*`，不改动产品代码。

子命令：
  env            列定义与既有记录（时区口径对照）
  naming         端到端跑一个真实任务，核验命名在 5 个消费面的表现
  edge_dup       同一秒内两次提取 → 文件名是否冲突
  edge_long      超长库名 → source 列长度边界
  mixed_tz       新旧记录 created_at 时区口径混用与日期筛选影响
"""
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                   "SQLCHECK_DB_NAME": "uat_d_1636_meta", "AUTH_ENABLED": "true",
                   "REPORT_OUTPUT_DIR": str(ROOT / "data/reports/uat_d_1636/reports")})
sys.path.insert(0, str(ROOT))

from backend.services import metadata_audit_repository as R  # noqa: E402
from backend.services.database import _get_connection  # noqa: E402
from backend.services.metadata_audit_repository import repository as repo  # noqa: E402

HERE = Path(__file__).resolve().parent
DIST_DB = "uat_d_1636_dist"
NAME_RE = re.compile(r"^extracted_(?P<db>.+)_(?P<ts>\d{8}_\d{6})\.sql$")


def _cx():
    return _get_connection()


def _wipe():
    cx = _cx()
    cx.execute("UPDATE metadata_audit_jobs SET state='FAILED', phase='CLEANUP', "
               "error_code='SIT_WIPE', finished_at=UTC_TIMESTAMP(6), cleanup_ok=1 "
               "WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELLED')")
    cx.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
    cx.commit(); cx.close()


def _mk(conn_id="d36-dist", db=DIST_DB, conn_name="D36-分布式库-含子表命名"):
    key = "sit37" + datetime.now(timezone.utc).strftime("%H%M%S%f")[:9]
    job, _ = repo.create_job(
        created_by="uat_d_1636", request_id="SIT37", idempotency_key=key, request_hash=key,
        connection_id=conn_id, db_name=db,
        request_json=json.dumps({"connection_id": conn_id, "database": db, "scopes": ["TABLE"]}),
        execution_context_json=json.dumps({"instance_type": "distributed",
                                           "connection_name": conn_name}),
        connection_fingerprint="fp", report_deadline_seconds=1800)
    token = "tok" + key
    repo.claim_next_accepted("runner-sit37", token, R._now())
    return job["id"], token


def _run_job(db=DIST_DB, conn_id="d36-dist"):
    from backend.workers import metadata_audit_worker as W
    jid, token = _mk(conn_id, db)
    rc = W.run(jid, token)
    repo.complete(jid, token, exit_code=0, cleanup_ok=True)
    repo.release_slot(jid)
    return jid, rc, repo.get_job(jid)


def env():
    cx = _cx()
    out = {}
    for tbl, col in (("audit_history", "source"), ("scan_snapshots", "scan_label")):
        row = cx.execute(f"SHOW COLUMNS FROM {tbl} LIKE ?", (col,)).fetchone()
        out[f"{tbl}.{col}"] = {k: str(v) for k, v in (row or {}).items()}
    out["existing_extracted"] = [
        {"id": r["id"], "source": r["source"], "created_at": str(r["created_at"])}
        for r in cx.execute("SELECT id, source, created_at FROM audit_history "
                            "WHERE audit_type='extracted_schema' ORDER BY id DESC LIMIT 5").fetchall()]
    out["server_local_now"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out["server_utc_now"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cx.close()
    (HERE / "sit-env.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def naming():
    """端到端命名核验：worker → audit_history.source → scan_label → 下载名 → HTML 抬头。"""
    _wipe()
    t0 = datetime.now()
    jid, rc, job = _run_job()
    out = {"job_id": jid, "worker_rc": rc, "state": job["state"],
           "report_id": job.get("report_id")}
    cx = _cx()
    row = cx.execute("SELECT id, source, created_at, db_name FROM audit_history "
                     "WHERE id=?", (job.get("report_id"),)).fetchone()
    snap = cx.execute("SELECT scan_label FROM scan_snapshots WHERE biz_ref_id=?",
                      (str(job.get("report_id")),)).fetchone()
    d = ROOT / "data/reports/uat_d_1636/reports/metadata-audit" / jid
    header = ""
    schema = d / "schema.sql"
    if schema.exists():
        for line in schema.read_text(encoding="utf-8").splitlines()[:12]:
            if "提取日期" in line:
                header = line
                break
    cx.close()
    src = (row or {}).get("source") or ""
    m = NAME_RE.match(src)
    out.update({
        "source": src,
        "source_matches_rule": bool(m),
        "db_part": m.group("db") if m else None,
        "ts_part": m.group("ts") if m else None,
        "contains_job_id_fragment": (jid[:8] in src) if src else None,
        "scan_label": (snap or {}).get("scan_label"),
        "scan_label_equals_source": ((snap or {}).get("scan_label") == src),
        "sql_header_line": header.strip(),
        "header_ts_matches_filename": (m.group("ts") in header) if (m and header) else None,
        "audit_history_created_at": str((row or {}).get("created_at")),
        "worker_ran_within_s": round((datetime.now() - t0).total_seconds(), 1),
    })
    # 时间戳与本地时间一致性（容差 120s，覆盖任务耗时）
    if m:
        ts = datetime.strptime(m.group("ts"), "%Y%m%d_%H%M%S")
        out["ts_delta_s_vs_local_start"] = round((ts - t0).total_seconds(), 1)
    (HERE / "sit-naming.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def edge_dup():
    """同一秒内两次提取 → 文件名是否相同（历史契约下的固有行为）。"""
    from backend.workers import metadata_audit_worker as W
    _wipe()
    fixed = datetime(2026, 9, 12, 15, 30, 0)
    orig_now = W.datetime

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.replace(tzinfo=tz)

    W.datetime = _FrozenDatetime
    try:
        j1, r1, job1 = _run_job()
        j2, r2, job2 = _run_job()
    finally:
        W.datetime = orig_now
    cx = _cx()
    rows = cx.execute("SELECT id, source FROM audit_history WHERE id IN (?,?)",
                      (job1.get("report_id"), job2.get("report_id"))).fetchall()
    cx.close()
    names = [(r["id"], r["source"]) for r in rows]
    out = {"job1": j1, "job2": j2, "rows": [{"id": i, "source": s} for i, s in names],
           "two_jobs_same_name": len({s for _, s in names}) == 1 and len(names) == 2,
           "note": "同名代表两次不同任务产出同一文件名；磁盘产物按 job_id 隔离，故仅影响可读性与检索"}
    (HERE / "sit-edge-dup.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def edge_long():
    """超长库名（MySQL 上限 64 字符）→ 文件名长度 vs audit_history.source / scan_label 列宽。"""
    import pymysql
    from backend.services.database import MYSQL_CONFIG
    prefix = "uat_d_1636_"
    long_db = prefix + "x" * (64 - len(prefix))   # 合法最长 64 字符
    cfg = dict(MYSQL_CONFIG); cfg.pop("database")
    with pymysql.connect(**cfg) as conn:
        with conn.cursor() as c:
            c.execute(f"CREATE DATABASE IF NOT EXISTS `{long_db}` DEFAULT CHARSET utf8mb4")
            c.execute(f"CREATE TABLE IF NOT EXISTS `{long_db}`.t_one "
                      "(id BIGINT NOT NULL, PRIMARY KEY(id)) ENGINE=InnoDB")
        conn.commit()
    from backend.services.connection_registry import registry
    registry.save_connection(name="D36-SIT-超长库名", host="127.0.0.1", port=13306,
                             username=MYSQL_CONFIG["user"], password=MYSQL_CONFIG["password"],
                             database=long_db, is_distributed=True, conn_id="d36-sit-long",
                             is_default=False, operator="D-SIT")
    _wipe()
    jid, rc, job = _run_job(db=long_db, conn_id="d36-sit-long")
    cx = _cx()
    row = cx.execute("SELECT id, source FROM audit_history WHERE id=?",
                     (job.get("report_id"),)).fetchone()
    cx.close()
    src = (row or {}).get("source") or ""
    expected = f"extracted_{long_db}_" + "0" * 15 + ".sql"
    out = {"db_len": len(long_db), "expected_name_len": len(expected), "rc": rc,
           "state": job["state"], "source": src, "actual_len": len(src),
           "source_matches_rule": bool(NAME_RE.match(src)),
           "truncated_or_rejected": (not NAME_RE.match(src)),
           "verdict": ("PASS 超长库名仍完整落库" if NAME_RE.match(src)
                       else "FAIL 超长库名导致命名不合规（详见 source）")}
    (HERE / "sit-edge-long.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def mixed_tz():
    """新旧记录 created_at 时区口径混用：同表内 UTC 与本地时间并存的实际影响。"""
    cx = _cx()
    rows = cx.execute("SELECT id, source, created_at FROM audit_history "
                      "WHERE audit_type='extracted_schema' ORDER BY id").fetchall()
    cx.close()
    data = [{"id": r["id"], "source": r["source"], "created_at": str(r["created_at"])} for r in rows]
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    local_now = datetime.now()
    skew = round((local_now - utc_now).total_seconds() / 3600, 1)
    out = {"server_tz_offset_h": skew, "records": data,
           "note": ("v1.6.3.6 及更早的 worker 记录写 UTC；本次整改后写本地时间 —— "
                    "同列混用两种时基，差 = 服务器时区偏移")}
    (HERE / "sit-mixed-tz.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    {"env": env, "naming": naming, "edge_dup": edge_dup,
     "edge_long": edge_long, "mixed_tz": mixed_tz}[sys.argv[1]]()
