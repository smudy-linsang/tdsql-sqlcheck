"""Reproducible API, RBAC and protocol checks for G14 UAT round 4."""
import json
import os
import sys
from pathlib import Path

import httpx
import pymysql


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

META_DB = "tdsql_uat_o_g14_r4_1630"
TARGET_DB = "tdsql_demo_distributed"
BASE_URL = os.environ.get("UAT_BASE_URL", "http://127.0.0.1:18803")


def login(username, password):
    response = httpx.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={"username": username, "password": password}, timeout=10)
    response.raise_for_status()
    return response.json()["token"]


def rows_for(cursor, sql):
    cursor.execute(sql)
    description = cursor.description or []
    return {
        "columns": [column[0] for column in description],
        "rows": [list(row) for row in (cursor.fetchall() or [])],
    }


def main():
    if os.environ.get("SQLCHECK_DB_NAME") != META_DB:
        raise SystemExit(f"Refusing metadata database other than {META_DB}")
    user_password = os.environ["UAT_G14_PASSWORD"]
    target_password = os.environ["UAT_TDSQL_PASSWORD"]

    health = httpx.get(f"{BASE_URL}/health", timeout=10)
    health.raise_for_status()
    developer = login("uat4_g14_developer", user_password)
    dev_headers = {"Authorization": f"Bearer {developer}"}
    success = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run", headers=dev_headers,
        json={"connection_id": "uat4_g14_target", "database": TARGET_DB},
        timeout=30)
    success.raise_for_status()
    result = success.json()
    missing = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run", headers=dev_headers,
        json={"connection_id": "uat4_g14_target", "database": "uat4_missing"},
        timeout=30)
    offline = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run", headers=dev_headers,
        json={"connection_id": "uat4_g14_offline", "database": TARGET_DB},
        timeout=30)
    auditor = login("uat4_g14_auditor", user_password)
    denied = httpx.post(
        f"{BASE_URL}/api/v1/table-type-stats/run",
        headers={"Authorization": f"Bearer {auditor}"},
        json={"connection_id": "uat4_g14_target", "database": TARGET_DB},
        timeout=30)

    proxy = pymysql.connect(
        host="127.0.0.1", port=15002, user="root", password=target_password,
        charset="utf8mb4")
    try:
        proxy.select_db(TARGET_DB)
        with proxy.cursor() as cursor:
            direct = {
                "single": rows_for(cursor, "/*proxy*/show table without shardkey"),
                "broadcast": rows_for(cursor, "/*proxy*/show table with noshardkey_allset"),
                "shard": rows_for(cursor, "/*proxy*/show table with shardkey"),
            }
    finally:
        proxy.close()

    backend = pymysql.connect(
        host="127.0.0.1", port=13306, user="root", password=target_password,
        database=TARGET_DB, charset="utf8mb4")
    try:
        with backend.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME",
                (TARGET_DB,))
            baseline = [row[0] for row in cursor.fetchall()]
    finally:
        backend.close()

    sets = {
        kind: {row[0].split(".", 1)[-1] for row in value["rows"]}
        for kind, value in direct.items()
    }
    union = set().union(*sets.values())
    intersections = (
        sets["single"] & sets["broadcast"] |
        sets["single"] & sets["shard"] |
        sets["broadcast"] & sets["shard"])
    output = {
        "health": {"status": health.status_code, "version": health.json().get("version")},
        "target_api": {key: result.get(key) for key in (
            "database_count", "total_tables", "single_tables", "broadcast_tables",
            "shard_tables", "baseline_tables", "subpartition_tables",
            "failed_databases", "overlap_count", "stat_id", "created_at")},
        "direct_proxy": direct,
        "independent_baseline": baseline,
        "reconciliation": {
            "category_union_equals_baseline": union == set(baseline),
            "categories_pairwise_disjoint": not intersections,
            "api_total_equals_union": result["total_tables"] == len(union),
        },
        "missing_database": {"status": missing.status_code, "body": missing.json()},
        "offline": {"status": offline.status_code, "body": offline.json()},
        "auditor_denied": {"status": denied.status_code, "body": denied.json()},
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
