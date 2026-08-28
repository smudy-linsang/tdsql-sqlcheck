"""Read-only inventory of the explicitly isolated UAT database at handoff."""
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
if os.environ.get('SQLCHECK_DB_NAME') != 'tdsql_uat_o_r2_1622_20260828':
    raise SystemExit('Wrong database')
from backend.services.database import _get_connection

c = _get_connection()
rows = {'metadata_database':os.environ['SQLCHECK_DB_NAME']}
rows['connections'] = [dict(r) for r in c.execute('SELECT id,name,host,port,`database` FROM tdsql_connections').fetchall()]
rows['users'] = [dict(r) for r in c.execute('SELECT username,role FROM users').fetchall()]
rows['slow_queries'] = dict(c.execute('SELECT COUNT(*) AS records FROM slow_queries').fetchone())
rows['scan_tasks'] = dict(c.execute('SELECT COUNT(*) AS records FROM scan_tasks').fetchone())
c.close()
(HERE/'final_state.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
print(json.dumps(rows,ensure_ascii=False,indent=2))
