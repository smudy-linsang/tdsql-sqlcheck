"""Prepare synthetic local UAT fixtures; never run against a business database."""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def main():
    db = os.environ.get("SQLCHECK_DB_NAME", "")
    if not db.startswith("tdsql_uat_o_"):
        raise SystemExit("Refusing non-UAT metadata database")
    password = os.environ["UAT_O_PASSWORD"]
    from backend.services.database import ensure_db, _get_connection, MYSQL_CONFIG
    from backend.services.auth_service import auth_service, set_role_permissions
    from backend.services.connection_registry import registry
    import pymysql

    ensure_db()
    for role in ("dba", "developer", "auditor"):
        username = "uat_o_" + role
        if not auth_service.get_user(username, use_cache=False):
            _, error = auth_service.create_user(username, password, role, "UAT-O " + role, "UAT-O fixture")
            if error:
                raise RuntimeError(error)
        conn = _get_connection()
        try:
            conn.execute("UPDATE users SET must_change_password=0 WHERE username=?", (username,))
            conn.commit()
        finally:
            conn.close()
    # Developer has business menus but no instance-management menu.
    set_role_permissions("developer", {"instances": False})
    target_db = "tdsql_uat_o_target_1622"
    cfg = {k: v for k, v in MYSQL_CONFIG.items() if k != "database"}
    raw = pymysql.connect(**cfg)
    try:
        with raw.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{target_db}` CHARACTER SET utf8mb4")
            cursor.execute(f"CREATE TABLE IF NOT EXISTS `{target_db}`.t_uat_customer (id BIGINT NOT NULL COMMENT 'id', cust_no VARCHAR(32) NOT NULL COMMENT 'customer', customer_name VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'name', PRIMARY KEY(id), UNIQUE KEY uk_customer(cust_no) COMMENT 'UAT unique comment') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='synthetic UAT fixture'")
            cursor.execute(f"CREATE TABLE IF NOT EXISTS `{target_db}`.t_uat_order (id BIGINT NOT NULL COMMENT 'id', customer_id BIGINT NOT NULL COMMENT 'customer', amount DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT 'amount', PRIMARY KEY(id), KEY idx_customer(customer_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='synthetic UAT fixture'")
        raw.commit()
    finally:
        raw.close()
    registry.save_connection(name="UAT-O 本地集中式样本", host=MYSQL_CONFIG["host"], port=MYSQL_CONFIG["port"], username=MYSQL_CONFIG["user"], password=MYSQL_CONFIG["password"], database=target_db, is_default=True, is_distributed=False, conn_id="uat_o_local", operator="UAT-O", description="Local MySQL-protocol functional fixture; NOT a TDSQL compatibility certification")
    registry.save_connection(name="UAT-O 离线失败样本", host="127.0.0.1", port=1, username="synthetic", password="", database="uat_o_offline", is_distributed=True, conn_id="uat_o_offline", operator="UAT-O", description="Intentional unavailable endpoint for error-path UAT")
    print("UAT_FIXTURES_READY db=" + db + " users=3 connections=2 target_tables=2")


if __name__ == "__main__":
    main()
