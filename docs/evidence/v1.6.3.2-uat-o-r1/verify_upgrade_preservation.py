# -*- coding: utf-8 -*-
"""UAT-O-R1: prove the v1.6.3.2 rule-catalog upgrade is additive.

The fixture simulates an existing installation by removing the two new catalog
rows and preserving a custom R011 catalog value plus an active ruleset override.
Running ``init_rule_configs`` must add only R120/R121 and leave both existing
values untouched.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


META_DB = "tdsql_uat_o_1632_upgrade"
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def main() -> None:
    if os.environ.get("SQLCHECK_DB_NAME") != META_DB:
        raise SystemExit(f"Refusing metadata database other than {META_DB}")

    from backend.services.database import _get_connection, ensure_db, init_rule_configs

    ensure_db()
    init_rule_configs()
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM rule_configs WHERE rule_id IN ('R120','R121')")
        conn.execute(
            "UPDATE rule_configs SET enabled=0, severity='WARNING', "
            "description='UAT-PRESERVE-R011' WHERE rule_id='R011'"
        )
        conn.execute(
            "INSERT INTO rule_sets(id,name,description,is_builtin,created_by) "
            "VALUES('uat1632_active','UAT active ruleset','upgrade preservation',0,'UAT-O-R1') "
            "ON DUPLICATE KEY UPDATE name=VALUES(name)"
        )
        conn.execute(
            "INSERT INTO rule_set_items(rule_set_id,rule_id,enabled,severity_override) "
            "VALUES('uat1632_active','R011',0,'ERROR') "
            "ON DUPLICATE KEY UPDATE enabled=VALUES(enabled), "
            "severity_override=VALUES(severity_override)"
        )
        conn.execute(
            "REPLACE INTO system_config(config_key,config_value) "
            "VALUES('active_rule_set_id','uat1632_active')"
        )
        conn.commit()
    finally:
        conn.close()

    init_rule_configs()
    init_rule_configs()

    conn = _get_connection()
    try:
        catalog = conn.execute(
            "SELECT rule_id,severity,description,enabled FROM rule_configs "
            "WHERE rule_id IN ('R011','R120','R121') ORDER BY rule_id"
        ).fetchall()
        override = conn.execute(
            "SELECT rule_id,enabled,severity_override FROM rule_set_items "
            "WHERE rule_set_id='uat1632_active' AND rule_id='R011'"
        ).fetchone()
        total = conn.execute("SELECT COUNT(*) AS n FROM rule_configs").fetchone()["n"]
    finally:
        conn.close()

    by_id = {row["rule_id"]: dict(row) for row in catalog}
    assert total == 121, total
    assert set(by_id) == {"R011", "R120", "R121"}, by_id
    assert by_id["R011"] == {
        "rule_id": "R011",
        "severity": "WARNING",
        "description": "UAT-PRESERVE-R011",
        "enabled": 0,
    }, by_id["R011"]
    assert dict(override) == {
        "rule_id": "R011",
        "enabled": 0,
        "severity_override": "ERROR",
    }, dict(override)
    print("[PASS] rule_configs total=121; R120/R121 inserted exactly once")
    print("[PASS] existing R011 catalog values preserved by INSERT IGNORE")
    print("[PASS] active ruleset R011 enabled/severity override preserved")


if __name__ == "__main__":
    main()
