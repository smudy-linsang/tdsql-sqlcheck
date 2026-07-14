# -*- coding: utf-8 -*-
"""M3 · G5 索引健康审计 回归测试"""
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
DB = "idxaudit_test"


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP)
class TestIndexAudit:

    @classmethod
    def setup_class(cls):
        conn = pymysql.connect(host=_HOST, port=_PORT, user=_USER, password=_PASS)
        cur = conn.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS {DB}")
        cur.execute(f"CREATE DATABASE {DB}")
        cur.execute(f"USE {DB}")
        # 重复索引
        cur.execute("CREATE TABLE t_dup(id INT PRIMARY KEY, a INT, KEY k1(a), KEY k2(a))")
        # 前缀冗余
        cur.execute("CREATE TABLE t_prefix(id INT PRIMARY KEY, a INT, b INT, KEY ka(a), KEY kab(a,b))")
        # 低区分度：status 只有 2 种值
        cur.execute("CREATE TABLE t_lowsel(id INT PRIMARY KEY, status INT, KEY ks(status))")
        for i in range(1, 401):
            cur.execute("INSERT INTO t_lowsel VALUES(%s,%s)", (i, i % 2))
        # 单表索引过多（9 个）
        cols = ", ".join(f"c{i} INT" for i in range(9))
        keys = ", ".join(f"KEY k{i}(c{i})" for i in range(9))
        cur.execute(f"CREATE TABLE t_manyidx(id INT PRIMARY KEY, {cols}, {keys})")
        # 自增耗尽：tinyint auto_increment，AUTO_INCREMENT=120/127≈94%
        cur.execute("CREATE TABLE t_ai(id TINYINT AUTO_INCREMENT PRIMARY KEY, v INT)")
        cur.execute("INSERT INTO t_ai(v) VALUES(1)")
        cur.execute("ALTER TABLE t_ai AUTO_INCREMENT=120")
        conn.commit()
        for t in ("t_dup", "t_prefix", "t_lowsel", "t_manyidx", "t_ai"):
            cur.execute(f"ANALYZE TABLE {DB}.{t}")
        conn.commit()
        conn.close()
        cls.pool = TDSQLConnectionPool(TDSQLConnectionConfig(
            host=_HOST, port=_PORT, user=_USER, password=_PASS, database=DB))

    def test_analyze_findings(self):
        from backend.services import index_audit_service as svc
        res = svc.analyze(self.pool, database=DB)
        types = {(f["table_name"], f["finding_type"]) for f in res["findings"]}
        assert ("t_dup", "重复索引") in types
        assert ("t_prefix", "前缀冗余索引") in types
        assert ("t_manyidx", "单表索引过多") in types
        assert ("t_lowsel", "低区分度索引") in types
        assert ("t_ai", "自增耗尽风险") in types
        # 自增94% → ERROR
        ai = [f for f in res["findings"] if f["table_name"] == "t_ai" and f["finding_type"] == "自增耗尽风险"]
        assert ai and ai[0]["severity"] == "ERROR"
        # 严重度只三级
        assert all(f["severity"] in ("ERROR", "WARNING", "INFO") for f in res["findings"])

    def test_run_audit_persists(self):
        os.environ.setdefault("SQLCHECK_DB_NAME", "tdsql_sqlcheck_test")
        from backend.services.database import ensure_db
        from backend.services import index_audit_service as svc
        ensure_db()
        res = svc.run_audit(self.pool, connection_id="qa", database=DB)
        assert res["total_findings"] >= 5
        fs = svc.get_findings(res["audit_id"])
        assert len(fs) == res["total_findings"]
        errs = svc.get_findings(res["audit_id"], severity="ERROR")
        assert all(f["severity"] == "ERROR" for f in errs)
