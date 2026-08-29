"""Create successful comparison rows for fifth-round stale-result browser testing."""
import os
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
if os.environ.get("SQLCHECK_DB_NAME") != "tdsql_uat_o_r5_1622_20260829":
    raise SystemExit("Refusing non-round-five database")

from backend.services.database import _get_connection


conn = _get_connection()
try:
    connection_id = "uat_o_index_r5"
    dates = ("2026-08-26", "2026-08-27")
    conn.execute("DELETE FROM daily_inspection WHERE connection_id=? AND inspect_date IN (?,?)",
                 (connection_id, *dates))
    columns = [row["Field"] for row in conn.execute("SHOW COLUMNS FROM daily_inspection").fetchall()
               if row["Field"] not in ("id", "created_at")]
    placeholders = ",".join("?" for _ in columns)
    for date_value, cpu_value in zip(dates, (21.0, 42.0)):
        values = []
        for name in columns:
            values.append({
                "inspect_date": date_value, "connection_id": connection_id,
                "node": "uat-node-r5", "cpu_peak": cpu_value,
                "cpu_avg": cpu_value - 5, "mem_peak": 30.0,
                "conn_peak": 10.0, "slow_query": 1.0, "delay_peak": 0.1,
                "disk_peak": 20.0, "cpu_cores": 4, "mem_gb": 8.0,
                "data_disk_gb": 100.0, "log_disk_gb": 20.0,
                "cpu_avg_daily": cpu_value - 5, "mem_avg_daily": 25.0,
            }.get(name, 0))
        conn.execute(
            f"INSERT INTO daily_inspection ({','.join(columns)}) VALUES ({placeholders})",
            values)
    conn.commit()
finally:
    conn.close()
print("DAILY_FIXTURE_READY")
