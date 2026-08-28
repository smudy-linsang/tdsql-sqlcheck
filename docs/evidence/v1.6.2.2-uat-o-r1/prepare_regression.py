"""Explicit prerequisites of legacy test_fix_user_issues; isolated DB only."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
if not os.environ.get("SQLCHECK_DB_NAME", "").startswith("tdsql_uat_o_reg"):
    raise SystemExit("Refusing non-regression database")
from backend.services.database import _get_connection, ensure_db
from backend.services.auth_service import auth_service
from backend.services.slow_query_service import SlowQueryService
from backend.engine.slow_analyzer import SlowQueryRecord

ensure_db()
auth_service.ensure_bootstrap_admin()
conn = _get_connection()
exists = conn.execute("SELECT id FROM slow_queries LIMIT 1").fetchone()
conn.close()
if not exists:
    SlowQueryService().add_slow_query(SlowQueryRecord(fingerprint="SELECT id FROM t_uat_order WHERE id = ?", sql_text="SELECT id FROM t_uat_order WHERE id = 1", db_name="uat_synthetic", exec_count=10, avg_time_ms=1200, total_time_ms=12000, rows_examined=10000, rows_sent=10))
print("REGRESSION_PREREQUISITES_READY: admin + synthetic slow-query record")
