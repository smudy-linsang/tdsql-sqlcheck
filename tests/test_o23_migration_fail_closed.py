# -*- coding: utf-8 -*-
"""UAT-O-23 回归测试：迁移器失败关闭

覆盖 O 第五轮报告 O-23（MAJOR）的四类场景（真实 MySQL 测试库）：
1. 列缺失 → 正常补齐并登记版本键；
2. 列已存在且结构相符 → 幂等跳过（不靠吞 Duplicate column）；结构不符 → 失败关闭；
3. 首/次 ALTER 注入失败（锁超时/权限）→ 抛 MigrationError，绝不写版本键，下次可重试；
4. 并发启动：两个“worker”同时应用同一迁移 → 最终表结构一致且版本记录只有一条。
"""
import threading

import pytest

from backend.services.database import _get_connection, ensure_db
from backend.schema.migrator import MigrationError, SchemaMigrator

_PROBE_TABLE = "o23_probe_migrate"
_KEY = "v99_998_o23_synthetic"


@pytest.fixture()
def probe_env():
    """独立的探针表与版本键；用例结束后清理"""
    ensure_db()
    conn = _get_connection()
    try:
        conn.execute(f"DROP TABLE IF EXISTS `{_PROBE_TABLE}`")
        conn.execute(f"CREATE TABLE `{_PROBE_TABLE}` (id INT PRIMARY KEY)")
        conn.execute("DELETE FROM schema_migrations WHERE version_key = %s", (_KEY,))
        conn.commit()
    finally:
        conn.close()
    yield
    conn = _get_connection()
    try:
        conn.execute(f"DROP TABLE IF EXISTS `{_PROBE_TABLE}`")
        conn.execute("DELETE FROM schema_migrations WHERE version_key = %s", (_KEY,))
        conn.commit()
    finally:
        conn.close()


def _stmts():
    return [
        f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN related_index_name VARCHAR(128) DEFAULT ''",
        f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN index_columns VARCHAR(512) DEFAULT ''",
    ]


def _version_row():
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE version_key = %s",
            (_KEY,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


class TestColumnIdempotency:
    def test_missing_columns_applied_and_recorded(self, probe_env):
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            cur = conn.cursor()
            m._apply_file(cur, conn, _KEY, "sha-x", _stmts())
        finally:
            conn.close()
        conn = _get_connection()
        try:
            info = m._column_info(conn.cursor(), _PROBE_TABLE, "related_index_name")
            assert info is not None and "varchar" in info["column_type"]
        finally:
            conn.close()
        assert _version_row() is not None, "成功后必须登记版本键"

    def test_existing_column_idempotent_skip(self, probe_env):
        """两个列都已存在且结构相符：幂等跳过并正常登记，不执行 ALTER"""
        conn = _get_connection()
        try:
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN related_index_name VARCHAR(128) DEFAULT ''")
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN index_columns VARCHAR(512) DEFAULT ''")
            conn.commit()
        finally:
            conn.close()
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            m._apply_file(conn.cursor(), conn, _KEY, "sha-x", _stmts())
        finally:
            conn.close()
        assert _version_row() is not None

    def test_partial_existing_column_only_adds_missing(self, probe_env):
        """仅一个字段存在：只补缺失列，已存在列严格验收"""
        conn = _get_connection()
        try:
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN related_index_name VARCHAR(128) DEFAULT ''")
            conn.commit()
        finally:
            conn.close()
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            m._apply_file(conn.cursor(), conn, _KEY, "sha-x", _stmts())
        finally:
            conn.close()
        conn = _get_connection()
        try:
            assert m._column_info(conn.cursor(), _PROBE_TABLE, "index_columns") is not None
        finally:
            conn.close()
        assert _version_row() is not None

    def test_structure_mismatch_fails_closed(self, probe_env):
        """已存在列结构与设计不符：失败关闭，不写版本键"""
        conn = _get_connection()
        try:
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN related_index_name INT")
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN index_columns VARCHAR(512) DEFAULT ''")
            conn.commit()
        finally:
            conn.close()
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            with pytest.raises(MigrationError):
                m._apply_file(conn.cursor(), conn, _KEY, "sha-x", _stmts())
        finally:
            conn.close()
        assert _version_row() is None, "结构不符时绝不写版本键"


class TestFailureInjectionFailClosed:
    def test_first_alter_failure_no_version_record(self, probe_env, monkeypatch):
        """首条 ALTER 注入锁超时：抛错、无版本键、可重试"""
        conn = _get_connection()
        m = SchemaMigrator()
        cur = conn.cursor()
        real_execute = cur.execute

        def flaky(sql, params=None):
            if "ADD COLUMN" in str(sql):
                raise Exception("(1205, 'Lock wait timeout exceeded')")
            return real_execute(sql, params)

        monkeypatch.setattr(cur, "execute", flaky)
        try:
            with pytest.raises(MigrationError):
                m._apply_file(cur, conn, _KEY, "sha-x", _stmts())
        finally:
            monkeypatch.undo()
            conn.close()
        assert _version_row() is None, "失败时绝不写版本键"
        # 恢复条件后重试成功
        m2 = SchemaMigrator()
        conn = _get_connection()
        try:
            m2._apply_file(conn.cursor(), conn, _KEY, "sha-x", _stmts())
        finally:
            conn.close()
        assert _version_row() is not None, "恢复后重试必须成功补齐"

    def test_second_alter_failure_no_version_record(self, probe_env, monkeypatch):
        """次条 ALTER 注入权限错误：同样失败关闭"""
        conn = _get_connection()
        m = SchemaMigrator()
        cur = conn.cursor()
        real_execute = cur.execute

        def flaky(sql, params=None):
            if "index_columns" in str(sql) and "ADD COLUMN" in str(sql):
                raise Exception("(1142, 'ALTER command denied to user')")
            return real_execute(sql, params)

        monkeypatch.setattr(cur, "execute", flaky)
        try:
            with pytest.raises(MigrationError):
                m._apply_file(cur, conn, _KEY, "sha-x", _stmts())
        finally:
            monkeypatch.undo()
            conn.close()
        assert _version_row() is None


class TestStructureStateMachine:
    """v1.6.2.2-UAT-O-26：已登记迁移的启动路径结构验收矩阵"""

    @pytest.fixture()
    def both_columns(self, probe_env):
        """预建两个列（结构与设计完全一致）"""
        conn = _get_connection()
        try:
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN related_index_name VARCHAR(128) DEFAULT ''")
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN index_columns VARCHAR(512) DEFAULT ''")
            conn.commit()
        finally:
            conn.close()
        yield

    def test_valid_structure_returns_valid(self, both_columns):
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            assert m._structure_state(conn.cursor(), _KEY, _stmts()) == "valid"
        finally:
            conn.close()

    def test_missing_column_returns_missing(self, probe_env):
        """缺列 → missing（可幂等补齐，不是失败关闭场景）"""
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            assert m._structure_state(conn.cursor(), _KEY, _stmts()) == "missing"
        finally:
            conn.close()

    def test_wrong_type_fails_closed(self, probe_env):
        """错误类型：失败关闭（O-26 核心反例：INT 而非 VARCHAR）"""
        conn = _get_connection()
        try:
            conn.execute(f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN related_index_name INT")
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN index_columns VARCHAR(512) DEFAULT ''")
            conn.commit()
        finally:
            conn.close()
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            with pytest.raises(MigrationError):
                m._structure_state(conn.cursor(), _KEY, _stmts())
        finally:
            conn.close()

    def test_wrong_length_fails_closed(self, probe_env):
        """错误长度（VARCHAR(64) vs 设计 128）：失败关闭"""
        conn = _get_connection()
        try:
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN related_index_name VARCHAR(64) DEFAULT ''")
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN index_columns VARCHAR(512) DEFAULT ''")
            conn.commit()
        finally:
            conn.close()
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            with pytest.raises(MigrationError):
                m._structure_state(conn.cursor(), _KEY, _stmts())
        finally:
            conn.close()

    def test_wrong_default_fails_closed(self, probe_env):
        """错误默认值（DEFAULT 'x' vs 设计 ''）：失败关闭"""
        conn = _get_connection()
        try:
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN related_index_name VARCHAR(128) DEFAULT 'x'")
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN index_columns VARCHAR(512) DEFAULT ''")
            conn.commit()
        finally:
            conn.close()
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            with pytest.raises(MigrationError):
                m._structure_state(conn.cursor(), _KEY, _stmts())
        finally:
            conn.close()

    def test_wrong_nullable_fails_closed(self, probe_env):
        """错误可空性（NOT NULL vs 设计默认 NULL）：失败关闭"""
        conn = _get_connection()
        try:
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN related_index_name VARCHAR(128) NOT NULL DEFAULT ''")
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN index_columns VARCHAR(512) DEFAULT ''")
            conn.commit()
        finally:
            conn.close()
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            with pytest.raises(MigrationError):
                m._structure_state(conn.cursor(), _KEY, _stmts())
        finally:
            conn.close()


class TestChecksumDriftHandling:
    """v1.6.2.2-UAT-O-26：checksum 漂移默认失败关闭，显式调和须结构验收通过"""

    def test_drift_fails_closed_without_whitelist(self, probe_env, monkeypatch):
        monkeypatch.delenv("SCHEMA_CHECKSUM_RECONCILE", raising=False)
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            with pytest.raises(MigrationError) as ei:
                m._reconcile_checksum(conn.cursor(), conn, _KEY, "old-sha", "new-sha",
                                      _stmts())
        finally:
            conn.close()
        assert "漂移" in str(ei.value)
        assert "SCHEMA_CHECKSUM_RECONCILE" in str(ei.value)

    def test_reconcile_requires_valid_structure(self, probe_env, monkeypatch):
        """白名单内但结构缺失（列不存在）→ 拒绝调和"""
        monkeypatch.setenv("SCHEMA_CHECKSUM_RECONCILE", _KEY)
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            with pytest.raises(MigrationError):
                m._reconcile_checksum(conn.cursor(), conn, _KEY, "old-sha", "new-sha",
                                      _stmts())
        finally:
            conn.close()

    def test_reconcile_rebaselines_when_structure_valid(self, probe_env, monkeypatch):
        """白名单内且结构验收通过 → 重设基线"""
        conn = _get_connection()
        try:
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN related_index_name VARCHAR(128) DEFAULT ''")
            conn.execute(
                f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN index_columns VARCHAR(512) DEFAULT ''")
            conn.execute(
                "INSERT INTO schema_migrations (version_key, checksum) VALUES (%s, %s)",
                (_KEY, "old-sha"))
            conn.commit()
        finally:
            conn.close()
        monkeypatch.setenv("SCHEMA_CHECKSUM_RECONCILE", _KEY)
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            m._reconcile_checksum(conn.cursor(), conn, _KEY, "old-sha", "new-sha",
                                  _stmts())
        finally:
            conn.close()
        row = _version_row()
        assert row and row["checksum"] == "new-sha"


class TestSelfHealAndConcurrency:
    def test_legacy_false_applied_self_heals(self, probe_env):
        """历史假成功（有版本键、无列）→ _needs_reapply 判定需补齐"""
        conn = _get_connection()
        try:
            conn.execute(
                "INSERT INTO schema_migrations (version_key, checksum) VALUES (%s, %s)",
                (_KEY, "sha-x"))
            conn.commit()
        finally:
            conn.close()
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            assert m._needs_reapply(conn.cursor(), _stmts()) is True
        finally:
            conn.close()

    def test_concurrent_apply_exactly_one_version_row(self, probe_env):
        """双 worker 并发：两个线程同时应用同一迁移，版本记录只有一条"""
        errors = []

        def worker():
            try:
                m = SchemaMigrator()
                conn = _get_connection()
                try:
                    m._apply_file(conn.cursor(), conn, _KEY, "sha-x", _stmts())
                finally:
                    conn.close()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors, f"并发应用不得抛错: {errors}"
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT COUNT(*) AS c FROM schema_migrations WHERE version_key = %s",
                (_KEY,)).fetchone()
            assert dict(rows)["c"] == 1, "版本记录必须唯一"
            info = SchemaMigrator()._column_info(
                conn.cursor(), _PROBE_TABLE, "index_columns")
            assert info is not None, "最终表结构必须完整"
        finally:
            conn.close()
