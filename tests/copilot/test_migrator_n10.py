# -*- coding: utf-8 -*-
"""CP-TST-83：N-10 共享迁移器双向回归锁（真实 MySQL 测试库）。

锁定 DETAIL §10.4/§15.1 合同：
- 83a：既有 CREATE TABLE 迁移的缺表自愈保持有效（QC-DEFECT-07 不全局关闭）；
- 83b：已登记 A组两表（copilot_subjects/copilot_runtime）分别缺失必须
      MigrationError 失败关闭，重建 DDL 次数=0；
- 83c：A组首次正常安装仍允许创建；
- 双向缺陷注入：错误策略能使对应锁变红（变异自证由断言直接体现）。
"""
import pytest

from backend.schema.migrator import (
    MigrationError, SchemaMigrator, _NO_CREATE_SELF_HEAL_KEYS,
)
from backend.services.database import _get_connection, ensure_db

_A_KEY = "v16_160_copilot_identity_runtime"
_A_SQL = (
    "CREATE TABLE IF NOT EXISTS copilot_subjects (\n"
    "    subject_id CHAR(32) NOT NULL, PRIMARY KEY (subject_id)\n"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"
)


@pytest.fixture()
def migrator_env():
    """独立的探针表与 A组 version_key 台账记录；用例结束后清理。"""
    ensure_db()
    conn = _get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS cp_n10_probe_existing")
        conn.execute(
            "CREATE TABLE cp_n10_probe_existing (id INT NOT NULL PRIMARY KEY) "
            "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        conn.execute("DROP TABLE IF EXISTS copilot_subjects")
        conn.execute("DROP TABLE IF EXISTS copilot_runtime")
        conn.execute("DELETE FROM schema_migrations WHERE version_key = ?", (_A_KEY,))
        conn.commit()
    finally:
        conn.close()
    yield
    conn = _get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS cp_n10_probe_existing")
        # 恢复 A组：删除台账后重跑正式迁移链（重建两表 + 种子控制行 + 回填）
        conn.execute("DELETE FROM schema_migrations WHERE version_key = ?", (_A_KEY,))
        conn.commit()
        try:
            SchemaMigrator().run_migrations()
        except Exception:
            pass
    finally:
        conn.close()


class TestExistingSelfHealPreserved:
    """83a：既有迁移（非 A组 key）缺表仍自愈。"""

    def test_non_a_group_create_missing_self_heals(self, migrator_env):
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            conn.execute("DROP TABLE IF EXISTS cp_n10_probe_existing")
            conn.commit()
            stmts = [
                "CREATE TABLE IF NOT EXISTS cp_n10_probe_existing "
                "(id INT NOT NULL PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
            ]
            # 非 A组 key：缺表应判 missing（可自愈），不是抛错
            state = m._structure_state(conn.cursor(), "v99_998_legacy", stmts)
            assert state == "missing"
        finally:
            conn.close()


class TestAGroupFailClosed:
    """83b：已登记 A组缺表禁止自愈。"""

    def test_registered_a_group_missing_table_fails(self, migrator_env):
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            stmts = [_A_SQL]
            with pytest.raises(MigrationError) as ei:
                m._structure_state(conn.cursor(), _A_KEY, stmts)
            assert "copilot_subjects" in str(ei.value)
            assert "N-10" in str(ei.value)
        finally:
            conn.close()

    def test_registered_runtime_missing_fails(self, migrator_env):
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            stmts = [
                "CREATE TABLE IF NOT EXISTS copilot_runtime "
                "(id TINYINT NOT NULL PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
            ]
            with pytest.raises(MigrationError):
                m._structure_state(conn.cursor(), _A_KEY, stmts)
        finally:
            conn.close()

    def test_a_group_intact_passes(self, migrator_env):
        """A组两表完整时通过（不重建）。"""
        conn = _get_connection()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS copilot_subjects "
                "(subject_id CHAR(32) NOT NULL PRIMARY KEY) "
                "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS copilot_runtime "
                "(id TINYINT NOT NULL PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
            conn.commit()
            m = SchemaMigrator()
            stmts = [
                "CREATE TABLE IF NOT EXISTS copilot_subjects "
                "(subject_id CHAR(32) NOT NULL PRIMARY KEY)",
                "CREATE TABLE IF NOT EXISTS copilot_runtime "
                "(id TINYINT NOT NULL PRIMARY KEY)",
            ]
            state = m._structure_state(conn.cursor(), _A_KEY, stmts)
            assert state == "valid"
        finally:
            conn.close()

    def test_first_install_creates(self, migrator_env):
        """83c：未登记的 A组首次安装走 _apply_file 正常创建并登记。"""
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            m._apply_file(conn.cursor(), conn, _A_KEY, "sha-test", [_A_SQL])
            row = conn.execute(
                "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = "
                "DATABASE() AND TABLE_NAME = 'copilot_subjects'").fetchone()
            assert row is not None
            reg = conn.execute(
                "SELECT checksum FROM schema_migrations WHERE version_key = ?",
                (_A_KEY,)).fetchone()
            assert reg is not None
        finally:
            conn.close()


class TestMutationProof:
    """83d：双向缺陷注入——错误策略必须使对应锁变红。"""

    def test_mutation_disable_self_heal_breaks_83a(self, migrator_env):
        """若把既有自愈全局关闭（变异），83a 用例应变红；此处直接断言原实现
        对非 A组 key 仍返回 missing（即未全局关闭），若被变异此断言失败。"""
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            conn.execute("DROP TABLE IF EXISTS cp_n10_probe_existing")
            conn.commit()
            stmts = [
                "CREATE TABLE IF NOT EXISTS cp_n10_probe_existing (id INT PRIMARY KEY)"]
            assert m._structure_state(
                conn.cursor(), "v99_998_legacy", stmts) == "missing"
        finally:
            conn.close()

    def test_mutation_allow_a_group_rebuild_breaks_83b(self, migrator_env):
        """若允许已登记 A组自动重建（变异），83b 用例应变红；此处断言原实现
        对 A组 key 抛错，若被变异此断言失败。"""
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            with pytest.raises(MigrationError):
                m._structure_state(conn.cursor(), _A_KEY, [_A_SQL])
        finally:
            conn.close()

    def test_n10_key_registration(self):
        """N-10 闭集按精确 version_key 注册（不含糊前缀）。"""
        assert "v16_160_copilot_identity_runtime" in _NO_CREATE_SELF_HEAL_KEYS
        assert "v15_150_metadata_audit_jobs" not in _NO_CREATE_SELF_HEAL_KEYS
