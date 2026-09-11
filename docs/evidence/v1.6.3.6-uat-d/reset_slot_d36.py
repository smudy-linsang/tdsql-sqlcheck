"""智能体D / v1.6.3.6 UAT：释放专用槽并列出可核对的历史报告（仅本机专用库）。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                   "SQLCHECK_DB_NAME": "uat_d_1636_meta"})
sys.path.insert(0, str(ROOT))

from backend.services.database import _get_connection  # noqa: E402

cx = _get_connection()
cx.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
cx.commit()
rows = cx.execute("SELECT id, db_name, total_sql, skipped_objects, skipped_benign, "
                  "skipped_abnormal, omitted_results FROM audit_history "
                  "WHERE audit_type='extracted_schema' ORDER BY id").fetchall()
print("slot 已释放；可核对的历史报告：")
for r in rows:
    print(f"  report={r['id']} db={r['db_name']} total={r['total_sql']} "
          f"skip={r['skipped_objects']}(良性{r['skipped_benign']}/异常{r['skipped_abnormal']}) "
          f"omitted={r['omitted_results']}")
cx.close()
