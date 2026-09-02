"""Prepare isolated fixtures for v1.6.3.0 G14 UAT round 4."""
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

META_DB = "tdsql_uat_o_g14_r4_1630"
TARGET_DB = "tdsql_demo_distributed"


def main():
    if os.environ.get("SQLCHECK_DB_NAME") != META_DB:
        raise SystemExit(f"Refusing metadata database other than {META_DB}")
    user_password = os.environ["UAT_G14_PASSWORD"]
    target_password = os.environ["UAT_TDSQL_PASSWORD"]

    from backend.services.auth_service import auth_service
    from backend.services.connection_registry import registry
    from backend.services.database import _get_connection, ensure_db

    ensure_db()
    auth_service.ensure_bootstrap_admin()
    for role in ("developer", "auditor"):
        username = f"uat4_g14_{role}"
        if not auth_service.get_user(username, use_cache=False):
            _, error = auth_service.create_user(
                username, user_password, role, f"G14 UAT4 {role}",
                "G14 UAT4 isolated fixture")
            if error:
                raise RuntimeError(error)

    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE users SET must_change_password=0, status='active' "
            "WHERE username IN ('uat4_g14_developer','uat4_g14_auditor')")
        conn.commit()
    finally:
        conn.close()

    registry.save_connection(
        name="G14 UAT4 本地TDSQL模拟靶场",
        host="127.0.0.1", port=15002, username="root",
        password=target_password, database=TARGET_DB, charset="utf8mb4",
        is_default=True, is_distributed=True, conn_id="uat4_g14_target",
        operator="UAT-O-R4",
        description="Python TDSQL protocol simulator backed by stock MySQL 8")
    registry.save_connection(
        name="G14 UAT4 离线样本",
        host="127.0.0.1", port=1, username="synthetic", password="",
        database="offline", is_default=False, is_distributed=True,
        conn_id="uat4_g14_offline", operator="UAT-O-R4",
        description="Intentional unavailable endpoint for stale-request UAT")

    print(f"G14_UAT4_FIXTURES_READY metadata={META_DB} users=2 connections=2 target={TARGET_DB}")


if __name__ == "__main__":
    main()
