"""Reproducible API, SQL and protocol checks for G14 UAT round 3."""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
import pymysql


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

META_DB = "tdsql_uat_o_g14_r3_1630"
TARGET_DB = "tdsql_demo_distributed"
CENTRAL_DB = "tdsql_demo_centralized"
BASE_URL = os.environ.get("UAT_BASE_URL", "http://127.0.0.1:18802")


def login(username: str, password: str) -> str:
    response = httpx.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["token"]


def rows_for(cursor, sql: str):
    cursor.execute(sql)
    return {
        "columns": [column[0] for column in cursor.description],
        "rows": [list(row) for row in cursor.fetchall()],
    }


def main():
    if os.environ.get("SQLCHECK_DB_NAME") != META_DB:
        raise SystemExit(f"Refusing metadata database other than {META_DB}")
    user_password = os.environ["UAT_G14_PASSWORD"]
    target_password = os.environ["UAT_TDSQL_PASSWORD"]

    health = httpx.get(f"{BASE_URL}/health", timeout=10)
    health.raise_for_status()
    developer_token = login("uat3_g14_developer", user_password)
    headers = {"Authorization": f"Bearer {developer_token}"}

    target_response = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run",
        headers=headers,
        json={"connection_id": "uat3_g14_target", "database": TARGET_DB},
        timeout=30,
    )
    target_response.raise_for_status()
    target = target_response.json()

    empty_categories_response = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run",
        headers=headers,
        json={"connection_id": "uat3_g14_target", "database": CENTRAL_DB},
        timeout=30,
    )
    empty_categories_response.raise_for_status()
    empty_categories = empty_categories_response.json()

    central_response = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run",
        headers=headers,
        json={"connection_id": "uat3_g14_central", "database": CENTRAL_DB},
        timeout=30,
    )
    central_response.raise_for_status()
    central = central_response.json()

    missing = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run",
        headers=headers,
        json={"connection_id": "uat3_g14_target", "database": "uat3_missing"},
        timeout=30,
    )
    offline = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run",
        headers=headers,
        json={"connection_id": "uat3_g14_offline", "database": TARGET_DB},
        timeout=30,
    )

    auditor_token = login("uat3_g14_auditor", user_password)
    denied = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run",
        headers={"Authorization": f"Bearer {auditor_token}"},
        json={"connection_id": "uat3_g14_target", "database": TARGET_DB},
        timeout=30,
    )

    proxy = pymysql.connect(
        host="127.0.0.1", port=15002, user="root",
        password=target_password, charset="utf8mb4",
    )
    try:
        proxy.select_db(TARGET_DB)
        with proxy.cursor() as cursor:
            direct = {
                "single": rows_for(cursor, "/*proxy*/show table without shardkey"),
                "broadcast": rows_for(
                    cursor, "/*proxy*/show table with noshardkey_allset"),
                "shard": rows_for(cursor, "/*proxy*/show table with shardkey"),
            }
    finally:
        proxy.close()

    # Fidelity boundary: a database selected only in the authentication
    # handshake is not learned by this Python proxy. A real TDSQL gateway is
    # expected to know the authenticated session's default database.
    handshake_proxy = pymysql.connect(
        host="127.0.0.1", port=15002, user="root",
        password=target_password, database=TARGET_DB, charset="utf8mb4",
    )
    try:
        with handshake_proxy.cursor() as cursor:
            handshake_only = {
                "single": rows_for(cursor, "/*proxy*/show table without shardkey"),
                "broadcast": rows_for(
                    cursor, "/*proxy*/show table with noshardkey_allset"),
                "shard": rows_for(cursor, "/*proxy*/show table with shardkey"),
            }
    finally:
        handshake_proxy.close()

    backend = pymysql.connect(
        host="127.0.0.1", port=13306, user="root",
        password=target_password, database=TARGET_DB, charset="utf8mb4",
    )
    try:
        with backend.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' "
                "ORDER BY TABLE_NAME",
                (TARGET_DB,),
            )
            baseline = [row[0] for row in cursor.fetchall()]
    finally:
        backend.close()

    from backend.services.database import _get_connection

    metadata = _get_connection()
    try:
        persisted = metadata.execute(
            "SELECT id, created_at FROM table_type_stat WHERE id=?",
            (target["stat_id"],),
        ).fetchone()
        history_count = metadata.execute(
            "SELECT COUNT(*) AS n FROM table_type_stat WHERE connection_id=?",
            ("uat3_g14_target",),
        ).fetchone()["n"]
    finally:
        metadata.close()

    single_names = {row[0].split(".", 1)[-1] for row in direct["single"]["rows"]}
    broadcast_names = {
        row[0].split(".", 1)[-1] for row in direct["broadcast"]["rows"]
    }
    shard_names = {row[0].split(".", 1)[-1] for row in direct["shard"]["rows"]}
    union_names = single_names | broadcast_names | shard_names

    output = {
        "health": {"status": health.status_code, "version": health.json().get("version")},
        "target_api": {
            key: target.get(key) for key in (
                "database_count", "total_tables", "single_tables",
                "broadcast_tables", "shard_tables", "baseline_tables",
                "failed_databases", "overlap_count", "stat_id", "created_at",
            )
        },
        "target_empty_category_sets": {
            key: empty_categories.get(key) for key in (
                "database_count", "total_tables", "single_tables",
                "broadcast_tables", "shard_tables", "baseline_tables",
                "failed_databases", "overlap_count", "created_at", "items",
            )
        },
        "central_control_on_shared_backend": {
            key: central.get(key) for key in (
                "instance_type", "type_source", "type_conflict", "total_tables",
                "single_tables", "broadcast_tables", "shard_tables",
                "baseline_tables", "failed_databases", "warnings", "items",
            )
        },
        "direct_proxy": direct,
        "handshake_database_without_com_init_db": handshake_only,
        "independent_baseline": baseline,
        "reconciliation": {
            "category_union_equals_baseline": union_names == set(baseline),
            "categories_pairwise_disjoint": not (
                single_names & broadcast_names
                or single_names & shard_names
                or broadcast_names & shard_names
            ),
            "api_total_equals_union": target["total_tables"] == len(union_names),
        },
        "timestamp": {
            "response": target.get("created_at"),
            "persisted": str(persisted["created_at"]),
            "exact_to_second": datetime.fromisoformat(target["created_at"]) ==
            datetime.fromisoformat(str(persisted["created_at"])),
        },
        "history_count": history_count,
        "missing_database": {"status": missing.status_code, "body": missing.json()},
        "offline": {"status": offline.status_code, "body": offline.json()},
        "auditor_denied": {"status": denied.status_code, "body": denied.json()},
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
