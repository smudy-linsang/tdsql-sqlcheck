# -*- coding: utf-8 -*-
"""M3 · G7 应急诊断 回归测试（只读，不执行任何写/kill）"""
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


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP)
class TestEmergency:

    @classmethod
    def setup_class(cls):
        os.environ.setdefault("SQLCHECK_DB_NAME", "tdsql_sqlcheck_test")
        from backend.services.database import ensure_db
        ensure_db()
        cls.pool = TDSQLConnectionPool(TDSQLConnectionConfig(
            host=_HOST, port=_PORT, user=_USER, password=_PASS, database="tdsql_sqlcheck_test"))

    def test_run_all_sections(self):
        from backend.services import emergency_diag_service as svc
        res = svc.run(self.pool, connection_id="qa", actions=["all"])
        secs = res["sections"]
        assert set(secs) == {"status", "session", "bigtrx", "lock", "slow", "innodb"}
        # status 必成功且给出连接使用率
        assert secs["status"]["ok"] and "conn_usage_pct" in secs["status"]
        # 各 section 要么 ok，要么带 error（不抛异常、结构化降级）
        for name, s in secs.items():
            assert "ok" in s
            if not s["ok"]:
                assert "error" in s
        # 严重度只三级
        for s in secs.values():
            if s.get("severity"):
                assert s["severity"] in ("ERROR", "WARNING", "INFO")
        # 落库
        assert res.get("report_id")

    def test_lock_version_adaptive(self):
        from backend.services import emergency_diag_service as svc
        r = svc._lock(self.pool, "")
        # 不论 5.7/8.0/MariaDB，都应结构化返回（ok 或带 error），绝不抛异常
        assert "ok" in r
        if r["ok"]:
            assert "lock_waits" in r and "source" in r

    def test_selected_actions_only(self):
        from backend.services import emergency_diag_service as svc
        res = svc.run(self.pool, connection_id="qa", actions=["status", "session"])
        assert set(res["sections"]) == {"status", "session"}
