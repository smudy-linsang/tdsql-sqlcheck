"""智能体D / v1.6.3.6 UAT：清理探针残留的悬挂任务（仅本机专用库）。

背景：合成 publish 探针只在库内留下 PUBLISHED/PUBLISHING 状态、从未调用 complete()，
与"runner 在 publish 与 complete 之间退出"是同一状态。清理时按 report_id 是否存在
判定为 SUCCEEDED / FAILED，并写入 finished_at（不删除记录，保留审计痕迹）。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                   "SQLCHECK_DB_NAME": "uat_d_1636_meta"})
sys.path.insert(0, str(ROOT))

from backend.services.database import _get_connection  # noqa: E402

cx = _get_connection()
rows = cx.execute("SELECT id, state, report_id FROM metadata_audit_jobs "
                  "WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELLED')").fetchall()
n_ok = n_bad = 0
for r in rows:
    if r["report_id"]:
        cx.execute("UPDATE metadata_audit_jobs SET state='SUCCEEDED', phase='DONE', "
                   "finished_at=COALESCE(finished_at, UTC_TIMESTAMP(6)), cleanup_ok=1, "
                   "exit_code=0, updated_at=UTC_TIMESTAMP(6) WHERE id=?", (r["id"],))
        n_ok += 1
    else:
        cx.execute("UPDATE metadata_audit_jobs SET state='FAILED', phase='CLEANUP', "
                   "error_code='PROBE_RESIDUE', error_message='D36 探针残留清理', "
                   "finished_at=COALESCE(finished_at, UTC_TIMESTAMP(6)), cleanup_ok=1, "
                   "updated_at=UTC_TIMESTAMP(6) WHERE id=?", (r["id"],))
        n_bad += 1
cx.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
cx.commit()
left = cx.execute("SELECT COUNT(*) AS n FROM metadata_audit_jobs "
                  "WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELLED')").fetchone()["n"]
print(f"清理悬挂任务：置 SUCCEEDED {n_ok} 个 / FAILED {n_bad} 个；剩余非终态 = {left}")
cx.close()
