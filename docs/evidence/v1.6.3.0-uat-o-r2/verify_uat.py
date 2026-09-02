"""Reproducible API/database checks for v1.6.3.0 G14 UAT round 2."""
import json
import os
import sys
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

META_DB = "tdsql_uat_o_g14_r2_1630"
TARGET_A = "tdsql_uat_g14_r2_a"
BASE_URL = os.environ.get("UAT_BASE_URL", "http://127.0.0.1:18801")


def login(username: str, password: str) -> str:
    response = httpx.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["token"]


def main():
    if os.environ.get("SQLCHECK_DB_NAME") != META_DB:
        raise SystemExit(f"Refusing metadata database other than {META_DB}")
    password = os.environ["UAT_G14_PASSWORD"]

    health = httpx.get(f"{BASE_URL}/health", timeout=10)
    health.raise_for_status()

    developer_token = login("uat2_g14_developer", password)
    success = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run",
        headers={"Authorization": f"Bearer {developer_token}"},
        json={"connection_id": "uat2_g14_central", "database": TARGET_A},
        timeout=15,
    )
    success.raise_for_status()
    success_data = success.json()

    offline = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run",
        headers={"Authorization": f"Bearer {developer_token}"},
        json={"connection_id": "uat2_g14_offline", "database": TARGET_A},
        timeout=15,
    )
    missing = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run",
        headers={"Authorization": f"Bearer {developer_token}"},
        json={"connection_id": "uat2_g14_central", "database": "tdsql_uat_g14_r2_missing"},
        timeout=15,
    )
    system_db = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run",
        headers={"Authorization": f"Bearer {developer_token}"},
        json={"connection_id": "uat2_g14_central", "database": "mysql"},
        timeout=15,
    )

    auditor_token = login("uat2_g14_auditor", password)
    denied = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run",
        headers={"Authorization": f"Bearer {auditor_token}"},
        json={"connection_id": "uat2_g14_central", "database": TARGET_A},
        timeout=15,
    )

    from backend.services.database import _get_connection

    conn = _get_connection()
    try:
        table_types = conn.execute(
            "SELECT TABLE_TYPE, COUNT(*) AS n FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=? GROUP BY TABLE_TYPE ORDER BY TABLE_TYPE",
            (TARGET_A,),
        ).fetchall()
        history_counts = conn.execute(
            "SELECT connection_id, COUNT(*) AS n FROM table_type_stat "
            "GROUP BY connection_id ORDER BY connection_id"
        ).fetchall()
        missing_created_at = conn.execute(
            "SELECT COUNT(*) AS n FROM table_type_stat WHERE created_at IS NULL"
        ).fetchone()
    finally:
        conn.close()

    body = {
        "health_status": health.status_code,
        "health_version": health.json().get("version"),
        "developer_run_status": success.status_code,
        "developer_result": {
            "database_count": success_data.get("database_count"),
            "total_tables": success_data.get("total_tables"),
            "single_tables": success_data.get("single_tables"),
            "broadcast_tables": success_data.get("broadcast_tables"),
            "shard_tables": success_data.get("shard_tables"),
            "created_at_present": bool(success_data.get("created_at")),
        },
        "offline_status": offline.status_code,
        "offline_body": offline.json(),
        "missing_database_status": missing.status_code,
        "missing_database_body": missing.json(),
        "system_database_status": system_db.status_code,
        "system_database_body": system_db.json(),
        "auditor_run_status": denied.status_code,
        "auditor_body": denied.json(),
        "independent_table_types": [dict(row) for row in table_types],
        "history_counts": [dict(row) for row in history_counts],
        "history_rows_with_null_created_at": dict(missing_created_at)["n"],
    }
    print(json.dumps(body, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
