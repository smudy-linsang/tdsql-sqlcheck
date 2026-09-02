"""Prepare isolated local fixtures for v1.6.3.0 G14 UAT round 2.

The script refuses to touch a metadata database outside the dedicated UAT name.
It creates only synthetic users, roles, databases, tables and connection entries.
"""
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

META_DB = "tdsql_uat_o_g14_r2_1630"
TARGET_A = "tdsql_uat_g14_r2_a"
TARGET_B = "tdsql_uat_g14_r2_b"
TARGET_EMPTY = "tdsql_uat_g14_r2_empty"


def main():
    if os.environ.get("SQLCHECK_DB_NAME") != META_DB:
        raise SystemExit(f"Refusing metadata database other than {META_DB}")
    password = os.environ["UAT_G14_PASSWORD"]

    from backend.services.auth_service import (
        auth_service,
        create_custom_role,
        set_role_permissions,
    )
    from backend.services.connection_registry import registry
    from backend.services.database import MYSQL_CONFIG, _get_connection, ensure_db
    import pymysql

    ensure_db()
    auth_service.ensure_bootstrap_admin()

    for role in ("dba", "developer", "auditor"):
        username = f"uat2_g14_{role}"
        if not auth_service.get_user(username, use_cache=False):
            _, error = auth_service.create_user(
                username, password, role, f"G14 UAT2 {role}", "G14 UAT2 fixture")
            if error:
                raise RuntimeError(error)

    role_id = "uat2_g14_tabletype_only"
    result = create_custom_role(
        role_id, "G14 表类型统计最小权限（UAT2）", "仅可进入深度诊断表类型统计")
    if result.get("error") and "已存在" not in result["error"]:
        raise RuntimeError(result["error"])
    set_role_permissions(role_id, {
        "dashboard": True,
        "deep-diag": True,
        "deep-diag-tabletype": True,
        "instances": False,
    })
    if not auth_service.get_user("uat2_g14_tt", use_cache=False):
        _, error = auth_service.create_user(
            "uat2_g14_tt", password, role_id,
            "G14 UAT2 最小权限", "G14 UAT2 fixture")
        if error:
            raise RuntimeError(error)

    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE users SET must_change_password=0, status='active' "
            "WHERE username IN ('admin','uat2_g14_dba','uat2_g14_developer',"
            "'uat2_g14_auditor','uat2_g14_tt')")
        conn.commit()
    finally:
        conn.close()

    raw_cfg = {k: v for k, v in MYSQL_CONFIG.items() if k != "database"}
    raw = pymysql.connect(**raw_cfg)
    try:
        with raw.cursor() as cur:
            for name in (TARGET_A, TARGET_B, TARGET_EMPTY):
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4")
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{TARGET_A}`.customer ("
                "id BIGINT PRIMARY KEY, name VARCHAR(64) NOT NULL) ENGINE=InnoDB")
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{TARGET_A}`.orders ("
                "id BIGINT PRIMARY KEY, customer_id BIGINT NOT NULL) ENGINE=InnoDB")
            cur.execute(
                f"CREATE OR REPLACE VIEW `{TARGET_A}`.v_customer AS "
                f"SELECT id,name FROM `{TARGET_A}`.customer")
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS `{TARGET_B}`.inventory ("
                "id BIGINT PRIMARY KEY, qty INT NOT NULL DEFAULT 0) ENGINE=InnoDB")
        raw.commit()
    finally:
        raw.close()

    shared = dict(
        host=MYSQL_CONFIG["host"], port=MYSQL_CONFIG["port"],
        username=MYSQL_CONFIG["user"], password=MYSQL_CONFIG["password"],
        charset="utf8mb4", operator="UAT-O-R2",
    )
    registry.save_connection(
        name="G14 UAT2 集中式样本", database=TARGET_A, is_default=True,
        is_distributed=False, conn_id="uat2_g14_central",
        description="Synthetic MariaDB centralized UAT round-2 fixture", **shared)
    registry.save_connection(
        name="G14 UAT2 分布式失败样本", database=TARGET_B, is_default=False,
        is_distributed=True, conn_id="uat2_g14_distributed",
        description="Synthetic MariaDB forced-distributed error-path fixture", **shared)
    registry.save_connection(
        name="G14 UAT2 离线样本", host="127.0.0.1", port=1,
        username="synthetic", password="", database="offline", is_default=False,
        is_distributed=True, conn_id="uat2_g14_offline", operator="UAT-O-R2",
        description="Intentional unavailable endpoint for UAT round 2")

    print(
        "G14_UAT2_FIXTURES_READY "
        f"metadata={META_DB} users=5 connections=3 "
        f"targets={TARGET_A}:2+1view,{TARGET_B}:1,{TARGET_EMPTY}:0")


if __name__ == "__main__":
    main()
