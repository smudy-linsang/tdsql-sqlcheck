# -*- coding: utf-8 -*-
"""M3 · G4 每日巡检 + 趋势 回归测试"""
import os
import pytest

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
MON_DB = "tdsqlpcloud_monitor_g4"


def _setup(cpu_peak):
    conn = pymysql.connect(host=_HOST, port=_PORT, user=_USER, password=_PASS)
    cur = conn.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS {MON_DB}")
    cur.execute(f"CREATE DATABASE {MON_DB}")
    cur.execute(f"USE {MON_DB}")
    cur.execute("""CREATE TABLE m_data_cur(
        f_mid VARCHAR(128), f_pmid VARCHAR(128), f_key VARCHAR(64), f_val DOUBLE, f_type INT)""")

    def put(mid, key, val):
        cur.execute("INSERT INTO m_data_cur VALUES(%s,%s,%s,%s,1)", (mid, f"/tdsqlzk/{mid}", key, val))
    for mid in ("node_a",):
        put(mid, "cpu_usage_max", cpu_peak); put(mid, "cpu_usage", cpu_peak - 10)
        put(mid, "mysql_max_mem_usage", 40); put(mid, "connect_usage", 20)
        put(mid, "slow_query", 5); put(mid, "slave_delay", 1); put(mid, "data_dir_usage", 55)
    conn.commit()
    conn.close()


def _cfg():
    return TDSQLConnectionConfig(host=_HOST, port=_PORT, user=_USER, password=_PASS,
                                database="bank", monitor_port=_PORT, monitor_db=MON_DB)


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP)
class TestDailyInspect:

    def test_run_and_trend(self):
        os.environ.setdefault("SQLCHECK_DB_NAME", "tdsql_sqlcheck_test")
        from backend.services.database import ensure_db
        from backend.services import daily_inspect_service as svc
        ensure_db()
        # 清理历史
        c = pymysql.connect(host=_HOST, port=_PORT, user=_USER, password=_PASS,
                            database="tdsql_sqlcheck_test")
        c.cursor().execute("DELETE FROM daily_inspection WHERE connection_id='g4_conn'")
        c.commit(); c.close()

        # 第1天 cpu峰值70
        _setup(70)
        pool = TDSQLConnectionPool(_cfg())
        r1 = svc.run_daily(pool, connection_id="g4_conn", inspect_date="2026-07-13")
        assert r1["node_count"] == 1 and r1["rows"][0]["cpu_peak"] == 70

        # 第2天 cpu峰值85（同库刷新为新值）
        _setup(85)
        pool2 = TDSQLConnectionPool(_cfg())
        r2 = svc.run_daily(pool2, connection_id="g4_conn", inspect_date="2026-07-14")
        assert r2["rows"][0]["cpu_peak"] == 85

        # 幂等：重跑第2天不产生重复行
        svc.run_daily(pool2, connection_id="g4_conn", inspect_date="2026-07-14")

        tr = svc.get_trend("g4_conn", "2026-07-01", "2026-07-31", ["cpu_peak"])
        assert tr["days"] == 2
        vals = [p["value"] for p in tr["series"]["cpu_peak"]]
        assert 70 in vals and 85 in vals
