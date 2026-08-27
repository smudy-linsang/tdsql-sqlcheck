# -*- coding: utf-8 -*-
"""按 manifest 判据逐条执行（第十一轮 BLOCK-11-07、第十二轮 BLOCK-12-05）。

判据完全来自同目录的 `parser_recovery_manifest.py`，本文件**不含任何用例数据**
——新增/修改用例只改 manifest，本文件与设计说明书 §7.1 的表格都自动跟随。

**这些是准出用例，不是现网回归用例。** 它们断言的是 v1.6.2.2 修复**之后**的行为；
在未打补丁的主干上必然大面积失败，这正是它们作为准入门槛的意义。
用 `python docs/evidence/v1.6.2.2/run_all.py` 一条命令即可在临时目录里重建
设计补丁并跑通全部断言，不触碰工作区。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import random

import pytest
import sqlglot

from backend.engine.checker import RuleChecker
from backend.engine.parser import parser_legacy as PL
from backend.engine.parser.parser_legacy import SQLParser
from parser_recovery_manifest import CASES, FUZZ, MUTATIONS

_sp = SQLParser()
_ck = RuleChecker()
_rid_cache = {}


def _rid(sql, inst):
    key = (sql, inst)
    if key not in _rid_cache:
        _rid_cache[key] = frozenset(
            v.rule_id for v in _ck.audit_sql(sql, instance_type=inst).violations)
    return _rid_cache[key]


def _plan_and_spans(sql):
    plan = PL._plan_recovery(sql, "mysql")
    if plan is None:
        return None, []
    return plan, list(plan["primary_spans"]) + list(plan["auxiliary_spans"])


def _out_of_span_chars(sql, spans):
    """越界改写字符数；长度不恒等直接判 -1。"""
    out = PL._blank_spans(sql, spans)
    if out is None or len(out) != len(sql):
        return -1
    return sum(1 for i, ch in enumerate(sql)
               if ch != out[i] and not any(a <= i <= b for a, b in spans))


# ── 判据分派 ────────────────────────────────────────────────────────────────

def _check_contract():
    node = sqlglot.parse_one(
        "CREATE TABLE t (id INT, a VARCHAR(20), g GEOMETRY, PRIMARY KEY (id), "
        "UNIQUE KEY u (id), FULLTEXT KEY f (a), SPATIAL KEY s (g))", dialect="mysql")
    got = [type(i).__name__ for i in node.this.expressions[3:]]
    kinds = [str(i.args.get("kind") or "") for i in node.this.expressions[5:]]
    assert got == ["PrimaryKey", "UniqueColumnConstraint",
                   "IndexColumnConstraint", "IndexColumnConstraint"], (
        "sqlglot %s 的建表 AST 契约已变化：%s" % (sqlglot.__version__, got))
    assert kinds == ["FULLTEXT", "SPATIAL"], (
        "sqlglot %s 的索引 kind 契约已变化：%s" % (sqlglot.__version__, kinds))


def _check_ruleset(case):
    ex = case.extra
    raw = io.open("tests/fixtures/" + ex["fixture"], encoding="utf-8").read()
    got = set(_rid(raw, ex["instance_type"]))
    assert got == ex["rules"], "规则集合必须精确相等：多出=%s 少了=%s" % (
        sorted(got - ex["rules"]), sorted(ex["rules"] - got))


def _check_spans(case):
    plan, spans = _plan_and_spans(case.sql)
    n = len(spans) if plan else 0
    assert n == case.extra["spans"], "span 数应为 %d，实得 %d" % (case.extra["spans"], n)
    if plan:
        assert _out_of_span_chars(case.sql, spans) == 0, "存在越界改写字符"


def _check_sql_case(case):
    ex = case.extra
    plan, spans = _plan_and_spans(case.sql)
    has_plan = plan is not None
    parsed = _sp.parse(case.sql)
    ast = type(parsed.ast).__name__
    e999 = bool(parsed.parse_error)

    if case.klass in ("pos", "characterization"):
        assert ast == "Create" and not e999, "应恢复为 Create 且无 E999，实得 %s/E999=%s" % (ast, e999)
        if case.klass == "pos" and ex.get("needs_recovery", True):
            assert has_plan, "规划器必须先接受该语句"
        if "spans" in ex:
            if ex["spans"]:
                assert len(spans) >= 1, "应产生掩码 span"
            else:
                assert len(spans) == 0, "不应产生掩码 span"
        if ex.get("raw_verbatim"):
            assert parsed.raw_sql == case.sql.strip(), "raw_sql 必须逐字符等于输入"
        if ex.get("columns") is not None:
            assert [c.get("name") for c in (parsed.columns or [])] == ex["columns"]
        if ex.get("column_comment"):
            col, txt = ex["column_comment"]
            assert parsed.column_comments.get(col) == txt
        if ex.get("index_type"):
            assert ex["index_type"] in [i.get("type") for i in (parsed.indexes or [])]
        inst = ex.get("instance_type", "distributed")
        if ex.get("rule_hit"):
            assert ex["rule_hit"] in _rid(case.sql, inst)
        if ex.get("rule_miss"):
            assert ex["rule_miss"] not in _rid(case.sql, inst)
        if ex.get("equal_ruleset_without_comment"):
            bare = case.sql.replace(" COMMENT '唯一索引说明'", "").replace(" COMMENT 'z'", "")
            assert _rid(case.sql, inst) == _rid(bare, inst), (
                "恢复不得引入自己的口径：规则集合必须等于去掉索引 COMMENT 的同表")
        return

    if case.klass == "neg":
        assert not has_plan, "token 规划器必须先行拒绝，不能只依赖候选 parser 或 AST 门禁"
        assert ast != "Create", "非法形态不得被恢复成 Create"
        if ex.get("e999"):
            assert e999, "必须保留 E999"
        if ex.get("ast"):
            assert ast == ex["ast"]
        return

    if case.klass in ("pos_known", "unsupported_unproven"):
        assert ast != "Create", "必须失败关闭（既不冒充合法，也不冒充非法）"
        if ex.get("ast"):
            assert ast == ex["ast"]
        return

    raise AssertionError("未知 klass: %s" % case.klass)


@pytest.mark.parametrize("case", CASES, ids=[c.cid for c in CASES])
def test_manifest_case(case):
    if case.klass == "contract":
        _check_contract()
    elif case.klass == "ruleset":
        _check_ruleset(case)
    elif case.klass == "spans":
        _check_spans(case)
    else:
        _check_sql_case(case)


@pytest.mark.parametrize("suite", MUTATIONS, ids=[s["cid"] for s in MUTATIONS])
def test_gate_is_conservative(suite):
    """候选 AST 结构守恒门禁的反向鉴别：不得误杀，也不得漏放。"""
    plan = PL._plan_recovery(suite["src"], "mysql")
    assert plan is not None, "变异套件的源语句必须能生成恢复计划"
    good = sqlglot.parse_one(suite["good"], dialect="mysql")
    assert PL._validate_recovery_candidate(good, plan) is True, (
        "%s：正确候选被门禁误杀" % suite["title"])
    for label, msql in suite["muts"]:
        try:
            cand = sqlglot.parse_one(msql, dialect="mysql")
        except Exception:
            continue                                   # 解析不出来 → 不可能成为候选
        assert PL._validate_recovery_candidate(cand, plan) is False, (
            "%s / %s：变异候选被门禁放行" % (suite["title"], label))


def test_blank_spans_invariants_under_fuzz():
    """模糊测试：不抛异常；凡产生计划者必满足长度恒等 + 差异全落在 span 内。"""
    random.seed(FUZZ["seed"])
    atoms = ["'", "''", "\\'", "\\\\", "`", "``", '"', "(", ")", ",", ";", "--x\n",
             "/*y*/", "\n", " ", "#z\n", "UNIQUE", "KEY", "INDEX", "COMMENT", "CREATE",
             "TABLE", "TEMPORARY", "PRIMARY", "CONSTRAINT", "a", "uk", "20",
             "UNIQUE KEY `u` (`a`) COMMENT 'x'", "varchar(20)", "ENGINE=InnoDB",
             "NOT NULL", "TDSQL_DISTRIBUTED BY HASH(a)"]
    violations = []
    for _ in range(FUZZ["n"]):
        body = "".join(random.choice(atoms) for _ in range(random.randint(3, 45)))
        sql = random.choice(["CREATE TABLE `t` (" + body,
                             "CREATE TEMPORARY TABLE `t` (" + body + ")", body])
        plan, spans = _plan_and_spans(sql)           # 抛异常即测试失败
        if plan is not None and _out_of_span_chars(sql, spans) != 0:
            violations.append(sql)
    assert not violations, "模糊测试发现 %d 条越界改写" % len(violations)
