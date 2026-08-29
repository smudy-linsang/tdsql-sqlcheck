"""Supplemental positive probes for rules absent from the main corpus."""
import json
import os
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PREPARE = HERE.parent / "v1.6.2.2-uat-o-r1" / "prepare_regression.py"
os.environ.update(
    AUTH_ENABLED="false",
    SCHEDULER_ENABLED="false",
    SQLCHECK_DB_NAME="tdsql_uat_o_reg_r7_supplemental_20260829",
)
prep = subprocess.run(
    [sys.executable, str(PREPARE)], cwd=ROOT, env=os.environ,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
if prep.returncode:
    raise SystemExit(prep.returncode)
sys.path.insert(0, str(ROOT))
from backend.engine.checker import RuleChecker


checker = RuleChecker()
cases = [
    ("R025", "ALTER TABLE t_customer MODIFY cust_id BIGINT", {"t_customer": {"shard_key": "cust_id"}}),
    ("R035", "CREATE TABLE t_customer(id BIGINT NOT NULL COMMENT 'id', PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='customer' shardkey=id", {"existing_columns": {"id": "VARCHAR(10)"}}),
    ("R038", "CREATE TABLE t_order_log(id BIGINT NOT NULL AUTO_INCREMENT COMMENT 'id', PRIMARY KEY(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='log' shardkey=id", None),
    ("R049", "SELECT a.id FROM t_a a JOIN t_b a ON a.id=a.id", None),
    ("R059", "BEGIN", {"t_customer": {"shard_key": "cust_id"}}),
]
rows = []
for rule_id, sql, metadata in cases:
    result = checker.audit_sql(sql, table_metadata=metadata, instance_type="distributed")
    fired = sorted(v.rule_id for v in result.violations)
    rows.append({"id": rule_id, "sql": sql, "fired": fired,
                 "target_fired": rule_id in fired})
(HERE / "supplemental_rule_probe.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print(json.dumps(rows, ensure_ascii=False))
