# -*- coding: utf-8 -*-
"""V1.5 实例类型适用域 — 规则标注一致性 + 核心行为测试

本文件锁定 DETAIL-v1.5 §3.3 判定表（v1.5 历史基线 119 条；v1.6.3.2 新增
R120/R121 并调整 R030/R032 适用域后扩展至 121 条），是本次改造的正确性基准。
test_distributed_only_list_is_exactly_as_designed 失败意味着有人改动了规则的
instance_scope 标注——错标一条为 DISTRIBUTED 就会让集中式实例静默漏报一项检查。
"""
import pytest

from backend.engine.rules import ALL_RULE_CLASSES
from backend.engine.checker import RuleChecker


# 30 条仅分布式适用规则，判定依据见 DETAIL-v1.5 §3.3；
# R097/R113 由负责人 2026-07-29 裁定归 DISTRIBUTED；
# v1.6.3.2：R030/R032 适用域改 DISTRIBUTED（REQ-03/04），新增 R121（仅分布式）。
DISTRIBUTED_ONLY = {
    "R020", "R021", "R022", "R023", "R024", "R025", "R043", "R048",
    "R053", "R054", "R055", "R056", "R057", "R058", "R059", "R060",
    "R077", "R092", "R097", "R100", "R111", "R112", "R113",
    "R115", "R116", "R117", "R118",
    "R030", "R032", "R121",
}


def _scope(rule_cls) -> str:
    scope = getattr(rule_cls, "instance_scope", None)
    return getattr(scope, "value", scope) or "all"


# ════════════════════════════════════════════════════════════
# 清单一致性（锁定判定表）
# ════════════════════════════════════════════════════════════

def test_distributed_only_list_is_exactly_as_designed():
    """锁定适用域判定清单，改动即失败。"""
    actual = {c.rule_id for c in ALL_RULE_CLASSES if _scope(c) == "distributed"}
    assert actual == DISTRIBUTED_ONLY, (
        f"多标: {actual - DISTRIBUTED_ONLY}，漏标: {DISTRIBUTED_ONLY - actual}")


def test_no_centralized_only_rules_yet():
    """现行规范中不存在 0 条仅集中式适用规则；将来新增需同步更新设计文档。"""
    actual = {c.rule_id for c in ALL_RULE_CLASSES if _scope(c) == "centralized"}
    assert actual == set()


def test_rule_counts():
    """总数 121；分布式跑 121；集中式跑 91（v1.6.3.2）。"""
    assert len(ALL_RULE_CLASSES) == 121
    checker = RuleChecker()
    assert len(checker.get_enabled_rules(None, "distributed")) == 121
    assert len(checker.get_enabled_rules(None, "centralized")) == 91


def test_every_rule_has_valid_scope():
    """防止手滑写成字符串或拼错枚举值。"""
    for c in ALL_RULE_CLASSES:
        assert _scope(c) in ("all", "distributed", "centralized"), f"{c.rule_id}: {_scope(c)}"


# ════════════════════════════════════════════════════════════
# 核心行为（缺陷修复验证）
# ════════════════════════════════════════════════════════════

def test_r077_not_fired_on_centralized():
    """用户报告的缺陷现场：集中式实例不得出现 R077。"""
    sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY, name VARCHAR(64)) ENGINE=InnoDB"
    r = RuleChecker().audit_sql(sql, instance_type="centralized")
    assert "R077" not in {v.rule_id for v in r.violations}


def test_r077_still_fired_on_distributed():
    """反向验证：分布式实例上 R077 必须照常触发，否则等于把功能删了。"""
    sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY, name VARCHAR(64)) ENGINE=InnoDB"
    r = RuleChecker().audit_sql(sql, instance_type="distributed")
    assert "R077" in {v.rule_id for v in r.violations}


def test_window_function_legal_on_centralized():
    """R111：集中式 MySQL 8.0 的合法窗口函数不得被判 ERROR。"""
    sql = "SELECT id, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY id) rn FROM t_emp WHERE dept = 1"
    r = RuleChecker().audit_sql(sql, instance_type="centralized")
    assert "R111" not in {v.rule_id for v in r.violations}


def test_window_function_still_fired_on_distributed():
    """分布式实例上窗口函数仍应触发 R111（零回归）。"""
    sql = "SELECT id, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY id) rn FROM t_emp WHERE dept = 1"
    r = RuleChecker().audit_sql(sql, instance_type="distributed")
    assert "R111" in {v.rule_id for v in r.violations}


# 覆盖 DDL/DML/索引/Oracle兼容 的样本 SQL，用于分布式零回归 diff
SAMPLE_SQLS = [
    "SELECT * FROM t_order WHERE user_id = 123 ORDER BY RAND() LIMIT 10",
    "CREATE TABLE t_user (id BIGINT PRIMARY KEY, name VARCHAR(64)) ENGINE=InnoDB",
    "UPDATE t_order SET status = 0",
    "DELETE FROM t_log",
    "SELECT id, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY id) rn FROM t_emp WHERE dept = 1",
    "SELECT NVL(name, 'N/A'), ROWNUM FROM emp WHERE ROWNUM < 10",
    "CREATE TABLE t_tmp (id BIGINT, amount DECIMAL(10,2)) ENGINE=InnoDB",
    "INSERT INTO t_order (id, name) VALUES (1, 'a')",
]


def test_distributed_zero_regression():
    """INV-4：分布式口径与 V1.4 的"不过滤"口径逐条一致。"""
    checker = RuleChecker()
    for sql in SAMPLE_SQLS:
        old = {(v.rule_id, v.message) for v in checker.audit_sql(sql).violations}
        new = {(v.rule_id, v.message)
               for v in checker.audit_sql(sql, instance_type="distributed").violations}
        assert old == new, f"分布式口径发生回归: {sql[:60]}"


def test_ruleset_cannot_reenable_inapplicable_rule():
    """INV-2：规则集不得反向打开一条不适用的规则。"""
    overrides = {"R077": {"enabled": True, "severity_override": None}}
    ids = {r.rule_id for r in RuleChecker().get_enabled_rules(overrides, "centralized")}
    assert "R077" not in ids


def test_ruleset_can_still_disable_applicable_rule():
    """适用域只做减法，不影响规则集正常的禁用能力。"""
    overrides = {"R012": {"enabled": False, "severity_override": None}}
    ids = {r.rule_id for r in RuleChecker().get_enabled_rules(overrides, "centralized")}
    assert "R012" not in ids


def test_legacy_calls_still_work():
    """存量调用（不传 instance_type）行为与 V1.4 完全一致：返回全部 121 条。"""
    assert len(RuleChecker().get_enabled_rules()) == 121


def test_count_skipped_by_scope():
    """跳过计数：集中式跳 27 条，分布式跳 0 条。"""
    checker = RuleChecker()
    assert checker.count_skipped_by_scope("centralized") == 30
    assert checker.count_skipped_by_scope("distributed") == 0
    assert checker.count_skipped_by_scope(None) == 0
