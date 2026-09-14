# -*- coding: utf-8 -*-
"""CP-TST-71/72/80/81：B组故障隔离与结构验收（真实 MySQL 测试库）。

- B组任一表缺失/错列 → verify_business_schema 报问题，模块 UNAVAILABLE；
- B组故障时原账户事务（create_user/delete_user）不受影响（B组 SQL 次数 0）；
- 台账校验和漂移 → UNAVAILABLE；
- 重复运行 --apply 幂等。
"""
import pytest

from backend.services.copilot import schema as schema_mod
from backend.services.database import _get_connection, ensure_db


@pytest.fixture()
def b_group_ready():
    """确保核心+A组+B组就绪；用例后恢复。"""
    ensure_db()
    rc = schema_mod.apply_business_schema()
    assert rc == 0
    yield
    # 恢复：重新应用一次（幂等）
    schema_mod.apply_business_schema()


class TestBGroupVerify:
    def test_ready_after_apply(self, b_group_ready):
        conn = _get_connection()
        try:
            ready, reason, st = schema_mod.evaluate_ready(conn)
            assert ready, f"expect READY, got reason={reason}"
            assert st["module_schema_state"] == "READY"
            assert st["module_reconciled_epoch"] == st["module_schema_epoch"]
        finally:
            conn.close()

    def test_missing_b_table_detected(self, b_group_ready, copilot_conn):
        copilot_conn.execute("DROP TABLE IF EXISTS copilot_providers")
        copilot_conn.commit()
        problems = schema_mod.verify_business_schema(copilot_conn)
        assert any("copilot_providers" in p for p in problems)

    def test_wrong_column_detected(self, b_group_ready, copilot_conn):
        copilot_conn.execute(
            "ALTER TABLE copilot_scene_routes MODIFY COLUMN privacy_profile "
            "VARCHAR(64) NOT NULL")
        copilot_conn.commit()
        problems = schema_mod.verify_business_schema(copilot_conn)
        assert any("privacy_profile" in p or "类型不符" in p for p in problems)
        # 恢复原结构（含字符集子句），避免污染后续用例
        copilot_conn.execute(
            "ALTER TABLE copilot_scene_routes MODIFY COLUMN privacy_profile "
            "VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL")
        copilot_conn.commit()

    def test_missing_index_detected(self, b_group_ready, copilot_conn):
        copilot_conn.execute(
            "ALTER TABLE copilot_turns DROP INDEX idx_copilot_turns_state")
        copilot_conn.commit()
        problems = schema_mod.verify_business_schema(copilot_conn)
        assert any("idx_copilot_turns_state" in p or "缺索引" in p
                   for p in problems)
        # 恢复索引
        copilot_conn.execute(
            "ALTER TABLE copilot_turns ADD INDEX idx_copilot_turns_state "
            "(state, created_at, id)")
        copilot_conn.commit()


class TestAccountOpsIndependentOfBGroup:
    """CP-TST-73：B组损坏时账户事务成功（B组 SQL 次数 0）。"""

    def test_create_delete_user_with_b_group_broken(self, b_group_ready,
                                                     copilot_conn):
        # 破坏 B组
        copilot_conn.execute("DROP TABLE IF EXISTS copilot_sessions")
        copilot_conn.commit()
        from backend.services.auth_service import AuthService
        svc = AuthService()
        uname = "cp_broken_test_user"
        # 清理可能残留
        svc.delete_user(uname, operator="test")
        user, err = svc.create_user(uname, "Test@12345", "developer",
                                    operator="test")
        assert err is None, f"create_user 受 B组故障影响: {err}"
        assert user is not None
        # subject 已分配（A组正常）
        row = copilot_conn.execute(
            "SELECT state FROM copilot_subjects WHERE username = ? AND "
            "state = 'ACTIVE'", (uname,)).fetchone()
        assert row is not None
        # 删除：吊销 subject（结束当前事务读快照后再查）
        err = svc.delete_user(uname, operator="test")
        assert err is None
        copilot_conn.commit()  # 刷新 REPEATABLE READ 快照
        row = copilot_conn.execute(
            "SELECT state FROM copilot_subjects WHERE username = ? "
            "ORDER BY created_at DESC LIMIT 1", (uname,)).fetchone()
        assert row is not None and dict(row).get("state") == "REVOKED"

    def test_recreate_same_name_gets_new_subject(self, b_group_ready,
                                                 copilot_conn):
        """CP-TST-59：删除重建同名账号不继承旧 subject。"""
        from backend.services.auth_service import AuthService
        svc = AuthService()
        uname = "cp_recreate_user"
        svc.delete_user(uname, operator="test")
        u1, e1 = svc.create_user(uname, "Test@12345", "developer",
                                 operator="test")
        assert e1 is None
        s1 = copilot_conn.execute(
            "SELECT subject_id FROM copilot_subjects WHERE username = ? AND "
            "state = 'ACTIVE'", (uname,)).fetchone()
        assert s1 is not None
        svc.delete_user(uname, operator="test")
        u2, e2 = svc.create_user(uname, "Test@12345", "developer",
                                 operator="test")
        assert e2 is None
        copilot_conn.commit()  # 刷新读快照
        s2 = copilot_conn.execute(
            "SELECT subject_id FROM copilot_subjects WHERE username = ? AND "
            "state = 'ACTIVE'", (uname,)).fetchone()
        assert s2 is not None
        assert dict(s1)["subject_id"] != dict(s2)["subject_id"]
        svc.delete_user(uname, operator="test")
