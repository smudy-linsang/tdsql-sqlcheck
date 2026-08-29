"""Independent probes for Q's exact fixes and likely secondary-damage boundaries."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
if not os.environ.get("SQLCHECK_DB_NAME", "").startswith("tdsql_uat_o_reg_r4_"):
    raise SystemExit("Isolated round-four diagnostic database required")

from backend.engine.checker import RuleChecker
from backend.services.slow_query_service import SlowQueryService

checker = RuleChecker()
head_cases = [
    "# operator's note\nLOAD XML INFILE '/tmp/synthetic.xml' INTO TABLE t",
    "-- ordinary\rCREATE VIEW v AS SELECT 1 +",
    "; LOAD XML INFILE '/tmp/synthetic.xml' INTO TABLE t",
    "'decoy' LOAD XML INFILE '/tmp/synthetic.xml' INTO TABLE t",
]
heads = []
for sql in head_cases:
    parsed = checker.parser.parse(sql)
    result = checker.audit_sql(sql)
    heads.append({"sql": sql, "fired": sorted(v.rule_id for v in result.violations),
                  "passed": result.passed, "parse_error": parsed.parse_error,
                  "has_load_data": parsed.has_load_data})


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
            def __enter__(self): return cursor
            def __exit__(self, *args): return False
        return Ctx()


class Pool:
    instances = []
    def __init__(self, cfg):
        self.closed = 0
        Pool.instances.append(self)
    def get_connection(self):
        conn = Connection()
        class Ctx:
            def __enter__(self): return conn
            def __exit__(self, *args): return False
        return Ctx()
    def close_all(self):
        self.closed += 1


saved = {"database": "default_db", "host": "127.0.0.1", "port": 3306,
         "username": "synthetic", "password_encrypted": "synthetic", "charset": "utf8mb4"}
preprocess_error = None
slow_service = object.__new__(SlowQueryService)
with patch("backend.services.connection_registry.registry.get_saved", return_value=saved), \
     patch("backend.services.security_service.decrypt_password", return_value="synthetic"), \
     patch("backend.services.tdsql_connector.TDSQLConnectionPool", Pool), \
    patch("backend.services.slow_query_service.re.sub", side_effect=RuntimeError("synthetic preprocess failure")):
    try:
        slow_service.analyze_explain_by_sql("SELECT 1", "synthetic", "other_db")
    except Exception as exc:
        preprocess_error = type(exc).__name__ + ": " + str(exc)

result = {
    "head_cases": heads,
    "ephemeral_preprocess_exception": preprocess_error,
    "ephemeral_pool_count": len(Pool.instances),
    "ephemeral_close_calls": Pool.instances[0].closed if Pool.instances else None,
}
(HERE / "targeted_probe.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print(json.dumps(result, ensure_ascii=False))
