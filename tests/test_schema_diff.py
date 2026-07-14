# -*- coding: utf-8 -*-
"""M3 · G6 表结构比对 回归测试"""
import os
import pytest

from backend.services import schema_diff_service as svc
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


# ── 比对逻辑（纯函数，覆盖全严重度矩阵）────────────────────────────
def test_diff_structures_matrix():
    left = {"bank": {
        "t_keep": {"columns": {"id": ("id", "int"), "a": ("a", "varchar(10)")},
                   "indexes": {"PRIMARY": ["id"], "idx_a": ["a"]}},
        "t_only_left": {"columns": {"id": ("id", "int")}, "indexes": {}},
    }}
    right = {"bank": {
        "t_keep": {"columns": {"id": ("id", "int"), "a": ("a", "varchar(20)")},  # 类型不一致
                   "indexes": {"PRIMARY": ["id"]}},  # idx_a 缺失
        "t_only_right": {"columns": {"id": ("id", "int")}, "indexes": {}},  # 表多余
    }}
    items = svc.diff_structures(left, right)
    byt = {(i["table_name"], i["diff_type"]): i for i in items}
    assert byt[("t_only_left", "表缺失(右侧缺)")]["severity"] == "ERROR"      # CRITICAL→ERROR
    assert byt[("t_only_right", "表多余(右侧多)")]["severity"] == "INFO"
    assert byt[("t_keep", "列类型不一致")]["severity"] == "WARNING"          # MEDIUM→WARNING
    assert byt[("t_keep", "索引缺失(右侧缺)")]["severity"] == "ERROR"        # CRITICAL→ERROR
    # 列缺失=HIGH→ERROR
    left2 = {"d": {"t": {"columns": {"id": ("id", "int"), "x": ("x", "int")}, "indexes": {}}}}
    right2 = {"d": {"t": {"columns": {"id": ("id", "int")}, "indexes": {}}}}
    it2 = svc.diff_structures(left2, right2)
    assert it2[0]["diff_type"] == "列缺失(右侧缺)" and it2[0]["severity"] == "ERROR"


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP)
class TestSchemaDiffDB:

    @classmethod
    def setup_class(cls):
        conn = pymysql.connect(host=_HOST, port=_PORT, user=_USER, password=_PASS)
        cur = conn.cursor()
        cur.execute("DROP DATABASE IF EXISTS sdiff_prod")
        cur.execute("CREATE DATABASE sdiff_prod")
        cur.execute("USE sdiff_prod")
        cur.execute("CREATE TABLE t(id INT PRIMARY KEY, name VARCHAR(32), KEY idx_name(name))")
        conn.commit()
        conn.close()
        cls.pool = TDSQLConnectionPool(TDSQLConnectionConfig(
            host=_HOST, port=_PORT, user=_USER, password=_PASS, database="sdiff_prod"))

    def test_collect_structure(self):
        st = svc.collect_structure(self.pool, databases=["sdiff_prod"])
        assert "sdiff_prod" in st
        t = st["sdiff_prod"]["t"]
        assert "id" in t["columns"] and "name" in t["columns"]
        assert "PRIMARY" in t["indexes"] and "idx_name" in t["indexes"]

    def test_run_diff_identical_persists(self):
        os.environ.setdefault("SQLCHECK_DB_NAME", "tdsql_sqlcheck_test")
        from backend.services.database import ensure_db
        ensure_db()
        # 同库自比 → 0 差异，验证落库路径
        res = svc.run_diff(self.pool, self.pool, databases=["sdiff_prod"],
                           left_conn="prod", right_conn="test")
        assert res["total_items"] == 0
        assert svc.get_items(res["diff_id"]) == []
