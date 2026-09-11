"""智能体D / v1.6.3.6 UAT：任务与 runner 状态诊断（只读）。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                   "SQLCHECK_DB_NAME": "uat_d_1636_meta"})
sys.path.insert(0, str(ROOT))

from backend.services.database import _get_connection  # noqa: E402

cx = _get_connection()
print("slot:", dict(cx.execute("SELECT * FROM metadata_audit_slot WHERE id=1").fetchone() or {}))
rows = cx.execute("SELECT id, state, phase, runner_id, attempt_token, created_at, started_at, "
                  "finished_at, exit_code, cleanup_ok, error_code, report_id "
                  "FROM metadata_audit_jobs ORDER BY created_at").fetchall()
print(f"\n任务数 {len(rows)}：")
for r in rows:
    print(f"  {r['id'][:8]} {r['state']:<10} {str(r['phase']):<12} "
          f"runner={str(r['runner_id'])[:22]:<22} rc={r['exit_code']} cleanup={r['cleanup_ok']} "
          f"report={r['report_id']} err={str(r['error_code'])[:14]:<14} "
          f"created={str(r['created_at'])[:19]}")
cx.close()
