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


class TestDefaultValueNormalization:
    """v1.6.2.2-UAT-O-29：默认值规范化矩阵——未声明/显式 NULL/有值必须逐一校验"""

    def _verify(self, column_ddl: str, migration_stmt: str, expect_valid: bool):
        """预建列（column_ddl），用迁移声明（migration_stmt）验收结构状态"""
        conn = _get_connection()
        try:
            conn.execute(f"DROP TABLE IF EXISTS `{_PROBE_TABLE}`")
            conn.execute(f"CREATE TABLE `{_PROBE_TABLE}` (id INT PRIMARY KEY)")
            conn.execute(f"ALTER TABLE `{_PROBE_TABLE}` ADD COLUMN {column_ddl}")
            conn.commit()
            m = SchemaMigrator()
            if expect_valid:
                assert m._structure_state(conn.cursor(), _KEY, [migration_stmt]) == "valid"
            else:
                with pytest.raises(MigrationError):
                    m._structure_state(conn.cursor(), _KEY, [migration_stmt])
        finally:
            conn.execute(f"DROP TABLE IF EXISTS `{_PROBE_TABLE}`")
            conn.commit()
            conn.close()

    def test_no_default_declared_wrong_existing_default_fails(self, probe_env):
        """O-29 核心反例：迁移未声明 DEFAULT，预存任意错误默认值必须失败关闭"""
        self._verify("note VARCHAR(32) DEFAULT 'unexpected'",
                     f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN note VARCHAR(32)",
                     expect_valid=False)

    def test_no_default_declared_null_default_valid(self, probe_env):
        """未声明 DEFAULT + 现存无默认值（NULL）→ valid"""
        self._verify("note VARCHAR(32)",
                     f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN note VARCHAR(32)",
                     expect_valid=True)

    def test_explicit_default_null_valid(self, probe_env):
        """显式 DEFAULT NULL + 现存 NULL → valid"""
        self._verify("note VARCHAR(32) DEFAULT NULL",
                     f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN note VARCHAR(32) DEFAULT NULL",
                     expect_valid=True)

    def test_explicit_default_null_vs_value_fails(self, probe_env):
        """显式 DEFAULT NULL 与现存有值不符 → 失败关闭"""
        self._verify("note VARCHAR(32) DEFAULT 'x'",
                     f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN note VARCHAR(32) DEFAULT NULL",
                     expect_valid=False)

    def test_empty_string_default_valid(self, probe_env):
        self._verify("note VARCHAR(32) DEFAULT ''",
                     f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN note VARCHAR(32) DEFAULT ''",
                     expect_valid=True)

    def test_numeric_default_valid(self, probe_env):
        self._verify("cnt INT DEFAULT 0",
                     f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN cnt INT DEFAULT 0",
                     expect_valid=True)

    def test_numeric_default_mismatch_fails(self, probe_env):
        self._verify("cnt INT DEFAULT 1",
                     f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN cnt INT DEFAULT 0",
                     expect_valid=False)

    def test_boolean_default_normalized(self, probe_env):
        """布尔默认值在整型列上的物化（TRUE/1）必须被规范化接受"""
        self._verify("flag TINYINT(1) DEFAULT TRUE",
                     f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN flag TINYINT(1) DEFAULT TRUE",
                     expect_valid=True)

    def test_string_default_with_space_valid(self, probe_env):
        self._verify("note VARCHAR(32) DEFAULT 'a b'",
                     f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN note VARCHAR(32) DEFAULT 'a b'",
                     expect_valid=True)

    def test_quoted_double_vs_single_normalized(self, probe_env):
        """双引号与单引号声明同一字符串默认值应视为一致（引号规范化）"""
        self._verify('note VARCHAR(32) DEFAULT "ok"',
                     f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN note VARCHAR(32) DEFAULT 'ok'",
                     expect_valid=True)

    def test_case_insensitive_keyword_default(self, probe_env):
        """关键字默认值大小写归一"""
        self._verify("flag TINYINT(1) DEFAULT TRUE",
                     f"ALTER TABLE {_PROBE_TABLE} ADD COLUMN flag TINYINT(1) DEFAULT true",
                     expect_valid=True)


def _ledger_row(key):
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE version_key = %s",
            (key,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


class TestChecksumDriftHandling:
    """v1.6.2.2-UAT-O-30：checksum 漂移走代码内精确账本的一次性调和闭环

    - 只有 {version_key, 历史 checksum, 当前 checksum} 精确三元组被接受；
    - 调和前验证业务结构不变量（uq_conn_endpoint 存在、uq_conn_name 已移除、无重复端点）；
    - 原子条件 UPDATE，双 worker 并发只有一个中标；
    - 未知组合与未来篡改一律失败关闭（不存在长期开关）。
    """

    _LKEY = "v9_090_connection_unique"
    _OLD = "54ee2e97c804f5d8ec216d9f51600c19cc8463f2cede1de07fa67635abe6de28"
    _NEW = "c6cf33bb385456fef12af3d4888ea6b22dcfc2a64052d734adc4c37457915209"

    @pytest.fixture()
    def drift_row(self):
        """在真实账本键 v9_090 上模拟漂移（保存现状、置为历史 checksum、用后恢复）"""
        ensure_db()
        saved = None
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT checksum FROM schema_migrations WHERE version_key = %s",
                (self._LKEY,)).fetchone()
            saved = dict(row)["checksum"] if row else None
            conn.execute(
                "UPDATE schema_migrations SET checksum = %s WHERE version_key = %s",
                (self._OLD, self._LKEY))
            conn.commit()
        finally:
            conn.close()
        yield
        conn = _get_connection()
        try:
            if saved is not None:
                conn.execute(
                    "UPDATE schema_migrations SET checksum = %s WHERE version_key = %s",
                    (saved, self._LKEY))
            conn.commit()
        finally:
            conn.close()

    def test_unknown_triple_fails_closed(self, drift_row):
        """未知 old/new 组合一律失败关闭（探针键不在账本内）"""
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            with pytest.raises(MigrationError) as ei:
                m._auto_reconcile(conn.cursor(), conn, _KEY, "unknown-old", "unknown-new")
        finally:
            conn.close()
        assert "不在已知调和账本" in str(ei.value)

    def test_wrong_current_checksum_fails_closed(self, drift_row):
        """账本内的 key + 正确历史值，但当前文件被篡改（新值不符）→ 失败关闭
        注：账本键记录已置为历史 checksum，调用后记录必须保持不变"""
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            with pytest.raises(MigrationError):
                m._auto_reconcile(conn.cursor(), conn, self._LKEY, self._OLD, "tampered-new")
        finally:
            conn.close()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT checksum FROM schema_migrations WHERE version_key = %s",
                (self._LKEY,)).fetchone()
            assert dict(row)["checksum"] == self._OLD, "失败关闭后记录不得被改写"
        finally:
            conn.close()

    def test_exact_triple_reconciles_once(self, drift_row):
        """精确三元组 + 结构不变量满足 → 原子调和一次成功"""
        m = SchemaMigrator()
        conn = _get_connection()
        try:
            m._auto_reconcile(conn.cursor(), conn, self._LKEY, self._OLD, self._NEW)
        finally:
            conn.close()
        row = _ledger_row(self._LKEY)
        assert row and row["checksum"] == self._NEW
        # 调和完成后再被调用：属并发幂等场景（另一进程已完成），安全返回不重写
        conn = _get_connection()
        try:
            m._auto_reconcile(conn.cursor(), conn, self._LKEY, self._OLD, self._NEW)
        finally:
            conn.close()
        # 调和后的篡改场景：记录已是新值、文件再被改 → 不在账本，必须失败关闭
        conn = _get_connection()
        try:
            with pytest.raises(MigrationError):
                m._auto_reconcile(conn.cursor(), conn, self._LKEY, self._NEW,
                                  "tampered-after-reconcile")
        finally:
            conn.close()

    def test_invariants_violation_fails_closed(self, drift_row, monkeypatch):
        """结构不变量不满足（模拟 uq_conn_name 仍存在）→ 失败关闭"""
        m = SchemaMigrator()
        monkeypatch.setattr(SchemaMigrator, "_verify_090_invariants",
                            lambda self, cursor: ["名称唯一约束 uq_conn_name 未被移除"])
        conn = _get_connection()
        try:
            with pytest.raises(MigrationError) as ei:
                m._auto_reconcile(conn.cursor(), conn, self._LKEY, self._OLD, self._NEW)
        finally:
            conn.close()
        assert "不变量" in str(ei.value)
        # 未调和，记录仍为旧值
        row = _ledger_row(self._LKEY)
        assert row and row["checksum"] == self._OLD

    def test_concurrent_reconcile_single_write(self, drift_row):
        """双 worker 并发调和：恰好一次原子写入，两进程结果一致"""
        results = []
        errors = []

        def worker():
            try:
                m = SchemaMigrator()
                conn = _get_connection()
                try:
                    m._auto_reconcile(conn.cursor(), conn, self._LKEY, self._OLD, self._NEW)
                    results.append("ok")
                finally:
                    conn.close()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors, f"并发调和不得抛错: {errors}"
        assert results == ["ok", "ok"], "并发调和两进程都必须成功返回"
        row = _ledger_row(self._LKEY)
        assert row and row["checksum"] == self._NEW


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
