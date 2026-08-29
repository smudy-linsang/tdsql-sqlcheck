# -*- coding: utf-8 -*-
"""UAT-O-20 回归测试：跨库 EXPLAIN 临时池生命周期全路径关闭

覆盖 O 第四轮报告 O-20（MINOR）：
临时池一经创建即进入单一 try/finally，覆盖验证、预处理、执行、分析、返回；
对预处理每一步做异常注入，全部断言 close_all() 恰为 1 次。
"""
import pytest
from unittest.mock import patch

from backend.services.slow_query_service import SlowQueryService


class _Cursor:
    description = [("id",)]

    def execute(self, sql):
        return None

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return [(1,)]


class _Connection:
    def cursor(self):
        cursor = _Cursor()

        class _Ctx:
            def __enter__(self):
                return cursor

            def __exit__(self, *args):
                return False

        return _Ctx()


class _Pool:
    instances = []

    def __init__(self, cfg):
        self.closed = 0
        _Pool.instances.append(self)

    def get_connection(self):
        conn = _Connection()

        class _Ctx:
            def __enter__(self):
                return conn

            def __exit__(self, *args):
                return False

        return _Ctx()

    def close_all(self):
        self.closed += 1


_SAVED = {
    "database": "default_db", "host": "127.0.0.1", "port": 3306,
    "username": "synthetic", "password_encrypted": "synthetic",
    "charset": "utf8mb4",
}


@pytest.fixture(autouse=True)
def _reset_pool_instances():
    _Pool.instances.clear()
    yield
    _Pool.instances.clear()


def _patched_service():
    return object.__new__(SlowQueryService)


def _base_patches():
    return [
        patch("backend.services.connection_registry.registry.get_saved", return_value=_SAVED),
        patch("backend.services.security_service.decrypt_password", return_value="synthetic"),
        patch("backend.services.tdsql_connector.TDSQLConnectionPool", _Pool),
    ]


class TestPreprocessFailureClosesPool:
    """预处理阶段任意一步抛异常，临时池都必须被关闭且仅关闭一次"""

    def test_preprocess_exception_closes_pool(self):
        svc = _patched_service()
        p1, p2, p3 = _base_patches()
        with p1, p2, p3, patch(
                "backend.services.slow_query_service.re.sub",
                side_effect=RuntimeError("synthetic preprocess failure")):
            with pytest.raises(RuntimeError):
                svc.analyze_explain_by_sql("SELECT 1", "synthetic", "other_db")
        assert len(_Pool.instances) == 1, "临时池必须已创建"
        assert _Pool.instances[0].closed == 1, \
            f"预处理异常后必须关闭临时池一次，实际 {_Pool.instances[0].closed}"

    def test_balance_paren_step_failure_closes_pool(self):
        """模拟括号平衡步骤失败（split 抛异常）"""
        svc = _patched_service()
        p1, p2, p3 = _base_patches()
        with p1, p2, p3, patch(
                "backend.services.slow_query_service.re.split",
                side_effect=MemoryError("synthetic split failure")):
            with pytest.raises(MemoryError):
                svc.analyze_explain_by_sql("SELECT (a FROM t", "synthetic", "other_db")
        assert _Pool.instances[0].closed == 1

    def test_execute_step_failure_closes_pool(self):
        """执行阶段异常（EXPLAIN 执行失败）同样关池一次"""
        svc = _patched_service()
        p1, p2, p3 = _base_patches()

        class _FailCursor(_Cursor):
            def execute(self, sql):
                if str(sql).upper().startswith("EXPLAIN"):
                    raise RuntimeError("synthetic explain failure")
                return super().execute(sql)

        class _FailConnection(_Connection):
            def cursor(self):
                cursor = _FailCursor()

                class _Ctx:
                    def __enter__(self):
                        return cursor

                    def __exit__(self, *args):
                        return False

                return _Ctx()

        class _FailPool(_Pool):
            def get_connection(self):
                conn = _FailConnection()

                class _Ctx:
                    def __enter__(self):
                        return conn

                    def __exit__(self, *args):
                        return False

                return _Ctx()

        with p1, p2, patch("backend.services.tdsql_connector.TDSQLConnectionPool", _FailPool):
            with pytest.raises(RuntimeError):
                svc.analyze_explain_by_sql("SELECT 1 FROM t", "synthetic", "other_db")
        assert _Pool.instances[0].closed == 1


class TestValidationFailureClosesPool:
    """验证阶段失败：友好错误映射保留，且临时池被关闭"""

    def test_unknown_database_maps_value_error_and_closes(self):
        svc = _patched_service()
        p1, p2, p3 = _base_patches()

        class _DenyPool(_Pool):
            def get_connection(self):
                raise Exception("(1049, \"Unknown database 'ghost_db'\")")

        with p1, p2, patch("backend.services.tdsql_connector.TDSQLConnectionPool", _DenyPool):
            with pytest.raises(ValueError) as ei:
                svc.analyze_explain_by_sql("SELECT 1", "synthetic", "ghost_db")
        assert "数据库不存在" in str(ei.value)
        assert _Pool.instances[0].closed == 1, "验证失败也必须关闭临时池"

    def test_connection_failure_maps_value_error_and_closes(self):
        svc = _patched_service()
        p1, p2, p3 = _base_patches()

        class _DenyPool(_Pool):
            def get_connection(self):
                raise Exception("(2003, \"Can't connect to MySQL server\")")

        with p1, p2, patch("backend.services.tdsql_connector.TDSQLConnectionPool", _DenyPool):
            with pytest.raises(ValueError) as ei:
                svc.analyze_explain_by_sql("SELECT 1", "synthetic", "other_db")
        assert "连接实例失败" in str(ei.value)
        assert _Pool.instances[0].closed == 1
