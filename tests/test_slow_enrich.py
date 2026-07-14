# -*- coding: utf-8 -*-
"""M1 · G2 慢SQL十列增强诊断 回归测试"""
import os
import pytest

from backend.services import slow_enrich_service as se
from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig

_HOST = os.environ.get("TDSQL_TEST_HOST", "127.0.0.1")
_PORT = int(os.environ.get("TDSQL_TEST_PORT", "13306"))
_USER = os.environ.get("TDSQL_TEST_USER", "root")
_PASS = os.environ.get("TDSQL_TEST_PASSWORD", "tdsql_test_2024")
try:
    import pymysql
    pymysql.connect(host=_HOST, port=_PORT, user=_USER, password=_PASS, connect_timeout=3).close()
    MYSQL_AVAILABLE = True
except Exception:
    MYSQL_AVAILABLE = False
SKIP = "MySQL 测试环境未启动（需 127.0.0.1:13306）"


# ── 安全 EXPLAIN（纯函数，最高优先级红线）────────────────────────────
def test_safe_explain_select():
    sql, skip = se.safe_sql_for_explain("SELECT * FROM t WHERE id=1")
    assert skip is None and sql.upper().startswith("EXPLAIN SELECT")

def test_safe_explain_update_rewrite():
    sql, skip = se.safe_sql_for_explain("UPDATE t SET a=1 WHERE id=9")
    assert skip is None
    assert sql.upper().startswith("EXPLAIN SELECT")
    assert "WHERE" in sql.upper() and "UPDATE" not in sql.upper()

def test_safe_explain_delete_rewrite():
    sql, skip = se.safe_sql_for_explain("DELETE FROM t WHERE id=9")
    assert skip is None and sql.upper().startswith("EXPLAIN SELECT * FROM T")

def test_safe_explain_rejects_semicolon():
    sql, skip = se.safe_sql_for_explain("SELECT 1; DROP TABLE t")
    assert sql is None and "分号" in skip

def test_safe_explain_skips_insert_ddl():
    for bad in ["INSERT INTO t VALUES(1)", "DROP TABLE t", "SET autocommit=1", "REPLACE INTO t VALUES(1)"]:
        sql, skip = se.safe_sql_for_explain(bad)
        assert sql is None and skip

def test_extract_explain_issues():
    assert "全表扫描" in se.extract_explain_issues("id=1 | type=ALL | key=NULL")
    assert "未使用索引" in se.extract_explain_issues("type=ALL | key=NULL")
    assert "临时表" in se.extract_explain_issues("Extra=Using temporary")
    assert se.extract_explain_issues("type=const | key=PRIMARY") == "无明显问题"
    assert "过大" in se.extract_explain_issues("rows=200000")

def test_scan_efficiency_and_divzero():
    assert "优秀" in se.calc_scan_efficiency(100, 90)
    assert "极低" in se.calc_scan_efficiency(10000, 1)
    assert se.calc_scan_efficiency(0, 0) == "N/A (无扫描行数)"   # 防除零

def test_extract_tables():
    assert se.extract_tables_from_sql("SELECT * FROM t_txn WHERE id=1") == ["t_txn"]
    ts = se.extract_tables_from_sql("SELECT * FROM a JOIN b ON a.id=b.id")
    assert "a" not in ts  # len<=1 被过滤
    assert set(se.extract_tables_from_sql("UPDATE t_acct SET bal=1 WHERE id=9")) == {"t_acct"}


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP)
class TestEnrichDB:

    @classmethod
    def setup_class(cls):
        conn = pymysql.connect(host=_HOST, port=_PORT, user=_USER, password=_PASS)
        cur = conn.cursor()
        cur.execute("DROP DATABASE IF EXISTS bank_enrich")
        cur.execute("CREATE DATABASE bank_enrich")
        cur.execute("USE bank_enrich")
        cur.execute("""CREATE TABLE t_txn(id INT PRIMARY KEY, acct VARCHAR(32),
                       amount DECIMAL(10,2), remark VARCHAR(200), KEY idx_acct(acct))""")
        for i in range(1, 51):
            cur.execute("INSERT INTO t_txn VALUES(%s,%s,%s,%s)", (i, f"a{i%5}", i*1.5, "x"))
        conn.commit()
        conn.close()
        cls.pool = TDSQLConnectionPool(TDSQLConnectionConfig(
            host=_HOST, port=_PORT, user=_USER, password=_PASS, database="bank_enrich"))

    def test_explain_real_table(self):
        # 无索引列过滤 → 全表扫描
        ex = se.get_explain(self.pool, "bank_enrich", "SELECT * FROM t_txn WHERE remark='x'")
        assert not ex.startswith("N/A"), ex
        assert "type=ALL" in ex

    def test_table_stats_and_index(self):
        stats = se.get_table_stats(self.pool, "bank_enrich", "t_txn")
        assert "引擎=" in stats and "MB" in stats
        idx = se.get_index_details(self.pool, "bank_enrich", "t_txn")
        assert "PRIMARY" in idx and "idx_acct" in idx

    def test_enrich_rows_end_to_end(self):
        rows = [{"SCHEMA_NAME": "bank_enrich",
                 "example_sql": "SELECT * FROM t_txn WHERE remark='x'",
                 "DIGEST_TEXT": "SELECT * FROM t_txn WHERE remark=?",
                 "rows_examined": 50, "rows_sent": 45}]
        se.enrich_rows(self.pool, rows, "bank_enrich")
        r = rows[0]
        assert r["involved_tables"] == "t_txn"
        assert "type=ALL" in r["explain_plan"]
        assert "全表扫描" in r["explain_issues"]
        assert "引擎=" in r["table_stats"]
        assert "PRIMARY" in r["index_details"]
        assert "0.9" in r["scan_efficiency"]  # 45/50=0.9 优秀

    def test_enrich_permission_degradation(self):
        # sys 库通常存在，但即便查询失败也应降级为字符串而不抛异常
        red = se.get_redundant_indexes(self.pool, "bank_enrich", "t_txn")
        assert isinstance(red, str)  # 不抛异常
