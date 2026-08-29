"""Independent close-out probe for UAT round-eight O-28/O-29/O-30 only."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

if os.environ.get("SQLCHECK_DB_NAME") != "tdsql_uat_o_r8_1622_20260830":
    raise SystemExit("Refusing non-round-eight database")

from fastapi.testclient import TestClient
import pymysql

from backend.main import app
from backend.schema.migrator import MigrationError, SchemaMigrator
from backend.services.connection_errors import (
    AuthenticationFailedError,
    ConnectionRefusedError_,
    DatabaseNotFoundError,
    translate_db_error,
)
from backend.services.database import _get_connection, ensure_db


OLD = "54ee2e97c804f5d8ec216d9f51600c19cc8463f2cede1de07fa67635abe6de28"
NEW = "c6cf33bb385456fef12af3d4888ea6b22dcfc2a64052d734adc4c37457915209"
KEY = "v9_090_connection_unique"
TABLE = "uat_o_r8_default_probe"
results = {"tested_commit": "d40cf739420be984a2805253ba671c890fe17c66"}


ensure_db()

# O-28: the two original counterexamples must be 500, while supported driver/network
# failures retain their readable domain mapping.
client = TestClient(app, raise_server_exceptions=False)
o28 = []
for exc in (
    PermissionError("access denied reading encryption key"),
    FileNotFoundError("unknown database catalog file"),
):
    mapped = translate_db_error(exc)
    with patch("backend.services.connection_registry.registry.get_saved", side_effect=exc):
        response = client.post(
            "/api/v1/daily-inspect/run",
            json={"connection_id": "uat-o-r8-offline", "inspect_date": "2026-08-30"},
        )
    o28.append({
        "exception": type(exc).__name__,
        "mapped": type(mapped).__name__ if mapped else None,
        "status": response.status_code,
        "request_id": bool(response.headers.get("X-Request-ID")),
    })
driver = [
    (pymysql.err.OperationalError(2003, "Can't connect"), ConnectionRefusedError_),
    (pymysql.err.OperationalError(1045, "Access denied"), AuthenticationFailedError),
    (pymysql.err.OperationalError(1049, "Unknown database"), DatabaseNotFoundError),
    (ConnectionRefusedError("connection refused"), ConnectionRefusedError_),
    (TimeoutError("timed out"), ConnectionRefusedError_),
]
results["o28"] = {
    "original_counterexamples": o28,
    "supported_mappings": [
        {"exception": type(exc).__name__, "mapped": type(translate_db_error(exc)).__name__,
         "expected": expected.__name__, "pass": isinstance(translate_db_error(exc), expected)}
        for exc, expected in driver
    ],
}

# O-29: reproduce the exact missing-DEFAULT mismatch, then prove the compliant
# no-default structure remains valid.
conn = _get_connection()
try:
    cursor = conn.cursor()
    cursor.execute(f"DROP TABLE IF EXISTS `{TABLE}`")
    cursor.execute(f"CREATE TABLE `{TABLE}` (id INT PRIMARY KEY, note VARCHAR(32) DEFAULT 'unexpected')")
    conn.commit()
    migration = f"ALTER TABLE {TABLE} ADD COLUMN note VARCHAR(32)"
    mismatch_error = None
    try:
        SchemaMigrator()._structure_state(cursor, "uat_o_r8_o29", [migration])
    except MigrationError as exc:
        mismatch_error = str(exc)
    cursor.execute(f"DROP TABLE `{TABLE}`")
    cursor.execute(f"CREATE TABLE `{TABLE}` (id INT PRIMARY KEY, note VARCHAR(32))")
    conn.commit()
    valid_state = SchemaMigrator()._structure_state(cursor, "uat_o_r8_o29", [migration])
    results["o29"] = {
        "wrong_default_failed_closed": mismatch_error is not None,
        "error": mismatch_error,
        "matching_no_default_state": valid_state,
    }
finally:
    try:
        conn.execute(f"DROP TABLE IF EXISTS `{TABLE}`")
        conn.commit()
    finally:
        conn.close()

# O-30: simulate an actual v1.6.2.1 ledger, run production migration discovery
# without any switch, prove one-time automatic reconciliation and idempotence,
# then prove a later unknown checksum is rejected even when the removed legacy
# environment variable is present.
rowcount_conn = _get_connection()
try:
    rowcount_conn.execute("DROP TABLE IF EXISTS uat_o_r8_rowcount_probe")
    rowcount_conn.execute(
        "CREATE TABLE uat_o_r8_rowcount_probe (id INT PRIMARY KEY, value_col INT)")
    rowcount_conn.execute(
        "INSERT INTO uat_o_r8_rowcount_probe(id, value_col) VALUES(1, 0)")
    rowcount_conn.commit()
    rowcount_cursor = rowcount_conn.cursor()
    execute_return = rowcount_cursor.execute(
        "UPDATE uat_o_r8_rowcount_probe SET value_col=1 WHERE id=1 AND value_col=0")
    rowcount_contract = {
        "execute_return_type": type(execute_return).__name__,
        "execute_return_value": execute_return,
        "cursor_rowcount": rowcount_cursor.rowcount,
    }
    rowcount_conn.rollback()
finally:
    rowcount_conn.execute("DROP TABLE IF EXISTS uat_o_r8_rowcount_probe")
    rowcount_conn.commit()
    rowcount_conn.close()

conn = _get_connection()
try:
    row = conn.execute(
        "SELECT checksum FROM schema_migrations WHERE version_key=%s", (KEY,)).fetchone()
    saved = dict(row)["checksum"] if row else None
    initial_audit = conn.execute(
        "SELECT COUNT(*) AS c FROM operation_logs WHERE operation_type=%s AND target_id=%s",
        ("schema_checksum_reconcile", KEY),
    ).fetchone()
    initial_audit_count = int(dict(initial_audit)["c"])
    if row:
        conn.execute(
            "UPDATE schema_migrations SET checksum=%s WHERE version_key=%s", (OLD, KEY))
    else:
        conn.execute(
            "INSERT INTO schema_migrations(version_key, checksum) VALUES(%s,%s)", (KEY, OLD))
    conn.commit()
finally:
    conn.close()

try:
    os.environ.pop("SCHEMA_CHECKSUM_RECONCILE", None)
    SchemaMigrator().run_migrations()
    conn = _get_connection()
    try:
        first = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE version_key=%s", (KEY,)).fetchone()
        first_checksum = dict(first)["checksum"]
        before_second = conn.execute(
            "SELECT COUNT(*) AS c FROM operation_logs WHERE operation_type=%s AND target_id=%s",
            ("schema_checksum_reconcile", KEY),
        ).fetchone()
        before_second_count = int(dict(before_second)["c"])
    finally:
        conn.close()
    SchemaMigrator().run_migrations()
    conn = _get_connection()
    try:
        second = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE version_key=%s", (KEY,)).fetchone()
        second_checksum = dict(second)["checksum"]
        after_second = conn.execute(
            "SELECT COUNT(*) AS c FROM operation_logs WHERE operation_type=%s AND target_id=%s",
            ("schema_checksum_reconcile", KEY),
        ).fetchone()
        after_second_count = int(dict(after_second)["c"])
    finally:
        conn.close()
    os.environ["SCHEMA_CHECKSUM_RECONCILE"] = KEY
    unknown_error = None
    conn = _get_connection()
    try:
        try:
            SchemaMigrator()._auto_reconcile(
                conn.cursor(), conn, KEY, NEW, "tampered-after-reconcile")
        except MigrationError as exc:
            unknown_error = str(exc)
        final = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE version_key=%s", (KEY,)).fetchone()
        final_checksum = dict(final)["checksum"]
    finally:
        conn.close()
    results["o30"] = {
        "cursor_execute_contract": rowcount_contract,
        "legacy_env_absent_during_upgrade": True,
        "first_checksum": first_checksum,
        "second_checksum": second_checksum,
        "audit_count_before_upgrade": initial_audit_count,
        "audit_count_before_second": before_second_count,
        "audit_count_after_second": after_second_count,
        "exactly_one_new_audit": before_second_count == initial_audit_count + 1,
        "idempotent_no_extra_audit": before_second_count == after_second_count,
        "unknown_future_checksum_failed_closed": unknown_error is not None,
        "unknown_error": unknown_error,
        "legacy_env_could_not_override": final_checksum == NEW,
        "final_checksum": final_checksum,
    }
finally:
    os.environ.pop("SCHEMA_CHECKSUM_RECONCILE", None)
    conn = _get_connection()
    try:
        if saved is None:
            conn.execute("DELETE FROM schema_migrations WHERE version_key=%s", (KEY,))
        else:
            conn.execute(
                "UPDATE schema_migrations SET checksum=%s WHERE version_key=%s", (saved, KEY))
        conn.commit()
    finally:
        conn.close()

ok = (
    all(row["mapped"] is None and row["status"] == 500 and row["request_id"]
        for row in results["o28"]["original_counterexamples"])
    and all(row["pass"] for row in results["o28"]["supported_mappings"])
    and results["o29"]["wrong_default_failed_closed"]
    and results["o29"]["matching_no_default_state"] == "valid"
    and results["o30"]["first_checksum"] == NEW
    and results["o30"]["second_checksum"] == NEW
    and results["o30"]["exactly_one_new_audit"]
    and results["o30"]["idempotent_no_extra_audit"]
    and results["o30"]["unknown_future_checksum_failed_closed"]
    and results["o30"]["legacy_env_could_not_override"]
)
results["result"] = "PASS" if ok else "FAIL"
(HERE / "targeted_closeout_probe.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print(json.dumps(results, ensure_ascii=False, indent=2))
raise SystemExit(0 if ok else 1)
