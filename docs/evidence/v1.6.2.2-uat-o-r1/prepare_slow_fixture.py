"""One synthetic slow-query record for browser state-transition testing."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
if os.environ.get('SQLCHECK_DB_NAME') != 'tdsql_uat_o_1622_20260828':
    raise SystemExit('Wrong database')
from backend.services.slow_query_service import SlowQueryService
from backend.engine.slow_analyzer import SlowQueryRecord
from backend.services.database import _get_connection
c = _get_connection()
row = c.execute('SELECT id FROM slow_queries WHERE db_name=?',('uat_o_synthetic_workflow',)).fetchone()
c.close()
if row:
    print('SYNTHETIC_SLOW_ID',row['id'])
else:
    result = SlowQueryService().add_slow_query(SlowQueryRecord(fingerprint='SELECT id FROM t_uat_order WHERE customer_id = ?',sql_text='SELECT id FROM t_uat_order WHERE customer_id = 2',db_name='uat_o_synthetic_workflow',exec_count=10,avg_time_ms=1200,total_time_ms=12000,rows_examined=10000,rows_sent=10),connection_id='uat_o_local')
    print('SYNTHETIC_SLOW_ID',result.get('id'))
