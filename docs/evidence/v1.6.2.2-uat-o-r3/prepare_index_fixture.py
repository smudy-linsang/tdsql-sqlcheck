"""Create a new, tiny synthetic index database to verify report truthfulness."""
import os
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parents[2]))
if os.environ.get('SQLCHECK_DB_NAME')!='tdsql_uat_o_r3_1622_20260828':raise SystemExit('Wrong UAT database')
from backend.services.database import MYSQL_CONFIG
from backend.services.connection_registry import registry
import pymysql
db='tdsql_uat_o_r3_index_target'
cfg={k:v for k,v in MYSQL_CONFIG.items() if k!='database'}
c=pymysql.connect(**cfg)
try:
    with c.cursor() as cur:
        cur.execute(f'CREATE DATABASE IF NOT EXISTS `{db}` CHARACTER SET utf8mb4')
        cur.execute(f"CREATE TABLE IF NOT EXISTS `{db}`.t_uat_index (id INT NOT NULL, code VARCHAR(32) NOT NULL, PRIMARY KEY(id), KEY idx_code(code), KEY idx_code_copy(code), KEY idx_code_id(code,id)) ENGINE=InnoDB COMMENT='UAT O R3 synthetic duplicate index fixture'")
        cur.execute(f"INSERT IGNORE INTO `{db}`.t_uat_index VALUES (1,'synthetic-one'),(2,'synthetic-two')")
    c.commit()
finally:c.close()
registry.save_connection(name='UAT-O-R3 索引重复样本',host=MYSQL_CONFIG['host'],port=MYSQL_CONFIG['port'],username=MYSQL_CONFIG['user'],password=MYSQL_CONFIG['password'],database=db,is_distributed=False,conn_id='uat_o_index',operator='UAT-O fixture',description='Synthetic local index report validation only')
print('SYNTHETIC_INDEX_FIXTURE_READY',db,'one table four indexes two rows')
