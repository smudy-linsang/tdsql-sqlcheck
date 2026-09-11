"""智能体D / v1.6.3.6 补丁复测：`reclaim_stale_unowned` 行为矩阵（工单 L1~L5、L10）。

每个场景独立造数、独立断言、独立收尾；判定按 FIXREQ-v1.6.3.6-01 §2.1 / §2.1b。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                   "SQLCHECK_DB_NAME": "uat_d_1636_meta"})
sys.path.insert(0, str(ROOT))

from backend.services import metadata_audit_repository as R  # noqa: E402
from backend.services.database import _get_connection  # noqa: E402
from backend.services.metadata_audit_repository import repository as repo  # noqa: E402

HERE = Path(__file__).resolve().parent
DIST_DB = "uat_d_1636_dist"
SMALL = json.dumps([{"sql": "CREATE TABLE t (id INT)", "sql_type": "CREATE",
                     "passed": True, "file_path": "f.sql", "line_number": 1,
                     "violations": []}], ensure_ascii=False)


def _cx():
    return _get_connection()


def _wipe():
    """把所有非终态任务与槽清空（场景隔离）。"""
    cx = _cx()
    cx.execute("UPDATE metadata_audit_jobs SET state='FAILED', phase='CLEANUP', "
               "error_code='RETEST_WIPE', finished_at=UTC_TIMESTAMP(6), cleanup_ok=1 "
               "WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELLED')")
    cx.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
    cx.commit(); cx.close()


def _mk(tag):
    key = f"rt{tag}" + datetime.now(timezone.utc).strftime("%H%M%S%f")[:8]
    job, _ = repo.create_job(
        created_by="uat_d_1636", request_id="RETEST", idempotency_key=key, request_hash=key,
        connection_id="d36-dist", db_name=DIST_DB,
        request_json=json.dumps({"connection_id": "d36-dist", "database": DIST_DB,
                                 "scopes": ["TABLE"]}),
        execution_context_json=json.dumps({"instance_type": "distributed",
                                           "connection_name": "D36-分布式库-含子表命名"}),
        connection_fingerprint="fp", report_deadline_seconds=1800)
    return job["id"], "tok" + key


def _backdate(job_id, seconds, column="updated_at"):
    cx = _cx()
    past = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime(
        "%Y-%m-%d %H:%M:%S.%f")
    cx.execute(f"UPDATE metadata_audit_jobs SET {column}=? WHERE id=?", (past, job_id))
    cx.commit(); cx.close()


def _set_slot(job_id):
    cx = _cx()
    cx.execute("UPDATE metadata_audit_slot SET active_job_id=? WHERE id=1", (job_id,))
    cx.commit(); cx.close()


def _slot():
    return (repo.slot_state() or {}).get("active_job_id")


def _publish(job_id, token):
    cols = ("extracted_schema", "f.sql", 1, 1, 0, 0, 0, 100.0, SMALL, "uat_d_1636",
            "", None, "", "2026-09-11 00:00:00.000000", "d36-dist", DIST_DB, None,
            "distributed", "probe", 0, None, 0, 0, 0)
    repo.cas_state(job_id, token, (R.STATE_RUNNING,), R.STATE_PUBLISHING,
                   phase=R.PHASE_PERSISTING)
    return repo.publish(job_id, token, audit_columns_values=cols, results_json=SMALL)


out = {}

# L1：PUBLISHED + 有 report + 槽指向它 + 超时 → SUCCEEDED 且释放槽
_wipe()
j1, t1 = _mk("pub")
repo.claim_next_accepted("runner-rt", t1, R._now())
rid = _publish(j1, t1)
_backdate(j1, 1200, "updated_at")
_backdate(j1, 1200, "created_at")
_set_slot(j1)
res = repo.reclaim_stale_unowned(30, 600)
g = repo.get_job(j1)
out["L1_published_with_report"] = {
    "reclaim_returned": res, "state": g["state"], "phase": g["phase"],
    "finished_at": str(g["finished_at"]), "slot_after": _slot(), "report_id": g["report_id"],
    "verdict": "PASS" if g["state"] == "SUCCEEDED" and g["finished_at"] and _slot() is None
               else "FAIL"}

# L2：PUBLISHING 无 report + 超时 → FAILED / PERSIST_TIMEOUT
_wipe()
j2, t2 = _mk("pubno")
repo.claim_next_accepted("runner-rt", t2, R._now())
repo.cas_state(j2, t2, (R.STATE_RUNNING,), R.STATE_PUBLISHING, phase=R.PHASE_PERSISTING)
_backdate(j2, 1200)
_set_slot(j2)
repo.reclaim_stale_unowned(30, 600)
g = repo.get_job(j2)
out["L2_publishing_without_report"] = {
    "state": g["state"], "error_code": g["error_code"], "slot_after": _slot(),
    "verdict": "PASS" if g["state"] == "FAILED" and g["error_code"] == "PERSIST_TIMEOUT"
               else "FAIL"}

# L5：PUBLISHED 但未超时 → 不动作
_wipe()
j3, t3 = _mk("fresh")
repo.claim_next_accepted("runner-rt", t3, R._now())
_publish(j3, t3)
_set_slot(j3)
repo.reclaim_stale_unowned(30, 600)
g = repo.get_job(j3)
out["L5_fresh_published_untouched"] = {
    "state": g["state"], "slot_after": _slot(),
    "verdict": "PASS" if g["state"] == "PUBLISHED" and _slot() == j3 else "FAIL"}

# L3（D-04）：ACCEPTED + 槽空闲 + 超时 → 收敛
_wipe()
j4, t4 = _mk("ghost")
_backdate(j4, 300, "created_at")
out["L3_accepted_unowned"] = {"slot_before": _slot()}
repo.reclaim_stale_unowned(30, 600)
g = repo.get_job(j4)
out["L3_accepted_unowned"].update(
    {"state": g["state"], "error_code": g["error_code"],
     "verdict": "PASS" if g["state"] == "FAILED" and g["error_code"] == "START_TIMEOUT"
                else "FAIL"})

# L4：槽被他人占用 → 不动作、不抢槽
_wipe()
j5, t5 = _mk("other")
_backdate(j5, 300, "created_at")
_set_slot("someone-else")
repo.reclaim_stale_unowned(30, 600)
g = repo.get_job(j5)
out["L4_slot_owned_by_other"] = {
    "state": g["state"], "slot_after": _slot(),
    "verdict": "PASS" if g["state"] == "ACCEPTED" and _slot() == "someone-else" else "FAIL"}

# L10（§2.1b 决策）：RUNNING 即使极旧也不被回收
_wipe()
j6, t6 = _mk("running")
repo.claim_next_accepted("runner-rt", t6, R._now())
_backdate(j6, 7200, "created_at")
_backdate(j6, 7200, "updated_at")
_backdate(j6, 7200, "heartbeat_at")
_set_slot(j6)
repo.reclaim_stale_unowned(30, 600)
g = repo.get_job(j6)
out["L10_running_never_touched"] = {
    "state": g["state"], "slot_after": _slot(),
    "verdict": "PASS" if g["state"] == "RUNNING" and _slot() == j6 else "FAIL"}

_wipe()
out["all_pass"] = all(v.get("verdict") == "PASS" for k, v in out.items() if isinstance(v, dict))
(HERE / "probe-reclaim-matrix.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
