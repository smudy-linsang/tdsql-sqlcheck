# -*- coding: utf-8 -*-
"""B-02 回归锁：B 组表运行期丢失 → 端点 503 COPILOT_SCHEMA_UNAVAILABLE、
状态置 UNAVAILABLE 并推进 epoch、不裸 500 穿透。"""
import pytest

from backend.services.copilot import schema as schema_mod
from backend.services.copilot.errors import CopilotError


class TestRuntimeStructuralGuard:
    def test_missing_table_turns_to_503(self, client, copilot_db):
        """运行期删除 copilot_sessions：GET /sessions 必须 503 且状态失效。"""
        from backend.services.database import _get_connection
        tok = _make_user_with_token("cp_b02_user", "developer")
        h = {"Authorization": f"Bearer {tok}"}
        # 破坏 B 组表
        conn = _get_connection()
        try:
            conn.execute("DROP TABLE IF EXISTS copilot_sessions")
            conn.commit()
        finally:
            conn.close()
        try:
            r = client.get("/api/v1/copilot/sessions", headers=h)
            assert r.status_code == 503, f"期望 503，实际 {r.status_code}: {r.text[:200]}"
            body = r.json()
            assert body["detail"]["code"] == "COPILOT_SCHEMA_UNAVAILABLE"
            # 状态应已失效（A 组 UNAVAILABLE）
            conn = _get_connection()
            try:
                st = schema_mod.read_module_state(conn)
                assert st["module_schema_state"] == "UNAVAILABLE"
            finally:
                conn.close()
        finally:
            # 恢复
            conn = _get_connection()
            try:
                schema_mod.apply_business_schema()
            finally:
                conn.close()

    def test_is_structural_error_classification(self):
        import pymysql
        assert schema_mod.is_structural_error(
            pymysql.err.ProgrammingError(1146, "Table doesn't exist"))
        assert schema_mod.is_structural_error(
            pymysql.err.ProgrammingError(1054, "Unknown column"))
        assert not schema_mod.is_structural_error(
            pymysql.err.OperationalError(2006, "MySQL server has gone away"))
        assert not schema_mod.is_structural_error(ValueError("x"))


def _make_user_with_token(username: str, role: str = "developer"):
    import time
    from backend.services.auth_service import auth_service, issue_token
    auth_service.delete_user(username, operator="test")
    user, err = auth_service.create_user(username, "Test@12345", role,
                                         operator="test")
    assert err is None
    time.sleep(1.1)
    user = auth_service.get_user(username, use_cache=False)
    return issue_token(username, role, int(user.get("token_version", 0) or 0))
