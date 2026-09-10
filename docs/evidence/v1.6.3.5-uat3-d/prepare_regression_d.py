"""智能体D / v1.6.3.5 UAT：专用回归库前置（只动 uat_d_1635_r3_* 库）。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
assert os.environ.get("SQLCHECK_DB_NAME") in ("uat_d_1635_r3_regression",
                                              "uat_d_1635_r3_tests"), os.environ.get("SQLCHECK_DB_NAME")
assert os.environ.get("SQLCHECK_DB_HOST", "127.0.0.1") == "127.0.0.1"
assert os.environ.get("SQLCHECK_DB_PORT", "13306") == "13306"

from backend.services.database import ensure_db, _get_connection  # noqa: E402
from backend.services.auth_service import auth_service  # noqa: E402

ensure_db()
auth_service.ensure_bootstrap_admin()
cx = _get_connection()
if not cx.execute("SELECT id FROM slow_queries LIMIT 1").fetchone():
    cx.execute("INSERT INTO slow_queries (fingerprint,sql_text,db_name,exec_count,avg_time_ms) "
               "VALUES (?,?,?,?,?)",
               ("D-UAT3-synthetic-slow", "SELECT id FROM t_synthetic WHERE id=1",
                "uat_d_1635_r3_target", 1, 1000))
    cx.commit()
cx.close()
print("D 专用回归库前置就绪；既有应用库未改动。")
