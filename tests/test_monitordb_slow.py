# -*- coding: utf-8 -*-
"""M1 · G1 monitordb 集群级慢SQL数据源 回归测试

- _normalize_fingerprint 纯函数（始终运行）
- get_cluster_slow_queries 对 mock monitordb 的聚合/过滤/防御式列裁剪（需 MySQL）
- source=monitordb 端到端扫描落库（需 MySQL）
"""
import os
import pytest

from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig

# ── MySQL 可用性探测（与其它 DB 测试一致的守卫）────────────────────────
_HOST = os.environ.get("TDSQL_TEST_HOST", "127.0.0.1")
_PORT = int(os.environ.get("TDSQL_TEST_PORT", "13306"))
_USER = os.environ.get("TDSQL_TEST_USER", "root")
_PASS = os.environ.get("TDSQL_TEST_PASSWORD", "tdsql_test_2024")
try:
    import pymysql
    _c = pymysql.connect(host=_HOST, port=_PORT, user=_USER, password=_PASS, connect_timeout=3)
    _c.close()
    MYSQL_AVAILABLE = True
except Exception:
    MYSQL_AVAILABLE = False
SKIP_REASON = "MySQL 测试环境未启动（需 127.0.0.1:13306）"

MON_DB = "tdsqlpcloud_monitor_test"  # 测试用监控库名，避免与真实库冲突


def _cfg():
    return TDSQLConnectionConfig(host=_HOST, port=_PORT, user=_USER, password=_PASS,
                                 database="bank", monitor_port=_PORT, monitor_db=MON_DB)


def _setup_mock_monitor(drop_cols=None):
    """建 mock monitordb 明细表并灌入固定样本；drop_cols 用于模拟旧版本缺列。"""
    drop_cols = drop_cols or []
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
    OUT = "2026-06-01 10:00:00"

    def ins(**k):
        cur.execute(f"INSERT INTO proxy_classes_analysis({','.join(k)}) VALUES({','.join(['%s']*len(k))})",
                    tuple(k.values()))
    base = dict(set_ip="10.0.0.1", example_time=IN, query_time_median=2.0, query_time_min=1.0,
                lock_time_sum=0.5, lock_time_median=0, lock_time_avg=0, lock_time_min=0, lock_time_max=0.1)
    # 指纹 A 跨 2 SET（checksum 3001）→ 合并
    ins(set_name="set_1", set_port=15005, timestramp=IN, master=1, example_sql="/*x*/SELECT * FROM t_txn",
        example_query_time=3.0, user="app", host="10.0.0.9", db="bank", checksum=3001,
        fingerprint="SELECT * FROM t_txn", ts_min=IN, ts_max=IN, query_count=100, query_time_sum=200,
        query_time_avg=2.0, query_time_max=3.0, rows_sent_sum=100, rows_examined_sum=100000,
        rows_affected_sum=0, rows_affected_max=0, **base)
    ins(set_name="set_2", set_port=15007, timestramp=IN, master=1, example_sql="/*y*/SELECT * FROM t_txn",
        example_query_time=5.0, user="app2", host="10.0.0.8", db="bank", checksum=3001,
        fingerprint="SELECT * FROM t_txn", ts_min=IN, ts_max=IN, query_count=50, query_time_sum=200,
        query_time_avg=4.0, query_time_max=5.0, rows_sent_sum=50, rows_examined_sum=50000,
        rows_affected_sum=0, rows_affected_max=0, **base)
    # 噪音 commit → 排除
    ins(set_name="set_1", set_port=15005, timestramp=IN, master=1, example_sql="commit", example_query_time=2.0,
        user="app", host="10.0.0.9", db="bank", checksum=3002, fingerprint="commit", ts_min=IN, ts_max=IN,
        query_count=10, query_time_sum=20, query_time_avg=2.0, query_time_max=2.0, rows_sent_sum=0,
        rows_examined_sum=0, rows_affected_sum=0, rows_affected_max=0, **base)
    # 系统账号 dbman → 排除
    ins(set_name="set_1", set_port=15005, timestramp=IN, master=1, example_sql="SELECT * FROM t_audit",
        example_query_time=9.0, user="dbman", host="10.0.0.9", db="bank", checksum=3003,
        fingerprint="SELECT * FROM t_audit", ts_min=IN, ts_max=IN, query_count=5, query_time_sum=45,
        query_time_avg=9.0, query_time_max=9.0, rows_sent_sum=5, rows_examined_sum=9,
        rows_affected_sum=0, rows_affected_max=0, **base)
    # 时间窗外 → 排除
    ins(set_name="set_1", set_port=15005, timestramp=OUT, master=1, example_sql="SELECT * FROM t_old",
        example_query_time=9.0, user="app", host="10.0.0.9", db="bank", checksum=3004,
        fingerprint="SELECT * FROM t_old", ts_min=OUT, ts_max=OUT, query_count=8, query_time_sum=72,
        query_time_avg=9.0, query_time_max=9.0, rows_sent_sum=8, rows_examined_sum=8,
        rows_affected_sum=0, rows_affected_max=0, **base)
    # UPDATE 带影响行（checksum 3007）
    ins(set_name="set_2", set_port=15007, timestramp=IN, master=1,
        example_sql="UPDATE t_acct SET bal=bal+1 WHERE id=9", example_query_time=1.5, user="app",
        host="10.0.0.7", db="bank", checksum=3007, fingerprint="UPDATE t_acct SET bal=bal+? WHERE id=?",
        ts_min=IN, ts_max=IN, query_count=20, query_time_sum=30, query_time_avg=1.5, query_time_max=2.0,
        rows_sent_sum=0, rows_examined_sum=20, rows_affected_sum=20, rows_affected_max=1, **base)
    conn.commit()
    for col in drop_cols:
        cur.execute(f"ALTER TABLE proxy_classes_analysis DROP COLUMN {col}")
    conn.commit()
    conn.close()


# ── 纯函数（始终运行）──────────────────────────────────────────────
def test_normalize_fingerprint():
    n = TDSQLConnectionPool._normalize_fingerprint
    assert n("  SELECT   *  FROM  t ;  ") == "SELECT * FROM t"
    assert n("SELECT * FROM ( select 1 )") == "SELECT * FROM (select 1)"
    assert n("a\t b\n c") == "a b c"


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
class TestMonitordbSlow:

    def test_probe_ok(self):
        _setup_mock_monitor()
        pool = TDSQLConnectionPool(_cfg())
        p = pool.monitor_probe()
        assert p["ok"] and len(p["columns"]) >= 30 and not p["error"]

    def test_aggregate_merge_and_filters(self):
        _setup_mock_monitor()
        pool = TDSQLConnectionPool(_cfg())
        rows = pool.get_cluster_slow_queries(limit=50, min_time=0.001,
                                             time_start="2026-07-14 00:00:00",
                                             time_end="2026-07-15 00:00:00")
        by = {int(r["DIGEST"]): r for r in rows}
        assert set(by) == {3001, 3007}, "噪音/系统账号/窗外应被排除"
        a = by[3001]
        assert a["exec_count"] == 150
        assert abs(a["total_seconds"] - 400) < 0.5
        assert abs(a["avg_seconds"] - 2.6667) < 0.01   # 加权重算 (2*100+4*50)/150
        assert abs(a["max_seconds"] - 5.0) < 0.01
        assert set(a["set_ids"].split(",")) == {"set_1", "set_2"}
        assert set(a["client_user"].split(",")) == {"app", "app2"}
        assert a["rows_examined"] == 150000
        assert by[3007]["rows_affected"] == 20

    def test_min_time_filter(self):
        _setup_mock_monitor()
        pool = TDSQLConnectionPool(_cfg())
        rows = pool.get_cluster_slow_queries(limit=50, min_time=3.0,
                                             time_start="2026-07-14 00:00:00",
                                             time_end="2026-07-15 00:00:00")
        # avg 2.67 与 1.5 均 < 3.0 → 全部过滤
        assert rows == []

    def test_defensive_missing_column(self):
        _setup_mock_monitor(drop_cols=["rows_affected_sum"])
        pool = TDSQLConnectionPool(_cfg())
        rows = pool.get_cluster_slow_queries(limit=50, min_time=0.001,
                                             time_start="2026-07-14 00:00:00",
                                             time_end="2026-07-15 00:00:00")
        assert len(rows) == 2
        assert all(r["rows_affected"] == 0 for r in rows)  # 缺列降级为0，不报错

    def test_end_to_end_scan_persists(self):
        _setup_mock_monitor()
        from backend.services.database import ensure_db
        from backend.services import scan_service
        ensure_db()
        pool = TDSQLConnectionPool(_cfg())
        res = scan_service.run_scan(connection_id="", source="monitordb", limit=50, min_time=0.001,
                                    time_window_start="2026-07-14 00:00:00",
                                    time_window_end="2026-07-15 00:00:00", operator="qa", pool=pool)
        assert res["source"] == "monitordb" and res["fetched"] == 2 and not res["errors"]
        m = pymysql.connect(host=_HOST, port=_PORT, user=_USER, password=_PASS,
                            database=os.environ.get("SQLCHECK_DB_NAME", "tdsql_sqlcheck_test"))
        mc = m.cursor(pymysql.cursors.DictCursor)
        mc.execute("SELECT client_user, rows_affected, severity FROM slow_queries "
                   "WHERE scan_task_id=%s AND rows_affected>0", (res["scan_task_id"],))
        upd = mc.fetchone()
        m.close()
        assert upd and upd["rows_affected"] == 20 and upd["client_user"] == "app"
        assert upd["severity"] in ("ERROR", "WARNING", "INFO")
