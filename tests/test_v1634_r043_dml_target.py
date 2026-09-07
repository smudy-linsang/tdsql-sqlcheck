# -*- coding: utf-8 -*-
"""v1.6.3.4 / D04 R043 联表误报修复 SIT 回归（DETAIL-v1.6.3.4 §8.3 DML-11—19）。

每条 DML 用例在**三条解析出口**分别断言（不改被测代码，用 unittest.mock.patch 注入）：
  · normal      —— 真实 sqlglot AST
  · parseerror  —— patch `sqlglot.parse_one` 抛 ParseError（ast=None 词法头回退）
  · command     —— patch `sqlglot.parse_one` 返回 exp.Command（降级节点回退）

R043 唯一触发条件（§5.2 第 5 条）：
  dml_target.status == RESOLVED AND statement_kind ∈ {UPDATE,DELETE} AND is_multi_table is True
"""
import unittest.mock as mock

import pytest
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from backend.engine.parser.parser_legacy import (
    SQLParser, ParsedSQL, DMLTarget, R043_NON_TARGET_HEADS)
from backend.engine.rules.dml import R043NoMultiTableUpdate


# ────────────────────────────────────────────────────────────────────────────
# 三路径注入
# ────────────────────────────────────────────────────────────────────────────
def parse3(sql: str) -> dict:
    """返回 {'normal','parseerror','command'} 三条路径的 ParsedSQL。"""
    parser = SQLParser()
    out = {"normal": parser.parse(sql)}
    with mock.patch("sqlglot.parse_one",
                    side_effect=ParseError("injected parse error")):
        out["parseerror"] = parser.parse(sql)
    with mock.patch("sqlglot.parse_one",
                    return_value=exp.Command(this="CMD", expression=sql)):
        out["command"] = parser.parse(sql)
    return out


def _assert_not_dml(sql: str, tag: str):
    for path, parsed in parse3(sql).items():
        dt = parsed.dml_target
        assert dt is not None, f"{tag}/{path}: dml_target 为空"
        assert dt.statement_kind == "NOT_DML", f"{tag}/{path}: kind={dt.statement_kind}"
        assert dt.status == "NOT_APPLICABLE", f"{tag}/{path}: status={dt.status}"
        assert dt.is_multi_table is False, f"{tag}/{path}: multi={dt.is_multi_table}"
        assert parsed.is_multi_table_update is False, f"{tag}/{path}: prop=True"
        # 规则层：不得产生 R043
        assert R043NoMultiTableUpdate().check(parsed) is None, f"{tag}/{path}: 误报 R043"


def _assert_multi(sql: str, kind: str, tag: str):
    for path, parsed in parse3(sql).items():
        dt = parsed.dml_target
        assert dt.statement_kind == kind, f"{tag}/{path}: kind={dt.statement_kind}"
        assert dt.status == "RESOLVED", f"{tag}/{path}: status={dt.status}"
        assert dt.is_multi_table is True, f"{tag}/{path}: multi={dt.is_multi_table}"
        assert parsed.is_multi_table_update is True, f"{tag}/{path}: prop=False"
        v = R043NoMultiTableUpdate().check(parsed)
        assert v is not None, f"{tag}/{path}: 真联表漏报 R043"
        assert kind in v.message, f"{tag}/{path}: 文案未含真实类型 {kind}"


# ════════════════════════════════════════════════════════════════════════════
# DML-11：ALTER ... MODIFY ... ON UPDATE CURRENT_TIMESTAMP（生产在用误报）
# ════════════════════════════════════════════════════════════════════════════
def test_dml11_alter_modify_on_update_three_paths():
    _assert_not_dml(
        "ALTER TABLE t MODIFY ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP "
        "ON UPDATE CURRENT_TIMESTAMP", "DML-11")
    # 仅含 ON UPDATE 的简化版
    _assert_not_dml("ALTER TABLE t MODIFY ts DATETIME ON UPDATE CURRENT_TIMESTAMP",
                    "DML-11-simplified")


def test_dml11_preserves_original_parse_error():
    # 强制 ParseError 路径：原解析错误必须保留（不能因 NOT_DML 被清除）
    parsed = parse3(
        "ALTER TABLE t MODIFY ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP "
        "ON UPDATE CURRENT_TIMESTAMP")["parseerror"]
    assert parsed.parse_error, "ParseError 路径丢失了原 parse_error"
    assert parsed.dml_target.statement_kind == "NOT_DML"


# ════════════════════════════════════════════════════════════════════════════
# DML-12：ALTER ... ADD COLUMN a ON UPDATE, ADD COLUMN b [CHARACTER SET] 强触发版
# ════════════════════════════════════════════════════════════════════════════
def test_dml12_alter_add_column_on_update_three_paths():
    _assert_not_dml(
        "ALTER TABLE t ADD COLUMN a DATETIME ON UPDATE CURRENT_TIMESTAMP, "
        "ADD COLUMN b INT", "DML-12")
    # 强触发版：b VARCHAR(20) CHARACTER SET utf8mb4
    _assert_not_dml(
        "ALTER TABLE t ADD COLUMN a DATETIME ON UPDATE CURRENT_TIMESTAMP, "
        "ADD COLUMN b VARCHAR(20) CHARACTER SET utf8mb4", "DML-12-charset")


# ════════════════════════════════════════════════════════════════════════════
# DML-13：CREATE TABLE ... ON UPDATE + CHARACTER SET，含前导注释/空白/混合大小写
# ════════════════════════════════════════════════════════════════════════════
def test_dml13_create_on_update_charset_three_paths():
    _assert_not_dml(
        "CREATE TABLE t (id INT, ts DATETIME ON UPDATE CURRENT_TIMESTAMP, "
        "c VARCHAR(20) CHARACTER SET utf8mb4)", "DML-13")


def test_dml13_with_leading_comment_and_mixed_case():
    # 前导注释 + 空白 + 混合大小写，词法头定位必须一致
    _assert_not_dml(
        "-- 建表注释\n  CrEaTe TaBlE t (id INT, ts DATETIME ON UPDATE CURRENT_TIMESTAMP)",
        "DML-13-comment")


# ════════════════════════════════════════════════════════════════════════════
# DML-14 / DML-15：真联表 UPDATE / DELETE（三路径均 RESOLVED/multi，且命中 R043）
# ════════════════════════════════════════════════════════════════════════════
def test_dml14_update_join_three_paths():
    _assert_multi("UPDATE a JOIN b ON a.id=b.id SET a.v=1", "UPDATE", "DML-14")


def test_dml14_parseerror_preserves_original_error():
    parsed = parse3("UPDATE a JOIN b ON a.id=b.id SET a.v=1")["parseerror"]
    assert parsed.parse_error, "强制 ParseError 路径必须保留原错误"
    assert parsed.is_multi_table_update is True


def test_dml15_delete_from_join_three_paths():
    _assert_multi("DELETE a FROM a JOIN b ON a.id=b.id", "DELETE", "DML-15")


# ════════════════════════════════════════════════════════════════════════════
# DML-16：is_multi_table_update 只读派生属性（无 setter、构造参数已移除、真值表）
# ════════════════════════════════════════════════════════════════════════════
def test_dml16_property_has_no_setter():
    p = ParsedSQL(raw_sql="UPDATE a JOIN b SET a.v=1")
    p.dml_target = DMLTarget.resolved("UPDATE", True, "UPDATE_TABLE_REFERENCES")
    assert p.is_multi_table_update is True
    with pytest.raises(AttributeError):
        p.is_multi_table_update = False     # 只读：无 setter


def test_dml16_constructor_arg_removed():
    # is_multi_table_update 已从 dataclass 字段移除——构造参数传入必须 TypeError
    with pytest.raises(TypeError):
        ParsedSQL(raw_sql="x", is_multi_table_update=True)


def test_dml16_truth_table():
    def _mk(kind, status, multi):
        p = ParsedSQL(raw_sql="x")
        p.dml_target = DMLTarget(statement_kind=kind, status=status,
                                 form="NONE", is_multi_table=multi)
        return p.is_multi_table_update
    # 只有 RESOLVED + UPDATE/DELETE + is_multi_table=True 才为 True
    assert _mk("UPDATE", "RESOLVED", True) is True
    assert _mk("DELETE", "RESOLVED", True) is True
    assert _mk("UPDATE", "RESOLVED", False) is False
    assert _mk("UPDATE", "UNKNOWN", None) is False
    assert _mk("NOT_DML", "NOT_APPLICABLE", False) is False
    assert _mk("SELECT", "RESOLVED", True) is False      # 非 DML kind
    # ★ SIT2 S2-01：隔离 status 条件的行——kind 与 multi 都满足，仅 status 非
    # RESOLVED，必须为 False。缺这两行，删掉属性里的 status==RESOLVED 条件不会有
    # 任何用例变红（SIT2 变异 M11b 已实证）。
    assert _mk("UPDATE", "UNKNOWN", True) is False
    assert _mk("DELETE", "UNKNOWN", True) is False
    assert _mk("UNKNOWN", "UNKNOWN", None) is False


# ════════════════════════════════════════════════════════════════════════════
# DML-17：Command/ParseError 回退的 WITH 外层定位
# ════════════════════════════════════════════════════════════════════════════
def test_dml17_with_outer_select_not_dml():
    # WITH 外层是 SELECT → NOT_DML（Command/ParseError 回退路径）
    for path in ("parseerror", "command"):
        parsed = parse3("WITH c AS (SELECT 1) SELECT * FROM c")[path]
        assert parsed.dml_target.statement_kind == "NOT_DML", \
            f"DML-17/{path}: {parsed.dml_target.statement_kind}"


def test_dml17_with_outer_single_update():
    for path in ("parseerror", "command"):
        parsed = parse3("WITH c AS (SELECT 1) UPDATE t SET x=1")[path]
        dt = parsed.dml_target
        assert dt.statement_kind == "UPDATE", f"DML-17/{path}: {dt.statement_kind}"
        assert dt.is_multi_table is False, f"DML-17/{path}: 单表 UPDATE 误判多表"
        assert R043NoMultiTableUpdate().check(parsed) is None


def test_dml17_with_outer_multi_delete():
    for path in ("parseerror", "command"):
        parsed = parse3("WITH c AS (SELECT 1) DELETE a FROM a JOIN b ON a.id=b.id")[path]
        assert parsed.dml_target.is_multi_table is True, \
            f"DML-17/{path}: WITH 外层真 multi DELETE 未识别"


# ════════════════════════════════════════════════════════════════════════════
# DML-18：36 项闭集完整性 + 净效果（误报消失、真联表仍命中、E999 不新增）
# ════════════════════════════════════════════════════════════════════════════
def test_dml18_closed_set_integrity():
    # 闭集是 frozenset、36 项、且不含 UPDATE/DELETE（直接目标）
    assert isinstance(R043_NON_TARGET_HEADS, frozenset)
    assert len(R043_NON_TARGET_HEADS) == 36
    assert "UPDATE" not in R043_NON_TARGET_HEADS
    assert "DELETE" not in R043_NON_TARGET_HEADS


def test_dml18_net_effect_no_false_positive_no_new_e999():
    # 净效果锁：闭合头语句（ALTER/CREATE/LOCK/SELECT）不产生 R043，且不新增 E999
    non_target = [
        "ALTER TABLE t MODIFY ts DATETIME ON UPDATE CURRENT_TIMESTAMP",
        "CREATE TABLE t (id INT, ts DATETIME ON UPDATE CURRENT_TIMESTAMP)",
        "LOCK TABLES t WRITE",
        "SELECT 'UPDATE a JOIN b SET x=1' AS c",
        "FLUSH TABLES",
    ]
    for sql in non_target:
        parsed = SQLParser().parse(sql)
        assert R043NoMultiTableUpdate().check(parsed) is None, f"误报 R043: {sql}"


def test_dml18_real_multi_still_flagged():
    # 真联表 UPDATE/DELETE 仍然命中 R043（仅分布式作用域由 checker 负责，此处测规则触发）
    for sql, kind in [("UPDATE a JOIN b ON a.id=b.id SET a.v=1", "UPDATE"),
                      ("DELETE a FROM a JOIN b ON a.id=b.id", "DELETE")]:
        parsed = SQLParser().parse(sql)
        v = R043NoMultiTableUpdate().check(parsed)
        assert v is not None, f"真联表漏报 R043: {sql}"
        assert kind in v.message


# ════════════════════════════════════════════════════════════════════════════
# DML-19：词法陷阱（复合 token / 字符串 / 反引号伪造 / 通用 Alias 根 / 未知头 / 批文件）
# ════════════════════════════════════════════════════════════════════════════
def test_dml19_lock_tables_composite_token():
    # sqlglot 把 "LOCK TABLES" 合成一个 COMMAND token——必须正确识别为 NOT_DML
    for path, parsed in parse3("LOCK TABLES t WRITE").items():
        assert parsed.dml_target.statement_kind == "NOT_DML", \
            f"DML-19/{path}: LOCK TABLES 误判"


def test_dml19_string_and_backtick_forge_not_head():
    # 字符串字面量 / 反引号标识符里的 UPDATE 不能冒充语句头
    _assert_not_dml("SELECT 'UPDATE a JOIN b SET x=1' AS c", "DML-19-string")
    _assert_not_dml("SELECT `UPDATE` FROM t", "DML-19-backtick")


def test_dml19_generic_alias_roots_not_dml():
    # FLUSH/SAVEPOINT/RESET 等通用表达式根，命中闭集 → NOT_DML
    for sql in ("FLUSH TABLES", "SAVEPOINT sp1", "RESET QUERY CACHE"):
        _assert_not_dml(sql, f"DML-19-{sql.split()[0]}")


def test_dml19_unknown_head_fails_closed():
    # 闭集外/未知头 → UNKNOWN（并入完整性门禁），不一律 NOT_DML 也不虚构 R043
    parsed = SQLParser().parse("FOOBAR BAZ something")
    dt = parsed.dml_target
    assert dt.status == "UNKNOWN", f"未知头应为 UNKNOWN，实得 {dt.status}"
    assert R043NoMultiTableUpdate().check(parsed) is None


def test_dml19_batch_first_select_does_not_exempt_later_dml():
    # 批文件第一条是 SELECT，不豁免后续真 multi DML——逐实际语句判断
    parser = SQLParser()
    p1 = parser.parse("SELECT 1")
    p2 = parser.parse("UPDATE a JOIN b ON a.id=b.id SET a.v=1")
    assert p1.dml_target.statement_kind == "NOT_DML"
    assert p2.is_multi_table_update is True
    assert R043NoMultiTableUpdate().check(p2) is not None
