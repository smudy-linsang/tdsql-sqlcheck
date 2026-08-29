"""Prepare isolated fourth-round browser fixtures; synthetic local data only."""
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
if os.environ.get("SQLCHECK_DB_NAME") != "tdsql_uat_o_r4_1622_20260829":
    raise SystemExit("Refusing non-round-four database")

from backend.services.auth_service import auth_service
from backend.services.connection_registry import registry
from backend.services.database import MYSQL_CONFIG, _get_connection
from backend.services.slow_query_service import SlowQueryService
from backend.engine.slow_analyzer import SlowQueryRecord
import pymysql

password = os.environ["UAT_O_PASSWORD"]
admin_name = "uat_o_admin_r4"
if not auth_service.get_user(admin_name, use_cache=False):
    _, error = auth_service.create_user(admin_name, password, "admin", "UAT-O 管理员", "UAT-O R4 fixture")
    if error:
        raise RuntimeError(error)
    conn = _get_connection()
    conn.execute("UPDATE users SET must_change_password=0 WHERE username=?", (admin_name,))
    conn.commit()
    conn.close()

target = "tdsql_uat_o_r4_index_target"
cfg = {k: v for k, v in MYSQL_CONFIG.items() if k != "database"}
raw = pymysql.connect(**cfg)
try:
    with raw.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{target}` CHARACTER SET utf8mb4")
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS `{target}`.t_uat_index "
            "(id INT NOT NULL, code VARCHAR(32) NOT NULL, PRIMARY KEY(id), "
            "KEY idx_code(code), KEY idx_code_copy(code), KEY idx_code_id(code,id)) "
            "ENGINE=InnoDB COMMENT='UAT O R4 synthetic duplicate index fixture'")
        cursor.execute(f"INSERT IGNORE INTO `{target}`.t_uat_index VALUES (1,'one'),(2,'two')")
    raw.commit()
finally:
    raw.close()
registry.save_connection(name="UAT-O-R4 索引重复样本", host=MYSQL_CONFIG["host"],
                         port=MYSQL_CONFIG["port"], username=MYSQL_CONFIG["user"],
                         password=MYSQL_CONFIG["password"], database=target,
                         is_distributed=False, conn_id="uat_o_index", operator="UAT-O fixture",
                         description="Synthetic local index report validation only")

conn = _get_connection()
row = conn.execute("SELECT id FROM slow_queries WHERE db_name=?", ("uat_o_r4_workflow",)).fetchone()
conn.close()
if not row:
    row = SlowQueryService().add_slow_query(SlowQueryRecord(
        fingerprint="SELECT id FROM t_uat_order WHERE customer_id = ?",
        sql_text="SELECT id FROM t_uat_order WHERE customer_id = 2",
        db_name="uat_o_r4_workflow", exec_count=10, avg_time_ms=1200,
        total_time_ms=12000, rows_examined=10000, rows_sent=10),
        connection_id="uat_o_local")
print("BROWSER_FIXTURES_READY", target, row.get("id"))
