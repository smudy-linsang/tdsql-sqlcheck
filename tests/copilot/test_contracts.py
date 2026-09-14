# -*- coding: utf-8 -*-
"""CP-W02 contracts.py 单元测试：DDL 解析与完整结构验收（CP-TST-80 纯函数部分）。"""
import pytest

from backend.schema.contracts import (
    parse_create_table, verify_statements_contracts,
)

DDL1 = """
CREATE TABLE IF NOT EXISTS copilot_subjects (
    subject_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    username VARCHAR(64) NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    note TEXT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (subject_id),
    INDEX idx_user (username, state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


class TestParse:
    def test_parse_basic(self):
        c = parse_create_table(DDL1)
        assert c is not None
        assert c.table == "copilot_subjects"
        assert c.engine == "innodb"
        assert c.table_collate == "utf8mb4_unicode_ci"
        names = [col.name for col in c.columns]
        assert names == ["subject_id", "username", "state", "note", "created_at"]
        sid = c.columns[0]
        assert sid.col_type == "char(32)"
        assert sid.charset == "ascii"
        assert sid.collate == "ascii_bin"
        assert sid.not_null is True
        state = c.columns[2]
        assert state.has_default and state.default == "ACTIVE"
        note = c.columns[3]
        assert note.not_null is False
        # 索引
        idx = {i.name: i for i in c.indexes}
        assert "PRIMARY" in idx and idx["PRIMARY"].unique
        assert idx["idx_user"].columns == [("username", None), ("state", None)]

    def test_parse_not_create(self):
        assert parse_create_table("ALTER TABLE t ADD COLUMN c INT") is None

    def test_parse_index_prefix(self):
        ddl = """CREATE TABLE t (a VARCHAR(100), KEY k1 (a(10), b))"""
        # 缺 PRIMARY KEY，列 b 未定义——解析器只负责结构，不校验语义
        c = parse_create_table(ddl)
        idx = {i.name: i for i in c.indexes}
        assert idx["k1"].columns == [("a", 10), ("b", None)]


class TestVerifyAgainstDb:
    """对真实测试库做完整结构验收（缺列/错型/缺索引/缺表均失败关闭）。"""

    @pytest.fixture()
    def probe_table(self, copilot_conn):
        copilot_conn.execute("DROP TABLE IF EXISTS cp_contract_probe")
        copilot_conn.execute(
            "CREATE TABLE cp_contract_probe (id INT NOT NULL, "
            "name VARCHAR(64) NOT NULL DEFAULT '', state TINYINT NOT NULL DEFAULT 0, "
            "created_at DATETIME(6) NOT NULL, PRIMARY KEY (id), "
            "INDEX idx_name (name, state)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        copilot_conn.commit()
        yield
        copilot_conn.execute("DROP TABLE IF EXISTS cp_contract_probe")
        copilot_conn.commit()

    DDL = """CREATE TABLE cp_contract_probe (
        id INT NOT NULL,
        name VARCHAR(64) NOT NULL DEFAULT '',
        state TINYINT NOT NULL DEFAULT 0,
        created_at DATETIME(6) NOT NULL,
        PRIMARY KEY (id),
        INDEX idx_name (name, state)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""

    def test_full_match(self, probe_table, copilot_conn):
        problems = verify_statements_contracts(copilot_conn.cursor(), [self.DDL])
        assert problems == []

    def test_missing_table(self, copilot_conn):
        problems = verify_statements_contracts(
            copilot_conn.cursor(),
            ["CREATE TABLE cp_no_such_table_xyz (id INT NOT NULL, PRIMARY KEY(id))"])
        assert any("不存在" in p for p in problems)

    def test_wrong_column_type(self, probe_table, copilot_conn):
        bad = self.DDL.replace("name VARCHAR(64)", "name VARCHAR(128)")
        problems = verify_statements_contracts(copilot_conn.cursor(), [bad])
        assert any("类型不符" in p for p in problems)

    def test_missing_index(self, probe_table, copilot_conn):
        # DDL 移除索引而库中仍存在 → 验收报“多索引”（合同不匹配即失败）
        bad = self.DDL.replace(",\n        INDEX idx_name (name, state)", "")
        problems = verify_statements_contracts(copilot_conn.cursor(), [bad])
        assert any("多索引" in p for p in problems)

    def test_extra_index_in_ddl_missing_in_db(self, probe_table, copilot_conn):
        # DDL 多一个库中不存在的索引 → 报“缺索引”
        bad = self.DDL.replace("PRIMARY KEY (id),",
                               "PRIMARY KEY (id),\n        INDEX idx_extra (state),")
        problems = verify_statements_contracts(copilot_conn.cursor(), [bad])
        assert any("缺索引" in p for p in problems)
