"""智能体D / v1.6.3.5 UAT 第三轮整改复测：D-02 生产接线探针。

Q 的 tests/test_v1635_uat3.py 直接调用了 repo.reclaim_stale_accepted()，因此**不覆盖**
两个真实调用点（受理路径 create_job、runner._tick）。本探针补齐这两条接线：

  w1    受理自愈：存在过期未认领任务时，新的受理必须成功（非 409），旧任务判 START_TIMEOUT
  prep  制造一个过期未认领任务（供 w2 用 runner 进程验证）
  check 查看某任务状态与槽归属
  slot  释放槽（收尾用）
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                   "SQLCHECK_DB_NAME": "uat_d_1635_r3_meta", "AUTH_ENABLED": "true"})
sys.path.insert(0, str(ROOT))

from backend.services.database import _get_connection, ensure_db  # noqa: E402
from backend.services.metadata_audit_repository import repository as repo  # noqa: E402
from backend.services import metadata_audit_repository as R  # noqa: E402

HERE = Path(__file__).resolve().parent
STATE = HERE / ".d02-probe-state.json"


def _mk(key):
    return repo.create_job(
        created_by="uat_d_1635_r3", request_id="D-d02", idempotency_key=key,
        request_hash=key, connection_id="d-r3-local", db_name="uat_d_1635_r3_target",
        request_json='{"scopes":["TABLE"]}',
        execution_context_json='{"connection_name":"D-UAT3-63表-本机模拟目标"}',
        connection_fingerprint="fp", report_deadline_seconds=1800)


def _backdate(job_id, seconds):
    conn = _get_connection()
    past = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime(
        "%Y-%m-%d %H:%M:%S.%f")
    conn.execute("UPDATE metadata_audit_jobs SET created_at=? WHERE id=?", (past, job_id))
    conn.commit(); conn.close()


def _clear():
    conn = _get_connection()
    conn.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
    conn.execute("DELETE FROM metadata_audit_jobs WHERE request_id='D-d02'")
    conn.commit(); conn.close()


def w1():
    """受理路径自愈（生产调用点：create_job）。"""
    ensure_db()
    _clear()
    ghost, _ = _mk("d02ghost")
    _backdate(ghost["id"], 60)
    before = repo.get_job(ghost["id"])
    out = {"ghost_job": ghost["id"], "ghost_state_before": before["state"],
           "ghost_created_at": str(before["created_at"])}
    try:
        new_job, created = _mk("d02new")
        out["new_job_created"] = created
        out["new_job_id"] = new_job["id"]
    except Exception as e:  # noqa: BLE001
        out["new_job_created"] = False
        out["new_job_error"] = f"{type(e).__name__}: {getattr(e, 'code', '')} {e}"
    ghost_after = repo.get_job(ghost["id"])
    out["ghost_state_after"] = ghost_after["state"]
    out["ghost_error_code"] = ghost_after["error_code"]
    out["ghost_finished_at"] = str(ghost_after["finished_at"])
    out["verdict"] = ("受理自愈生效：新任务成功且幽灵任务被判 START_TIMEOUT"
                      if out.get("new_job_created") and ghost_after["state"] == "FAILED"
                      and ghost_after["error_code"] == "START_TIMEOUT"
                      else "未生效或部分生效")
    _clear()
    (HERE / "probe-d02-w1.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def prep():
    """制造过期未认领任务，供 runner 进程回收验证。"""
    ensure_db()
    _clear()
    ghost, _ = _mk("d02runner")
    _backdate(ghost["id"], 60)
    STATE.write_text(json.dumps({"job_id": ghost["id"]}), encoding="utf-8")
    print(json.dumps({"job_id": ghost["id"], "state": repo.get_job(ghost["id"])["state"],
                      "slot": (repo.slot_state() or {}).get("active_job_id")},
                     ensure_ascii=False, indent=2))


def check():
    jid = json.loads(STATE.read_text(encoding="utf-8"))["job_id"]
    j = repo.get_job(jid)
    slot = repo.slot_state() or {}
    out = {"job_id": jid, "state": j["state"], "error_code": j["error_code"],
           "finished_at": str(j["finished_at"]), "cleanup_ok": j["cleanup_ok"],
           "slot_active_job": slot.get("active_job_id")}
    out["verdict"] = ("runner 回收生效" if j["state"] == "FAILED"
                      and j["error_code"] == "START_TIMEOUT" and not slot.get("active_job_id")
                      else "尚未被 runner 回收")
    (HERE / "probe-d02-w2.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    {"w1": w1, "prep": prep, "check": check, "clear": _clear}[sys.argv[1]]()
