"""Seed only O's dedicated regression database for legacy fixture prerequisites."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
assert os.environ.get("SQLCHECK_DB_NAME") == "uat_o_1635_r2_regression"
assert os.environ.get("SQLCHECK_DB_HOST", "127.0.0.1") == "127.0.0.1"
assert os.environ.get("SQLCHECK_DB_PORT", "13306") == "13306"
from backend.services.database import ensure_db, _get_connection
from backend.services.auth_service import auth_service

ensure_db()
auth_service.ensure_bootstrap_admin()
cx = _get_connection()
if not cx.execute("SELECT id FROM slow_queries LIMIT 1").fetchone():
    cx.execute("INSERT INTO slow_queries (fingerprint,sql_text,db_name,exec_count,avg_time_ms) "
               "VALUES (?,?,?,?,?)", ("O-UAT2-synthetic-slow", "SELECT id FROM t_synthetic WHERE id=1",
                                      "uat_o_1635_r2_target", 1, 1000))
    cx.commit()
cx.close()
print("Dedicated regression prerequisites ready; existing application DB untouched.")
