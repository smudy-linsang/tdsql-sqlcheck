"""智能体D：清理 D-02/D-17 探针在专用元数据库留下的非终态残留（仅本机专用库）。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                   "SQLCHECK_DB_NAME": "uat_d_1635_r3_meta"})
sys.path.insert(0, str(ROOT))

from backend.services.database import _get_connection  # noqa: E402

cx = _get_connection()
rows = cx.execute("SELECT id, state FROM metadata_audit_jobs "
                  "WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELLED')").fetchall()
for r in rows:
    cx.execute("UPDATE metadata_audit_jobs SET state='FAILED', phase='CLEANUP', "
               "error_code='PROBE_RESIDUE', error_message='D 探针残留清理', "
               "finished_at=UTC_TIMESTAMP(6), cleanup_ok=1 WHERE id=?", (r["id"],))
cx.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
cx.commit()
left = cx.execute("SELECT COUNT(*) AS n FROM metadata_audit_jobs "
                  "WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELLED')").fetchone()["n"]
print(f"已清理 {len(rows)} 个非终态残留；剩余非终态 = {left}")
cx.close()
