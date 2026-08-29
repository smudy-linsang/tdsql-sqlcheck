"""Independent sixth-round probes for the three repaired defects and boundaries."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
if not os.environ.get("SQLCHECK_DB_NAME", "").startswith("tdsql_uat_o_reg_r6_"):
    raise SystemExit("Isolated round-six diagnostic database required")

from fastapi import HTTPException
from backend.api.daily_inspect import DailyRequest, run as daily_run
from backend.engine.checker import RuleChecker
from backend.schema.migrator import SchemaMigrator
from backend.services.connection_errors import translate_db_error
from backend.services.database import _get_connection, ensure_db
from backend.services.gateway_log_service import gateway_log_service

ensure_db()
checker = RuleChecker()
audit_rows = []
for label, sql in {
    "cr_malformed_view": "-- ordinary\rCREATE VIEW v AS SELECT 1 +",
    "tdsql_hash_table": (
        "CREATE TABLE cus_bas_corp_contact (ID varchar(64) NOT NULL, "
        "CUST_NO varchar(20) NOT NULL, PRIMARY KEY (ID,CUST_NO)) "
        "ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(CUST_NO)"),
    "tdsql_broadcast": (
        "CREATE TABLE cus_name_list_type (ID varchar(64) NOT NULL, PRIMARY KEY(ID)) "
        "ENGINE=InnoDB shardkey=noshardkey_allset"),
}.items():
    result = checker.audit_sql(sql, instance_type="distributed")
    audit_rows.append({"label": label, "passed": result.passed,
                       "fired": sorted(v.rule_id for v in result.violations)})


async def injected_status(message):
    with patch("backend.api.daily_inspect.registry.get", side_effect=RuntimeError(message)):
        try:
            await daily_run(DailyRequest(connection_id="synthetic"))
        except HTTPException as exc:
            return {"status": exc.status_code, "detail": exc.detail}
    return {"status": 200, "detail": "unexpected success"}


translation_cases = []
for exc in (
    RuntimeError("synthetic programming defect"),
    RuntimeError("can't connect to internal cache"),
    AttributeError("connection refused while reading object"),
    Exception(2003, "Can't connect to MySQL server"),
):
    mapped = translate_db_error(exc)
    translation_cases.append({
        "exception": type(exc).__name__, "message": str(exc),
        "mapped": type(mapped).__name__ if mapped else None,
    })

# Applied migration with an existing but structurally wrong column. The advertised
# "every startup strict structure validation" must not silently skip this state.
probe_table = "o23_r6_existing_wrong"
probe_key = "v99_997_o23_r6_existing_wrong"
probe_sql = (
    f"ALTER TABLE {probe_table} ADD COLUMN related_index_name "
    "VARCHAR(128) DEFAULT '';"
)
probe_checksum = hashlib.sha256(probe_sql.encode("utf-8")).hexdigest()
conn = _get_connection()
try:
    conn.execute(f"DROP TABLE IF EXISTS `{probe_table}`")
    conn.execute(f"CREATE TABLE `{probe_table}` (id INT PRIMARY KEY, related_index_name INT)")
    conn.execute("DELETE FROM schema_migrations WHERE version_key=?", (probe_key,))
    conn.execute(
        "INSERT INTO schema_migrations(version_key,checksum) VALUES(?,?)",
        (probe_key, probe_checksum),
    )
    conn.commit()
finally:
    conn.close()

schema_file = type("SchemaFile", (), {
    "version": 99, "sequence": 997, "name": "o23_r6_existing_wrong", "sql": probe_sql,
})()
migration_error = None
with patch("backend.schema.migrator.discover_schema_files", return_value=[schema_file]):
    try:
        SchemaMigrator().run_migrations()
    except Exception as exc:
        migration_error = f"{type(exc).__name__}: {exc}"
conn = _get_connection()
try:
    row = conn.execute(
        "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=? AND COLUMN_NAME='related_index_name'",
        (probe_table,),
    ).fetchone()
    actual_type = dict(row)["COLUMN_TYPE"] if row else None
finally:
    conn.close()

ticket = gateway_log_service.create_report_ticket(601, "synthetic-user")
first = gateway_log_service.consume_report_ticket(ticket, 601)
replay = gateway_log_service.consume_report_ticket(ticket, 601)

result = {
    "audit_cases": audit_rows,
    "unknown_neutral": asyncio.run(injected_status("synthetic programming defect")),
    "unknown_connection_wording": asyncio.run(injected_status("can't connect to internal cache")),
    "translation_cases": translation_cases,
    "applied_wrong_structure": {
        "migration_error": migration_error,
        "actual_type_after_startup": actual_type,
        "strict_validation_happened": migration_error is not None,
    },
    "ticket_semantics": {"first": first, "replay": replay},
}

conn = _get_connection()
try:
    conn.execute(f"DROP TABLE IF EXISTS `{probe_table}`")
    conn.execute("DELETE FROM schema_migrations WHERE version_key=?", (probe_key,))
    conn.commit()
finally:
    conn.close()

(HERE / "targeted_probe.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print(json.dumps(result, ensure_ascii=False))
