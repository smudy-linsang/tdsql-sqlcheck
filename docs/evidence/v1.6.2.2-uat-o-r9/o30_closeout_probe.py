"""Independent UAT round-nine close-out probe for O-30 only."""
import json
import logging
import os
from pathlib import Path
import sys
import threading


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

if os.environ.get("SQLCHECK_DB_NAME") != "tdsql_uat_o_r9_1622_20260830":
    raise SystemExit("Refusing non-round-nine database")

from backend.schema.migrator import MigrationError, SchemaMigrator
from backend.services.database import _get_connection, ensure_db


KEY = "v9_090_connection_unique"
OLD = "54ee2e97c804f5d8ec216d9f51600c19cc8463f2cede1de07fa67635abe6de28"
NEW = "c6cf33bb385456fef12af3d4888ea6b22dcfc2a64052d734adc4c37457915209"


def ledger_checksum():
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE version_key=%s", (KEY,)).fetchone()
        return dict(row)["checksum"] if row else None
    finally:
        conn.close()


def set_checksum(value):
    conn = _get_connection()
    try:
        if ledger_checksum() is None:
            conn.execute(
                "INSERT INTO schema_migrations(version_key, checksum) VALUES(%s,%s)",
                (KEY, value),
            )
        else:
            conn.execute(
                "UPDATE schema_migrations SET checksum=%s WHERE version_key=%s",
                (value, KEY),
            )
        conn.commit()
    finally:
        conn.close()


def audit_rows():
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT id, operator, operation_type, target_type, target_id, detail "
            "FROM operation_logs WHERE operation_type=%s AND target_id=%s ORDER BY id",
            ("schema_checksum_reconcile", KEY),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append({"level": record.levelname, "message": record.getMessage()})


ensure_db()
saved = ledger_checksum()
results = {"tested_commit": "ddf5e6464a5e600c5c08d004a8a7352c93cd4f08"}
logger = logging.getLogger("tdsql.schema.migrator")
capture = Capture()
logger.addHandler(capture)
logger.setLevel(logging.INFO)

try:
    # 1. Single worker: exact historical checksum reconciles and writes exactly one audit.
    set_checksum(OLD)
    before_single = len(audit_rows())
    SchemaMigrator().run_migrations()
    single_rows = audit_rows()
    single_new = single_rows[before_single:]
    results["single_worker"] = {
        "checksum": ledger_checksum(),
        "audit_delta": len(single_new),
        "audit": single_new,
        "error_log_count": sum(
            1 for row in capture.messages
            if row["level"] == "ERROR" and "一次性自动调和完成" in row["message"]
            and "审计已落库" in row["message"]),
    }

    # 2. Second startup: current checksum skips reconciliation and writes no audit.
    before_second = len(audit_rows())
    SchemaMigrator().run_migrations()
    results["second_startup"] = {
        "checksum": ledger_checksum(),
        "audit_delta": len(audit_rows()) - before_second,
    }

    # 3. Audit insert failure: UPDATE and audit must roll back together.
    set_checksum(OLD)
    before_failure = len(audit_rows())
    failure_error = None
    failing = SchemaMigrator()

    def fail_audit(cursor, key, recorded, current, reason):
        raise RuntimeError("synthetic audit write failure")

    failing._insert_reconcile_audit = fail_audit
    conn = _get_connection()
    try:
        try:
            failing._auto_reconcile(conn.cursor(), conn, KEY, OLD, NEW)
        except MigrationError as exc:
            failure_error = str(exc)
    finally:
        conn.close()
    results["audit_failure"] = {
        "migration_error": failure_error,
        "checksum_after": ledger_checksum(),
        "audit_delta": len(audit_rows()) - before_failure,
    }

    # 4. Two workers: both return successfully, but exactly one audit is inserted.
    set_checksum(OLD)
    before_concurrent = len(audit_rows())
    worker_results = []
    worker_errors = []

    def worker():
        conn = _get_connection()
        try:
            SchemaMigrator()._auto_reconcile(conn.cursor(), conn, KEY, OLD, NEW)
            worker_results.append("ok")
        except Exception as exc:  # noqa: BLE001
            worker_errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    results["two_workers"] = {
        "results": worker_results,
        "errors": worker_errors,
        "checksum": ledger_checksum(),
        "audit_delta": len(audit_rows()) - before_concurrent,
    }

    # 5. A later unknown checksum remains fail-closed; the removed legacy variable
    # cannot override the exact ledger.
    os.environ["SCHEMA_CHECKSUM_RECONCILE"] = KEY
    before_tamper = len(audit_rows())
    tamper_error = None
    conn = _get_connection()
    try:
        try:
            SchemaMigrator()._auto_reconcile(
                conn.cursor(), conn, KEY, NEW, "tampered-after-reconcile")
        except MigrationError as exc:
            tamper_error = str(exc)
    finally:
        conn.close()
    results["future_drift"] = {
        "migration_error": tamper_error,
        "checksum_after": ledger_checksum(),
        "audit_delta": len(audit_rows()) - before_tamper,
    }
finally:
    logger.removeHandler(capture)
    os.environ.pop("SCHEMA_CHECKSUM_RECONCILE", None)
    if saved is not None:
        set_checksum(saved)

ok = (
    results["single_worker"]["checksum"] == NEW
    and results["single_worker"]["audit_delta"] == 1
    and results["single_worker"]["error_log_count"] == 1
    and results["second_startup"]["checksum"] == NEW
    and results["second_startup"]["audit_delta"] == 0
    and results["audit_failure"]["migration_error"] is not None
    and results["audit_failure"]["checksum_after"] == OLD
    and results["audit_failure"]["audit_delta"] == 0
    and sorted(results["two_workers"]["results"]) == ["ok", "ok"]
    and not results["two_workers"]["errors"]
    and results["two_workers"]["checksum"] == NEW
    and results["two_workers"]["audit_delta"] == 1
    and results["future_drift"]["migration_error"] is not None
    and results["future_drift"]["checksum_after"] == NEW
    and results["future_drift"]["audit_delta"] == 0
)
results["result"] = "PASS" if ok else "FAIL"
(HERE / "o30_closeout_probe.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print(json.dumps(results, ensure_ascii=False, indent=2))
raise SystemExit(0 if ok else 1)
