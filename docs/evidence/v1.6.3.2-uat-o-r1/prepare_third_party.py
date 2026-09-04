# -*- coding: utf-8 -*-
"""Prepare the isolated HTTP service consumed by ``tests_3p``."""
from __future__ import annotations

import os
import sys
from pathlib import Path


META_DB = "tdsql_uat_o_1632_3p"
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def main() -> None:
    if os.environ.get("SQLCHECK_DB_NAME") != META_DB:
        raise SystemExit(f"Refusing metadata database other than {META_DB}")
    password = os.environ["T3P_ADMIN_PASSWORD"]

    from backend.services.auth_service import auth_service
    from backend.services.database import _get_connection, ensure_db, init_rule_configs

    ensure_db()
    init_rule_configs()
    auth_service.ensure_bootstrap_admin()
    error = auth_service.reset_password("admin", password, operator="UAT-O-R1")
    if error:
        raise RuntimeError(error)

    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE users SET must_change_password=0, status='active', failed_attempts=0, "
            "locked_until=NULL WHERE username='admin'"
        )
        conn.commit()
        counts = {
            "rules": conn.execute("SELECT COUNT(*) AS n FROM rule_configs").fetchone()["n"],
            "users": conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"],
        }
    finally:
        conn.close()
    print(f"Prepared {META_DB}: {counts}")


if __name__ == "__main__":
    main()
