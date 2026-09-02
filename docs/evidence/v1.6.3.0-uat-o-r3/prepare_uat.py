"""Prepare isolated fixtures for v1.6.3.0 G14 UAT round 3.

The script writes only to the dedicated UAT metadata database and registers
the already-running local target. Passwords are supplied through environment
variables and are never written to repository files.
"""
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

META_DB = "tdsql_uat_o_g14_r3_1630"
TARGET_DB = "tdsql_demo_distributed"
CENTRAL_DB = "tdsql_demo_centralized"


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
        username = f"uat3_g14_{role}"
        if not auth_service.get_user(username, use_cache=False):
            _, error = auth_service.create_user(
                username,
                user_password,
                role,
                f"G14 UAT3 {role}",
                "G14 UAT3 isolated fixture",
            )
            if error:
                raise RuntimeError(error)

    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE users SET must_change_password=0, status='active' "
            "WHERE username IN ('uat3_g14_developer','uat3_g14_auditor')"
        )
        conn.commit()
    finally:
        conn.close()

    common = dict(
        host="127.0.0.1",
        username="root",
        password=target_password,
        charset="utf8mb4",
        operator="UAT-O-R3",
    )
    registry.save_connection(
        name="G14 UAT3 高仿分布式靶场",
        port=15002,
        database=TARGET_DB,
        is_default=True,
        is_distributed=True,
        conn_id="uat3_g14_target",
        description="Python protocol emulator backed by stock MySQL 8",
        **common,
    )
    registry.save_connection(
        name="G14 UAT3 集中式对照",
        port=13306,
        database=CENTRAL_DB,
        is_default=False,
        is_distributed=False,
        conn_id="uat3_g14_central",
        description="Stock MySQL 8 centralized control",
        **common,
    )
    registry.save_connection(
        name="G14 UAT3 离线样本",
        host="127.0.0.1",
        port=1,
        username="synthetic",
        password="",
        database="offline",
        is_default=False,
        is_distributed=True,
        conn_id="uat3_g14_offline",
        operator="UAT-O-R3",
        description="Intentional unavailable endpoint for stale-request UAT",
    )

    print(
        "G14_UAT3_FIXTURES_READY "
        f"metadata={META_DB} users=2 connections=3 target={TARGET_DB}"
    )


if __name__ == "__main__":
    main()
