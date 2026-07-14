# -*- coding: utf-8 -*-
"""M4 · G8 SQL调用量分析 + G9 大表增长趋势 回归测试"""
import os
import pytest

from backend.services import sql_stats_service
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
SKIP = "MySQL 测试环境未启动"
MON_DB = "tdsqlpcloud_monitor_g8"


def test_sql_type_classify():
    assert sql_stats_service._sql_type("SELECT * FROM t") == "SELECT"
    assert sql_stats_service._sql_type("update t set a=1") == "UPDATE"
    assert sql_stats_service._sql_type("show tables") == "OTHER"


def _setup_monitor():
    conn = pymysql.connect(host=_HOST, port=_PORT, user=_USER, password=_PASS)
    cur = conn.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS {MON_DB}")
    cur.execute(f"CREATE DATABASE {MON_DB}")
    cur.execute(f"USE {MON_DB}")
    cur.execute("""CREATE TABLE proxy_classes_analysis(
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, set_name VARCHAR(100), set_ip VARCHAR(20),
      set_port INT, timestramp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, master TINYINT,
      example_sql TEXT, example_query_time FLOAT, example_time DATETIME NULL,
      user VARCHAR(100), host VARCHAR(100), db VARCHAR(100), checksum BIGINT UNSIGNED,
      fingerprint TEXT, ts_min DATETIME, ts_max DATETIME,
      query_count FLOAT, query_time_sum FLOAT, query_time_median FLOAT, query_time_avg FLOAT,
      query_time_min FLOAT, query_time_max FLOAT, lock_time_sum FLOAT, lock_time_median FLOAT,
      lock_time_avg FLOAT, lock_time_min FLOAT, lock_time_max FLOAT,
      rows_sent_sum FLOAT, rows_examined_sum FLOAT, rows_affected_sum FLOAT, rows_affected_max FLOAT)""")
    IN = "2026-07-14 10:00:00"

    def ins(**k):
        cur.execute(f"INSERT INTO proxy_classes_analysis({','.join(k)}) "
                    f"VALUES({','.join(['%s']*len(k))})", tuple(k.values()))
    base = dict(set_name="s1", set_ip="1.1.1.1", set_port=15005, timestramp=IN, master=1,
                example_time=IN, user="app", host="h", db="bank", ts_min=IN, ts_max=IN,
                query_time_median=0, query_time_min=0, lock_time_sum=0, lock_time_median=0,
                lock_time_avg=0, lock_time_min=0, lock_time_max=0, rows_affected_sum=0, rows_affected_max=0)

    def row(cs, fp, cnt, tsum, avg, exam, sent, eqt):
        ins(checksum=cs, fingerprint=fp, example_sql=fp, example_query_time=eqt,
            query_count=cnt, query_time_sum=tsum, query_time_avg=avg, query_time_max=avg,
            rows_examined_sum=exam, rows_sent_sum=sent, **base)
    row(1, "SELECT * FROM t_a WHERE x=?", 1000, 5, 0.005, 2, 2, 0.01)      # 高频
    row(2, "SELECT * FROM t_b WHERE y=?", 5, 50, 10.0, 100000, 1, 12.0)    # 慢+全表扫描
    row(3, "UPDATE t_c SET a=? WHERE id=?", 200, 20, 0.1, 200, 0, 0.2)     # UPDATE
    conn.commit()
    conn.close()


def _cfg():
    return TDSQLConnectionConfig(host=_HOST, port=_PORT, user=_USER, password=_PASS,
                                database="bank", monitor_port=_PORT, monitor_db=MON_DB)


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP)
class TestSqlStats:

    def test_analyze_multidim(self):
        _setup_monitor()
        pool = TDSQLConnectionPool(_cfg())
        res = sql_stats_service.analyze(pool, time_start="2026-07-14 00:00:00",
                                        time_end="2026-07-15 00:00:00", top_n=10)
        assert res["sql_class_count"] == 3
        assert "SELECT" in res["type_distribution"] and "UPDATE" in res["type_distribution"]
        # 高频 TOP1 是 t_a(1000次)
        assert res["top_frequent"][0]["exec_count"] == 1000
        # 慢 TOP1 是 t_b(avg10)
        assert res["top_slow"][0]["avg_seconds"] == 10.0
        # 全表扫描候选含 t_b(扫10万返回1)
        assert any("t_b" in v["fingerprint"] for v in res["top_full_scan"])


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP)
class TestBigtableTrend:

    def test_growth_ranking(self):
        os.environ.setdefault("SQLCHECK_DB_NAME", "tdsql_sqlcheck_test")
        from backend.services.database import ensure_db, _get_connection
        from backend.services import bigtable_trend_service as bts
        ensure_db()
        c = _get_connection()
        c.execute("DELETE FROM bigtable_history WHERE connection_id='g9'")
        for (d, sz) in [("2026-07-01", 10.0), ("2026-07-14", 15.5)]:
            c.execute("INSERT INTO bigtable_history (snap_date,connection_id,db_name,table_name,"
                      "table_rows,size_gb) VALUES (?,?,?,?,?,?)",
                      (d, "g9", "bank", "t_big", 1000000, sz))
        c.commit(); c.close()
        g = bts.get_growth("g9", "bank", "t_big")
        assert g["growth_ranking"][0]["delta_gb"] == 5.5
        assert g["growth_ranking"][0]["table"] == "bank.t_big"
