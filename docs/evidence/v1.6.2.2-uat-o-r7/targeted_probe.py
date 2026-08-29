"""Independent seventh-round probes for O-25/O-26 and upgrade boundaries."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PREPARE = HERE.parent / "v1.6.2.2-uat-o-r1" / "prepare_regression.py"
DB_NAME = "tdsql_uat_o_reg_r7_targeted_20260829"
os.environ.update(
    PYTHONUTF8="1",
    PYTHONIOENCODING="utf-8",
    AUTH_ENABLED="false",
    SCHEDULER_ENABLED="false",
    SQLCHECK_DB_NAME=DB_NAME,
)
os.environ.pop("SCHEMA_CHECKSUM_RECONCILE", None)

prep = subprocess.run(
    [sys.executable, str(PREPARE)], cwd=ROOT, env=os.environ,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
if prep.returncode:
    raise SystemExit(prep.returncode)
sys.path.insert(0, str(ROOT))

import pymysql
from fastapi.testclient import TestClient
from backend.main import app
from backend.schema.loader import discover_schema_files
from backend.schema.migrator import MigrationError, SchemaMigrator
from backend.services.connection_errors import translate_db_error
from backend.services.database import _get_connection


client = TestClient(app)


def mapped_name(exc):
    mapped = translate_db_error(exc)
    return type(mapped).__name__ if mapped else None


def api_status(exc):
    with patch("backend.services.connection_registry.registry.get_saved", side_effect=exc):
        response = client.post(
            "/api/v1/daily-inspect/run", json={"connection_id": "synthetic"})
    return {
        "status": response.status_code,
        "request_id": bool(response.headers.get("X-Request-ID")),
        "detail": response.json().get("detail"),
    }


program_cases = []
for exc in (
    RuntimeError("can't connect to internal cache"),
    AttributeError("connection refused while reading object"),
    TypeError("timed out during pickle decode"),
    RuntimeError("access denied to in-memory registry"),
    RuntimeError("unknown database handle in context"),
):
    program_cases.append({
        "exception": type(exc).__name__,
        "message": str(exc),
        "mapped": mapped_name(exc),
        "api": api_status(exc),
    })

driver_cases = []
for exc in (
    pymysql.err.OperationalError(2003, "Can't connect"),
    pymysql.err.OperationalError(1045, "Access denied"),
    pymysql.err.OperationalError(1049, "Unknown database"),
    ConnectionRefusedError("connection refused"),
    TimeoutError("timed out"),
):
    driver_cases.append({
        "exception": type(exc).__name__,
        "message": str(exc),
        "mapped": mapped_name(exc),
    })

generic_os_cases = []
for exc in (
    PermissionError("access denied reading encryption key"),
    FileNotFoundError("unknown database catalog file"),
):
    generic_os_cases.append({
        "exception": type(exc).__name__,
        "message": str(exc),
        "mapped": mapped_name(exc),
        "api": api_status(exc),
    })


def fake_schema_file(version, sequence, name, sql):
    return type("SchemaFile", (), {
        "version": version, "sequence": sequence, "name": name, "sql": sql,
    })()


def replace_version(key, checksum):
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM schema_migrations WHERE version_key=%s", (key,))
        conn.execute(
            "INSERT INTO schema_migrations(version_key,checksum) VALUES(%s,%s)",
            (key, checksum),
        )
        conn.commit()
    finally:
        conn.close()


def column_default(table, column):
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT COLUMN_DEFAULT FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
            (table, column),
        ).fetchone()
        return dict(row)["COLUMN_DEFAULT"] if row else None
    finally:
        conn.close()


# O-26: an explicit wrong type must now fail closed.
wrong_table = "o26_r7_wrong_type"
wrong_sql = (
    f"ALTER TABLE {wrong_table} ADD COLUMN related_index_name "
    "VARCHAR(128) DEFAULT '';"
)
wrong_file = fake_schema_file(99, 971, "o26_r7_wrong_type", wrong_sql)
wrong_key = "v99_971_o26_r7_wrong_type"
conn = _get_connection()
try:
    conn.execute(f"DROP TABLE IF EXISTS `{wrong_table}`")
    conn.execute(f"CREATE TABLE `{wrong_table}` (id INT PRIMARY KEY, related_index_name INT)")
    conn.commit()
finally:
    conn.close()
replace_version(wrong_key, hashlib.sha256(wrong_sql.encode()).hexdigest())
wrong_error = None
with patch("backend.schema.migrator.discover_schema_files", return_value=[wrong_file]):
    try:
        SchemaMigrator().run_migrations()
    except Exception as exc:
        wrong_error = f"{type(exc).__name__}: {exc}"


# Boundary: when the declaration omits DEFAULT, a pre-existing wrong default
# must also be considered a mismatch under the advertised strict contract.
default_table = "o26_r7_implicit_default"
default_sql = f"ALTER TABLE {default_table} ADD COLUMN note VARCHAR(32);"
default_file = fake_schema_file(99, 972, "o26_r7_implicit_default", default_sql)
default_key = "v99_972_o26_r7_implicit_default"
conn = _get_connection()
try:
    conn.execute(f"DROP TABLE IF EXISTS `{default_table}`")
    conn.execute(
        f"CREATE TABLE `{default_table}` "
        "(id INT PRIMARY KEY, note VARCHAR(32) DEFAULT 'unexpected')")
    conn.commit()
finally:
    conn.close()
replace_version(default_key, hashlib.sha256(default_sql.encode()).hexdigest())
default_error = None
with patch("backend.schema.migrator.discover_schema_files", return_value=[default_file]):
    try:
        SchemaMigrator().run_migrations()
    except Exception as exc:
        default_error = f"{type(exc).__name__}: {exc}"


# Upgrade boundary: a database that applied v9 before its intentional no-op
# rewrite carries the historical checksum and now fails startup unless an
# operator supplies the reconcile environment variable.
v9 = next(sf for sf in discover_schema_files()
          if f"v{sf.version}_{sf.sequence:03d}_{sf.name}" == "v9_090_connection_unique")
v9_key = "v9_090_connection_unique"
v9_current = hashlib.sha256(v9.sql.encode()).hexdigest()
v9_historical = "54ee2e97c804f5d8ec216d9f51600c19cc8463f2cede1de07fa67635abe6de28"
replace_version(v9_key, v9_historical)
upgrade_without_env = None
try:
    SchemaMigrator().run_migrations()
except Exception as exc:
    upgrade_without_env = f"{type(exc).__name__}: {exc}"

os.environ["SCHEMA_CHECKSUM_RECONCILE"] = v9_key
upgrade_with_env = None
try:
    SchemaMigrator().run_migrations()
except Exception as exc:
    upgrade_with_env = f"{type(exc).__name__}: {exc}"
conn = _get_connection()
try:
    row = conn.execute(
        "SELECT checksum FROM schema_migrations WHERE version_key=%s", (v9_key,)
    ).fetchone()
    v9_after = dict(row)["checksum"] if row else None
finally:
    conn.close()


# If the one-time environment variable remains in the persistent deployment
# .env, it is not bound to old/new checksums. A later arbitrary change to the
# same no-column migration is accepted and re-baselined.
tampered_sql = "SELECT 'unexpected future v9 content';"
tampered_file = fake_schema_file(9, 90, "connection_unique", tampered_sql)
tampered_checksum = hashlib.sha256(tampered_sql.encode()).hexdigest()
reconcile_tamper_error = None
with patch("backend.schema.migrator.discover_schema_files", return_value=[tampered_file]):
    try:
        SchemaMigrator().run_migrations()
    except Exception as exc:
        reconcile_tamper_error = f"{type(exc).__name__}: {exc}"
conn = _get_connection()
try:
    row = conn.execute(
        "SELECT checksum FROM schema_migrations WHERE version_key=%s", (v9_key,)
    ).fetchone()
    checksum_after_tamper = dict(row)["checksum"] if row else None
finally:
    conn.close()
os.environ.pop("SCHEMA_CHECKSUM_RECONCILE", None)


result = {
    "tested_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "o25_program_cases": program_cases,
    "o25_driver_cases": driver_cases,
    "o25_generic_os_boundary": generic_os_cases,
    "o26_wrong_type": {
        "migration_error": wrong_error,
        "failed_closed": wrong_error is not None,
    },
    "o26_implicit_default_boundary": {
        "migration_error": default_error,
        "actual_default": column_default(default_table, "note"),
        "failed_closed": default_error is not None,
    },
    "o26_historical_v9_upgrade": {
        "historical_checksum": v9_historical,
        "current_checksum": v9_current,
        "without_reconcile_env": upgrade_without_env,
        "with_reconcile_env": upgrade_with_env,
        "checksum_after_reconcile": v9_after,
    },
    "o26_persistent_reconcile_boundary": {
        "migration_error": reconcile_tamper_error,
        "tampered_checksum": tampered_checksum,
        "checksum_after_run": checksum_after_tamper,
        "tamper_rebaselined": checksum_after_tamper == tampered_checksum,
    },
}

(HERE / "targeted_probe.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2),
    encoding="utf-8", newline="\n",
)
print(json.dumps(result, ensure_ascii=False))


# Remove synthetic tables/rows, leaving the isolated DB internally consistent.
conn = _get_connection()
try:
    conn.execute(f"DROP TABLE IF EXISTS `{wrong_table}`")
    conn.execute(f"DROP TABLE IF EXISTS `{default_table}`")
    conn.execute("DELETE FROM schema_migrations WHERE version_key IN (%s,%s)",
                 (wrong_key, default_key))
    conn.execute("UPDATE schema_migrations SET checksum=%s WHERE version_key=%s",
                 (v9_current, v9_key))
    conn.commit()
finally:
    conn.close()
