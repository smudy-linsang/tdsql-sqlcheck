"""Fill the five primary-corpus rule gaps without contacting any database."""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
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
    fired = sorted({v.rule_id for v in result.violations})
    rows.append({
        "id": rule_id,
        "sql": sql,
        "fired": fired,
        "target_fired": rule_id in fired,
    })
(HERE / "supplemental_rule_probe.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
)
print(json.dumps(rows, ensure_ascii=False))
