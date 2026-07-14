# -*- coding: utf-8 -*-
"""M2 · G3 集群深度巡检 回归测试"""
import os
import pytest

from backend.engine.severity_map import map_severity
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
MON_DB = "tdsqlpcloud_monitor_g3"


def test_severity_map():
    assert map_severity("FATAL") == "ERROR"
    assert map_severity("CRITICAL") == "ERROR"
    assert map_severity("HIGH") == "ERROR"
    assert map_severity("MEDIUM") == "WARNING"
    assert map_severity("WARNING") == "WARNING"
    assert map_severity("INFO") == "INFO"
    assert map_severity("low") == "INFO"


def _setup_mock_mdatacur():
    conn = pymysql.connect(host=_HOST, port=_PORT, user=_USER, password=_PASS)
    cur = conn.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS {MON_DB}")
    cur.execute(f"CREATE DATABASE {MON_DB}")
    cur.execute(f"USE {MON_DB}")
    cur.execute("""CREATE TABLE m_data_cur(
        f_mid VARCHAR(128), f_pmid VARCHAR(128), f_key VARCHAR(64), f_val DOUBLE, f_type INT)""")

    def put(mid, key, val):
        cur.execute("INSERT INTO m_data_cur(f_mid,f_pmid,f_key,f_val,f_type) VALUES(%s,%s,%s,%s,1)",
                    (mid, f"/tdsqlzk/{mid}", key, val))
    # node_a: 健康
    put("node_a", "cpu_usage", 30); put("node_a", "alive", 1)
    put("node_a", "connect_usage", 20); put("node_a", "slave_delay", 0)
    put("node_a", "no_primary_key_table_nums", 0)
    # node_b: CPU严重(>90) + 主备延迟警告(5<=x<30) + 无主键表>0
    put("node_b", "cpu_usage", 95); put("node_b", "alive", 1)
    put("node_b", "connect_usage", 88)   # >85 → CRITICAL(ERROR)
    put("node_b", "slave_delay", 10)     # >=5 <30 → WARNING
    put("node_b", "no_primary_key_table_nums", 3)   # >0 → WARNING
    put("node_b", "table_hit_rate", 85)  # <90 → CRITICAL(ERROR)
    conn.commit()
    conn.close()


def _cfg():
    return TDSQLConnectionConfig(host=_HOST, port=_PORT, user=_USER, password=_PASS,
                                database="bank", monitor_port=_PORT, monitor_db=MON_DB)


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP)
class TestClusterInspect:

    def test_run_inspection(self):
        os.environ.setdefault("SQLCHECK_DB_NAME", "tdsql_sqlcheck_test")
        _setup_mock_mdatacur()
        from backend.services.database import ensure_db
        from backend.services import cluster_inspect_service as svc
        ensure_db()
        pool = TDSQLConnectionPool(_cfg())
        res = svc.run_inspection(pool, connection_id="qa_conn", operator="qa")
        assert res["node_count"] == 2
        # node_b 应产生多条问题；node_a 健康无问题
        titles = {(i["node"], i["title"], i["severity"]) for i in res["issues"]}
        assert res["error_count"] >= 2   # CPU>90 + 连接>85 + 命中率<90
        assert res["warning_count"] >= 2  # 主备延迟 + 无主键表
        # 严重度只出现三级
        assert all(i["severity"] in ("ERROR", "WARNING", "INFO") for i in res["issues"])
        # 具体断言
        assert any(n == "node_b" and "CPU" in t and s == "ERROR" for (n, t, s) in titles)
        assert any(n == "node_b" and "主备延迟" in t and s == "WARNING" for (n, t, s) in titles)
        assert any(n == "node_b" and "无主键表" in t for (n, t, s) in titles)
        # 落库可查
        issues = svc.get_issues(res["inspection_id"])
        assert len(issues) == res["total_issues"]
        errs = svc.get_issues(res["inspection_id"], severity="ERROR")
        assert all(i["severity"] == "ERROR" for i in errs)
