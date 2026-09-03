# -*- coding: utf-8 -*-
"""v1.6.3.2 规则调整与解析器新通道专项测试（DESIGN-v1.6.3.2 §10.1/§10.2）。

覆盖：
  · R011 收窄为仅 TEXT(INFO)，CREATE 与 ALTER ADD/MODIFY/CHANGE 均覆盖；
  · R120 五种受限 LOB(ERROR)，CREATE 与 ALTER 覆盖；TINYTEXT/TINYBLOB/JSON 不命中；
  · R030/R032 仅分布式（集中式跳过、规则集不能绕过适用域）；
  · R035 只比较规范化基础类型（括号参数不参与），批内跨表上下文可触发；
  · R058 上限 2000 且结构化判定（注释/字符串不放行、占位符/offset 不可证明）；
  · R121 二级分区 MAXVALUE（CREATE/ALTER_ADD/ALTER_REORGANIZE 三出口）；
  · 解析器 dml_limit / alter_column_types / secondary_partition 三通道结构断言；
  · 质量门禁 strict/normal 放宽与收紧矩阵（§10.2）。
"""
import pytest

from backend.engine.checker import RuleChecker
from backend.engine.parser import SQLParser


@pytest.fixture()
def parser():
    return SQLParser(dialect="mysql")


@pytest.fixture()
def checker():
    return RuleChecker()


def _ids(result):
    return {v.rule_id for v in result.violations}


def _sev(result, rule_id):
    for v in result.violations:
        if v.rule_id == rule_id:
            return v.severity.value if hasattr(v.severity, "value") else str(v.severity)
    return None


# ════════════════════════════════════════════════════════════
# R011 / R120
# ════════════════════════════════════════════════════════════

def test_r011_text_only_info(parser, checker):
    r = checker.audit_sql("CREATE TABLE t (id INT, body TEXT)", instance_type="distributed")
    assert "R011" in _ids(r)
    assert _sev(r, "R011") == "INFO"


def test_r011_alter_add_modify_change(parser, checker):
    for sql in (
        "ALTER TABLE t ADD COLUMN body TEXT",
        "ALTER TABLE t MODIFY COLUMN body TEXT",
        "ALTER TABLE t CHANGE COLUMN a body TEXT",
    ):
        r = checker.audit_sql(sql, instance_type="distributed")
        assert "R011" in _ids(r), sql
        assert _sev(r, "R011") == "INFO", sql


def test_r011_not_hit_other_types(parser, checker):
    for t in ("MEDIUMTEXT", "LONGTEXT", "TINYTEXT", "BLOB", "JSON", "VARCHAR(2000)"):
        r = checker.audit_sql(f"CREATE TABLE t (id INT, body {t})", instance_type="distributed")
        assert "R011" not in _ids(r), t


def test_r120_five_lob_types(parser, checker):
    for t in ("BLOB", "MEDIUMTEXT", "LONGBLOB", "MEDIUMBLOB", "LONGTEXT"):
        r = checker.audit_sql(f"CREATE TABLE t (id INT, body {t})", instance_type="distributed")
        assert "R120" in _ids(r), t
        assert _sev(r, "R120") == "ERROR", t


def test_r120_alter_coverage(parser, checker):
    for sql in (
        "ALTER TABLE t ADD COLUMN body MEDIUMTEXT",
        "ALTER TABLE t MODIFY COLUMN body LONGBLOB",
        "ALTER TABLE t CHANGE COLUMN a body LONGTEXT",
    ):
        r = checker.audit_sql(sql, instance_type="distributed")
        assert "R120" in _ids(r), sql


def test_r120_not_hit_tiny_or_json_or_columnname(parser, checker):
    for t in ("TINYTEXT", "TINYBLOB", "JSON"):
        r = checker.audit_sql(f"CREATE TABLE t (id INT, body {t})", instance_type="distributed")
        assert "R120" not in _ids(r), t
    # 列名含 blob / 字符串 'LONGTEXT' 不得误命中
    r = checker.audit_sql("CREATE TABLE t (id INT, blob_url VARCHAR(64), note VARCHAR(32) DEFAULT 'LONGTEXT')",
                          instance_type="distributed")
    assert "R120" not in _ids(r)


def test_r011_and_r120_coexist(parser, checker):
    r = checker.audit_sql("CREATE TABLE t (id INT, a TEXT, b MEDIUMTEXT)", instance_type="distributed")
    assert "R011" in _ids(r) and "R120" in _ids(r)
    assert _sev(r, "R011") == "INFO" and _sev(r, "R120") == "ERROR"


# ════════════════════════════════════════════════════════════
# R030 / R032 适用域
# ════════════════════════════════════════════════════════════

def test_r030_r032_distributed_only(parser, checker):
    view = "CREATE VIEW v AS SELECT 1"
    tmp = "CREATE TEMPORARY TABLE tt (id INT)"
    rd = checker.audit_sql(view, instance_type="distributed")
    td = checker.audit_sql(tmp, instance_type="distributed")
    rc = checker.audit_sql(view, instance_type="centralized")
    tc = checker.audit_sql(tmp, instance_type="centralized")
    assert "R030" in _ids(rd)
    assert "R032" in _ids(td)
    assert "R030" not in _ids(rc)      # 集中式跳过
    assert "R032" not in _ids(tc)


def test_ruleset_cannot_bypass_scope(parser, checker):
    # 规则集把 R030 设为启用，也不能在集中式上执行（INV-2 适用域只做减法）
    overrides = {"R030": {"enabled": True}}
    rc = checker.audit_sql("CREATE VIEW v AS SELECT 1", rule_overrides=overrides,
                           instance_type="centralized")
    assert "R030" not in _ids(rc)


# ════════════════════════════════════════════════════════════
# R035 跨表上下文
# ════════════════════════════════════════════════════════════

def test_r035_same_base_type_different_length_passes(checker):
    sql = ("CREATE TABLE a (uid VARCHAR(32));\n"
           "CREATE TABLE b (uid VARCHAR(128));")
    results = checker.audit_file(sql, instance_type="distributed")
    ids = set().union(*[_ids(r) for r in results]) if results else set()
    assert "R035" not in ids


def test_r035_different_base_type_errors(checker):
    sql = ("CREATE TABLE a (uid VARCHAR(32));\n"
           "CREATE TABLE b (uid BIGINT);")
    results = checker.audit_file(sql, instance_type="distributed")
    ids = set().union(*[_ids(r) for r in results]) if results else set()
    assert "R035" in ids


def test_r035_unsigned_matters(checker):
    sql = ("CREATE TABLE a (uid INT);\n"
           "CREATE TABLE b (uid INT UNSIGNED);")
    results = checker.audit_file(sql, instance_type="distributed")
    ids = set().union(*[_ids(r) for r in results]) if results else set()
    assert "R035" in ids


def test_r035_single_statement_skips(checker):
    r = checker.audit_sql("CREATE TABLE a (uid VARCHAR(32))", instance_type="distributed")
    assert "R035" not in _ids(r)


def test_r035_message_no_length_wording(checker):
    sql = ("CREATE TABLE a (uid VARCHAR(32));\n"
           "CREATE TABLE b (uid BIGINT);")
    results = checker.audit_file(sql, instance_type="distributed")
    for r in results:
        for v in r.violations:
            if v.rule_id == "R035":
                assert "长度必须一致" not in v.message
                assert "长度" not in (v.suggestion or "") or "可按各表实际容量" in (v.suggestion or "")


# ════════════════════════════════════════════════════════════
# R058 结构化 LIMIT
# ════════════════════════════════════════════════════════════

_SHARD_META = {"t": {"shard_key": "id", "is_shard_table": True}}


def test_r058_no_limit_warns(parser, checker):
    r = checker.audit_sql("UPDATE t SET a=1 WHERE b=2", table_metadata=_SHARD_META,
                          instance_type="distributed")
    assert "R058" in _ids(r)


@pytest.mark.parametrize("limit", ["0", "1", "2000"])
def test_r058_within_limit_passes(parser, checker, limit):
    r = checker.audit_sql(f"UPDATE t SET a=1 WHERE b=2 LIMIT {limit}",
                          table_metadata=_SHARD_META, instance_type="distributed")
    assert "R058" not in _ids(r), limit


@pytest.mark.parametrize("limit", ["2001", "999999"])
def test_r058_over_limit_warns(parser, checker, limit):
    r = checker.audit_sql(f"UPDATE t SET a=1 WHERE b=2 LIMIT {limit}",
                          table_metadata=_SHARD_META, instance_type="distributed")
    assert "R058" in _ids(r), limit


def test_r058_placeholder_unprovable(parser, checker):
    r = checker.audit_sql("UPDATE t SET a=1 WHERE b=? LIMIT ?",
                          table_metadata=_SHARD_META, instance_type="distributed")
    assert "R058" in _ids(r)
    assert "无法在审核阶段确定" in next(v.message for v in r.violations if v.rule_id == "R058")


def test_r058_two_param_offset_unprovable(parser, checker):
    r = checker.audit_sql("UPDATE t SET a=1 WHERE b=2 LIMIT 1, 2000",
                          table_metadata=_SHARD_META, instance_type="distributed")
    assert "R058" in _ids(r)
    assert "无法在审核阶段确定" in next(v.message for v in r.violations if v.rule_id == "R058")


def test_r058_comment_and_string_not_limit(parser, checker):
    for sql in ("UPDATE t SET a=1 WHERE b=2 /* limit 10 */",
                "UPDATE t SET a=1 WHERE b=2 AND note='limit 10'"):
        r = checker.audit_sql(sql, table_metadata=_SHARD_META, instance_type="distributed")
        assert "R058" in _ids(r), sql     # 注释/字符串不构成 LIMIT → 视为未设置


def test_r058_subquery_limit_not_outer(parser, checker):
    r = checker.audit_sql("UPDATE t SET a=1 WHERE b=(SELECT c FROM x LIMIT 5)",
                          table_metadata=_SHARD_META, instance_type="distributed")
    assert "R058" in _ids(r)     # 子查询 LIMIT 不算外层 → 视为未设置


# ════════════════════════════════════════════════════════════
# R121 二级分区 MAXVALUE
# ════════════════════════════════════════════════════════════

def test_r121_create_bare_maxvalue(parser, checker):
    sql = ("CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id) "
           "PARTITION BY RANGE(id) (PARTITION pmax VALUES LESS THAN MAXVALUE)")
    r = checker.audit_sql(sql, instance_type="distributed")
    assert "R121" in _ids(r)
    assert "E999_SYNTAX_ERROR" in _ids(r)     # bare 形态当前 sqlglot ParseError
    assert _sev(r, "R121") == "ERROR"


def test_r121_create_paren_maxvalue(parser, checker):
    sql = ("CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id) "
           "PARTITION BY RANGE(id) (PARTITION pmax VALUES LESS THAN (MAXVALUE))")
    r = checker.audit_sql(sql, instance_type="distributed")
    assert "R121" in _ids(r)
    assert "E999_SYNTAX_ERROR" not in _ids(r)


def test_r121_alter_add_and_reorganize(parser, checker):
    add = "ALTER TABLE t ADD PARTITION (PARTITION pmax VALUES LESS THAN MAXVALUE)"
    reorg = "ALTER TABLE t REORGANIZE PARTITION p0 INTO (PARTITION pmax VALUES LESS THAN (MAXVALUE))"
    ra = checker.audit_sql(add, instance_type="distributed")
    rr = checker.audit_sql(reorg, instance_type="distributed")
    assert "R121" in _ids(ra)
    assert "R121" in _ids(rr)


def test_r121_normal_bound_passes(parser, checker):
    sql = ("CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id) "
           "PARTITION BY RANGE(id) (PARTITION p1 VALUES LESS THAN (202702))")
    r = checker.audit_sql(sql, instance_type="distributed")
    assert "R121" not in _ids(r)


def test_r121_first_level_not_hit(parser, checker):
    sql = "CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY RANGE(id) (PARTITION pmax VALUES LESS THAN MAXVALUE)"
    r = checker.audit_sql(sql, instance_type="distributed")
    assert "R121" not in _ids(r)


def test_r121_decoys_not_hit(parser, checker):
    for sql in ("CREATE TABLE t (id INT, note VARCHAR(32) DEFAULT 'MAXVALUE')",
                "CREATE TDSQL_SEQUENCE s TDSQL_MAXVALUE 100"):
        r = checker.audit_sql(sql, instance_type="distributed")
        assert "R121" not in _ids(r), sql


def test_r121_centralized_skipped(parser, checker):
    sql = ("CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id) "
           "PARTITION BY RANGE(id) (PARTITION pmax VALUES LESS THAN MAXVALUE)")
    r = checker.audit_sql(sql, instance_type="centralized")
    assert "R121" not in _ids(r)


# ════════════════════════════════════════════════════════════
# 解析器三通道结构断言
# ════════════════════════════════════════════════════════════

def test_parser_dml_limit_fields(parser):
    p = parser.parse("UPDATE t SET a=1 WHERE b=2 LIMIT 2000")
    assert p.dml_limit["present"] is True
    assert p.dml_limit["row_count"] == 2000
    assert p.dml_limit["verifiable"] is True
    p2 = parser.parse("UPDATE t SET a=1 WHERE b=2 LIMIT 1, 2000")
    assert p2.dml_limit["offset"] == 1
    assert p2.dml_limit["verifiable"] is False
    p3 = parser.parse("UPDATE t SET a=1 WHERE b=2")
    assert p3.dml_limit["present"] is False


def test_parser_alter_column_types(parser):
    p = parser.parse("ALTER TABLE t ADD COLUMN c1 MEDIUMTEXT")
    assert p.alter_column_types and p.alter_column_types[0]["type"] == "MEDIUMTEXT"
    assert p.alter_column_types[0]["operation"] == "ADD"
    p2 = parser.parse("ALTER TABLE t CHANGE COLUMN c1 c2 LONGBLOB")
    assert p2.alter_column_types[0]["operation"] == "CHANGE"


def test_parser_secondary_partition_three_exits(parser):
    bare = ("CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id) "
            "PARTITION BY RANGE(id) (PARTITION pmax VALUES LESS THAN MAXVALUE)")
    p = parser.parse(bare)     # ParseError 出口
    assert p.secondary_partition["maxvalue_partitions"] == ("pmax",)
    add = "ALTER TABLE t ADD PARTITION (PARTITION pmax VALUES LESS THAN MAXVALUE)"
    pa = parser.parse(add)     # Command/ParseError 出口
    assert pa.secondary_partition["source_context"] == "ALTER_ADD"
    reorg = "ALTER TABLE t REORGANIZE PARTITION p0 INTO (PARTITION pmax VALUES LESS THAN (MAXVALUE))"
    pr = parser.parse(reorg)   # Command 出口
    assert pr.secondary_partition["source_context"] == "ALTER_REORGANIZE"
    sel = parser.parse("SELECT 1")
    assert sel.secondary_partition["has_definition"] is False


# ════════════════════════════════════════════════════════════
# 质量门禁 strict/normal 矩阵（§10.2）
# ════════════════════════════════════════════════════════════

def _gate_pass(result, mode):
    errs = [v for v in result.violations
            if (v.severity.value if hasattr(v.severity, "value") else v.severity) == "ERROR"]
    warns = [v for v in result.violations
             if (v.severity.value if hasattr(v.severity, "value") else v.severity) == "WARNING"]
    if mode == "strict":
        return not errs and not warns
    return not errs


def _audit_only(checker, sql, keep, instance_type):
    """§10.2：门禁必须以无其他违规的最小规则集隔离测试——只保留 keep 中的规则。"""
    overrides = {r.rule_id: {"enabled": False} for r in checker.rules}
    for rid in keep:
        overrides[rid] = {"enabled": True}
    return checker.audit_sql(sql, rule_overrides=overrides, instance_type=instance_type)


def test_gate_text_relaxed(parser, checker):
    r = _audit_only(checker, "CREATE TABLE t (id INT, body TEXT)", {"R011", "R120"}, "distributed")
    assert _gate_pass(r, "strict")      # R011 改 INFO → strict 通过（相对旧 WARNING 放宽）
    assert _gate_pass(r, "normal")


def test_gate_lob_tightened(parser, checker):
    r = _audit_only(checker, "CREATE TABLE t (id INT, body MEDIUMTEXT)", {"R011", "R120"}, "distributed")
    assert not _gate_pass(r, "strict")
    assert not _gate_pass(r, "normal")


def test_gate_tinytext_relaxed(parser, checker):
    r = _audit_only(checker, "CREATE TABLE t (id INT, body TINYTEXT)", {"R011", "R120"}, "distributed")
    assert _gate_pass(r, "strict")
    assert _gate_pass(r, "normal")


def test_gate_centralized_r030_relaxed(parser, checker):
    r = _audit_only(checker, "CREATE VIEW v AS SELECT 1", {"R030"}, "centralized")
    assert _gate_pass(r, "strict")      # R030 改域后集中式放宽


def test_gate_distributed_maxvalue_tightened(parser, checker):
    sql = ("CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id) "
           "PARTITION BY RANGE(id) (PARTITION pmax VALUES LESS THAN (MAXVALUE))")
    r = _audit_only(checker, sql, {"R121"}, "distributed")
    assert not _gate_pass(r, "strict")
    assert not _gate_pass(r, "normal")
