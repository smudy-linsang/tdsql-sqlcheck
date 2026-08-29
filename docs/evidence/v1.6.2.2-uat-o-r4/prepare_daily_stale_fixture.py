"""Create two synthetic comparison rows to verify stale-result behavior in the browser."""
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
if os.environ.get("SQLCHECK_DB_NAME") != "tdsql_uat_o_r4_1622_20260829":
    raise SystemExit("Refusing non-round-four database")
from backend.services.database import _get_connection

conn = _get_connection()
try:
    conn.execute("DELETE FROM daily_inspection WHERE connection_id=? AND inspect_date IN (?,?)",
                 ("uat_o_index", "2026-08-26", "2026-08-27"))
    columns = [row["Field"] for row in conn.execute("SHOW COLUMNS FROM daily_inspection").fetchall()
               if row["Field"] not in ("id", "created_at")]
    placeholders = ",".join("?" for _ in columns)
    for date_value, cpu_value in (("2026-08-26", 21.0), ("2026-08-27", 42.0)):
        values = []
        for name in columns:
            values.append({
                "inspect_date": date_value, "connection_id": "uat_o_index", "node": "uat-node",
                "cpu_peak": cpu_value, "cpu_avg": cpu_value - 5, "mem_peak": 30.0,
                "conn_peak": 10.0, "slow_query": 1.0, "delay_peak": 0.1,
                "disk_peak": 20.0, "cpu_cores": 4, "mem_gb": 8.0,
                "data_disk_gb": 100.0, "log_disk_gb": 20.0,
                "cpu_avg_daily": cpu_value - 5, "mem_avg_daily": 25.0,
            }.get(name, 0))
        conn.execute(f"INSERT INTO daily_inspection ({','.join(columns)}) VALUES ({placeholders})", values)
    conn.commit()
finally:
    conn.close()
print("DAILY_STALE_FIXTURE_READY")
