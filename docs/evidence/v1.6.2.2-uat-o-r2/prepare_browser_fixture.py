"""Explicit fixtures only in the exact round-two local metadata database."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
if os.environ.get('SQLCHECK_DB_NAME') != 'tdsql_uat_o_r2_1622_20260828':
    raise SystemExit('Refusing non-round-two database')
from backend.services.auth_service import auth_service
from backend.services.database import _get_connection
from backend.services.slow_query_service import SlowQueryService
from backend.engine.slow_analyzer import SlowQueryRecord

# This is fixture creation, not a password reset of an existing account.
if not auth_service.get_user('admin', use_cache=False):
    _, error = auth_service.create_user('admin', os.environ['UAT_O_PASSWORD'], 'admin', 'UAT-O 管理员', 'UAT-O R2 fixture')
    if error:
        raise RuntimeError(error)
    c = _get_connection()
    c.execute('UPDATE users SET must_change_password=0 WHERE username=?', ('admin',))
    c.commit()
    c.close()
c = _get_connection()
record = c.execute('SELECT id FROM slow_queries WHERE db_name=?', ('uat_o_r2_workflow',)).fetchone()
c.close()
if not record:
    record = SlowQueryService().add_slow_query(SlowQueryRecord(fingerprint='SELECT id FROM t_uat_order WHERE customer_id = ?', sql_text='SELECT id FROM t_uat_order WHERE customer_id = 2', db_name='uat_o_r2_workflow', exec_count=10, avg_time_ms=1200, total_time_ms=12000, rows_examined=10000, rows_sent=10), connection_id='uat_o_local')
print('BROWSER_FIXTURE_READY synthetic_slow_id=', record.get('id'))
