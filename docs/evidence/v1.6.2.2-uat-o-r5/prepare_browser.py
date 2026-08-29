"""Prepare isolated fifth-round browser fixtures; synthetic local data only."""
import os
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
if os.environ.get("SQLCHECK_DB_NAME") != "tdsql_uat_o_r5_1622_20260829":
    raise SystemExit("Refusing non-round-five database")

from backend.engine.slow_analyzer import SlowQueryRecord
from backend.services.auth_service import auth_service
from backend.services.connection_registry import registry
from backend.services.database import MYSQL_CONFIG, _get_connection, ensure_db
from backend.services.slow_query_service import SlowQueryService
import pymysql


password = os.environ["UAT_O_PASSWORD"]
ensure_db()
auth_service.ensure_bootstrap_admin()
users = (
    ("uat_o_admin_r5", "admin", "UAT-O 管理员"),
    ("uat_o_dba_r5", "dba", "UAT-O DBA"),
    ("uat_o_developer_r5", "developer", "UAT-O 开发"),
    ("uat_o_auditor_r5", "auditor", "UAT-O 审计"),
)
for username, role, display in users:
    if not auth_service.get_user(username, use_cache=False):
        _, error = auth_service.create_user(
            username, password, role, display, "UAT-O R5 synthetic fixture")
        if error:
            raise RuntimeError(error)

conn = _get_connection()
try:
    for username, _, _ in users:
        conn.execute("UPDATE users SET must_change_password=0, status='active' WHERE username=?",
                     (username,))
    conn.commit()
finally:
    conn.close()

target = "tdsql_uat_o_r5_index_target"
explain_target = "tdsql_uat_o_r5_explain_target"
cfg = {k: v for k, v in MYSQL_CONFIG.items() if k != "database"}
raw = pymysql.connect(**cfg)
try:
    with raw.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{target}` CHARACTER SET utf8mb4")
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS `{target}`.t_uat_index "
            "(id INT NOT NULL, code VARCHAR(32) NOT NULL, PRIMARY KEY(id), "
            "KEY idx_code(code), KEY idx_code_copy(code), KEY idx_code_id(code,id)) "
            "ENGINE=InnoDB COMMENT='UAT O R5 synthetic duplicate index fixture'")
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS `{target}`.t_uat_order "
            "(id INT NOT NULL, customer_id INT NOT NULL, PRIMARY KEY(id), "
            "KEY idx_customer(customer_id)) ENGINE=InnoDB")
        cursor.execute(f"INSERT IGNORE INTO `{target}`.t_uat_index VALUES (1,'one'),(2,'two')")
        cursor.execute(f"INSERT IGNORE INTO `{target}`.t_uat_order VALUES (1,1),(2,2)")
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{explain_target}` CHARACTER SET utf8mb4")
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS `{explain_target}`.t_uat_order "
            "(id INT NOT NULL, customer_id INT NOT NULL, PRIMARY KEY(id), "
            "KEY idx_customer(customer_id)) ENGINE=InnoDB")
        cursor.execute(
            f"INSERT IGNORE INTO `{explain_target}`.t_uat_order VALUES (1,1),(2,2)")
    raw.commit()
finally:
    raw.close()

for conn_id, name, port, database in (
    ("uat_o_index_r5", "UAT-O-R5 索引重复样本", MYSQL_CONFIG["port"], target),
    ("uat_o_local_r5", "UAT-O-R5 本地在线样本", MYSQL_CONFIG["port"], explain_target),
    ("uat_o_offline_r5", "UAT-O-R5 离线样本", 65530, target),
):
    registry.save_connection(
        name=name, host=MYSQL_CONFIG["host"], port=port,
        username=MYSQL_CONFIG["user"], password=MYSQL_CONFIG["password"],
        database=database, is_distributed=False, conn_id=conn_id,
        operator="UAT-O fixture", description="Synthetic local UAT validation only")

conn = _get_connection()
row = conn.execute("SELECT id FROM slow_queries WHERE db_name=?", ("uat_o_r5_workflow",)).fetchone()
conn.close()
if not row:
    row = SlowQueryService().add_slow_query(SlowQueryRecord(
        fingerprint="SELECT id FROM t_uat_order WHERE customer_id = ?",
        sql_text="SELECT id FROM t_uat_order WHERE customer_id = 2",
        db_name="uat_o_r5_workflow", exec_count=10, avg_time_ms=1200,
        total_time_ms=12000, rows_examined=10000, rows_sent=10),
        connection_id="uat_o_local_r5")

(HERE / "gateway_partial.log").write_text(
    "invalid one\n"
    "[2026-08-29 00:00:00 100] INFO topic=test&timecost=2.5&sql=select 1&db=synthetic&user=uat\n"
    "[2026-08-29 00:00:01 101] INFO topic=test&timecost=3.5&sql=select 2&db=synthetic&user=uat\n"
    "invalid two\n"
    "[2026-08-29 00:00:02 102] INFO topic=test&timecost=4.5&sql=select 3&db=synthetic&user=uat\n"
    "[2026-08-29 00:00:03 103] INFO topic=test&timecost=5.5&sql=select 4&db=synthetic&user=uat\n"
    "invalid three\n",
    encoding="utf-8", newline="\n")
(HERE / "gateway_xss.log").write_text(
    "[2026-08-29 00:00:04 104] INFO topic=test&timecost=6.5&"
    "sql=select 1 /* </script><script>window.__uat_o_r5_pwned=1</script> "
    "<img src=x onerror=window.__uat_o_r5_img=1> */&db=synthetic&user=uat\n",
    encoding="utf-8", newline="\n")
print("BROWSER_FIXTURES_READY", target, row.get("id"), len(users))
