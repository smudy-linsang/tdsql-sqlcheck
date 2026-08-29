"""Independent fifth-round probes for repaired paths and secondary-damage boundaries."""
import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
if not os.environ.get("SQLCHECK_DB_NAME", "").startswith("tdsql_uat_o_reg_r5_"):
    raise SystemExit("Isolated round-five diagnostic database required")

from fastapi import HTTPException
from backend.api.daily_inspect import DailyRequest, run as daily_run
from backend.engine.checker import RuleChecker
from backend.services.connection_errors import translate_db_error
from backend.services.gateway_log_service import gateway_log_service
from backend.services.slow_query_service import SlowQueryService


checker = RuleChecker()
sql_cases = {
    "cr_malformed_view": "-- ordinary\rCREATE VIEW v AS SELECT 1 +",
    "valid_view": "CREATE VIEW v AS SELECT 1",
    "valid_procedure": "CREATE PROCEDURE p() BEGIN SELECT 1; END",
    "tdsql_hash_table": (
        "CREATE TABLE cus_bas_corp_contact (ID varchar(64) NOT NULL, "
        "CUST_NO varchar(20) NOT NULL, PRIMARY KEY (ID,CUST_NO)) "
        "ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(CUST_NO)"),
    "tdsql_broadcast": (
        "CREATE TABLE cus_name_list_type (ID varchar(64) NOT NULL, PRIMARY KEY(ID)) "
        "ENGINE=InnoDB shardkey=noshardkey_allset"),
}
audit_rows = []
for label, sql in sql_cases.items():
    parsed = checker.parser.parse(sql)
    result = checker.audit_sql(sql, instance_type="distributed")
    audit_rows.append({
        "label": label, "passed": result.passed,
        "fired": sorted(v.rule_id for v in result.violations),
        "parse_error": parsed.parse_error,
    })


class Cursor:
    description = [("id",)]
    def execute(self, sql):
        return None
    def fetchone(self):
        return (1,)


class Connection:
    def cursor(self):
        cursor = Cursor()
        class Ctx:
            def __enter__(self):
                return cursor
            def __exit__(self, *args):
                return False
        return Ctx()


class Pool:
    instances = []
    def __init__(self, cfg):
        self.closed = 0
        Pool.instances.append(self)
    def get_connection(self):
        conn = Connection()
        class Ctx:
            def __enter__(self):
                return conn
            def __exit__(self, *args):
                return False
        return Ctx()
    def close_all(self):
        self.closed += 1


saved = {"database": "default_db", "host": "127.0.0.1", "port": 3306,
         "username": "synthetic", "password_encrypted": "synthetic",
         "charset": "utf8mb4"}
preprocess_error = None
slow_service = object.__new__(SlowQueryService)
with patch("backend.services.connection_registry.registry.get_saved", return_value=saved), \
     patch("backend.services.security_service.decrypt_password", return_value="synthetic"), \
     patch("backend.services.tdsql_connector.TDSQLConnectionPool", Pool), \
     patch("backend.services.slow_query_service.re.sub",
           side_effect=RuntimeError("synthetic preprocess failure")):
    try:
        slow_service.analyze_explain_by_sql("SELECT 1", "synthetic", "other_db")
    except Exception as exc:
        preprocess_error = type(exc).__name__ + ": " + str(exc)


async def unknown_registry_error_status():
    with patch("backend.api.daily_inspect.registry.get",
               side_effect=RuntimeError("synthetic programming defect")):
        try:
            await daily_run(DailyRequest(connection_id="synthetic"))
        except HTTPException as exc:
            return {"status": exc.status_code, "detail": exc.detail}
    return {"status": 200, "detail": "unexpected success"}


ticket = gateway_log_service.create_report_ticket(101, "synthetic-user")
ticket_wrong_report = gateway_log_service.consume_report_ticket(ticket, 102)
ticket_replay_after_wrong = gateway_log_service.consume_report_ticket(ticket, 101)
ticket_ok = gateway_log_service.create_report_ticket(101, "synthetic-user")
ticket_first = gateway_log_service.consume_report_ticket(ticket_ok, 101)
ticket_replay = gateway_log_service.consume_report_ticket(ticket_ok, 101)

result = {
    "audit_cases": audit_rows,
    "ephemeral_preprocess_exception": preprocess_error,
    "ephemeral_pool_count": len(Pool.instances),
    "ephemeral_close_calls": Pool.instances[0].closed if Pool.instances else None,
    "unknown_registry_exception": asyncio.run(unknown_registry_error_status()),
    "unknown_translation_type": type(translate_db_error(
        RuntimeError("synthetic programming defect"))).__name__,
    "ticket_semantics": {
        "wrong_report": ticket_wrong_report,
        "replay_after_wrong": ticket_replay_after_wrong,
        "first_valid": ticket_first,
        "replay_after_valid": ticket_replay,
    },
}
(HERE / "targeted_probe.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print(json.dumps(result, ensure_ascii=False))
