# -*- coding: utf-8 -*-
"""v1.6.2.1 R061 索引名反引号未剥离导致系统性误报修复——12 例验收用例

覆盖 DESIGN-v1.6.2.1 §7.1 全部用例矩阵。
P*/E* = 正向（修复后不应命中 R061）
N*    = 反向鉴别（真实违规必须命中）
U*    = ADJ-5 现状锁定（UNIQUE 分支当前为死代码）
G*    = 生产报告原样 DDL 回放
"""
import os

import pytest

from backend.engine.checker import RuleChecker

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _read_fixture(filename: str) -> str:
    with open(os.path.join(FIXTURE_DIR, filename), encoding="utf-8") as f:
        return "\n".join(
            line for line in f.read().splitlines()
            if not line.strip().startswith("--")
        ).strip()


@pytest.fixture
def checker():
    return RuleChecker()


def _has_r061(checker, sql: str) -> tuple[bool, str]:
    """返回 (是否命中R061, R061告警消息)"""
    result = checker.audit_sql(sql, instance_type='distributed')
    for v in result.violations:
        if v.rule_id == "R061":
            return True, v.message
    return False, ""


# ── P 类：反引号合规索引名（修复核心）──

class TestBacktickCompliant:
    """P1/P2/E1/E4：带反引号的合规索引名不应再误报"""

    def test_p1_backtick_idx_prefix(self, checker):
        """P1: KEY `idx_cust` (`cust_no`) → R061 不命中（核心修复点）"""
        sql = ("CREATE TABLE `t1` (`id` BIGINT NOT NULL, `cust_no` VARCHAR(20) NOT NULL, "
               "PRIMARY KEY (`id`), KEY `idx_cust` (`cust_no`)) ENGINE=InnoDB")
        hit, msg = _has_r061(checker, sql)
        assert not hit, f"带反引号的合规索引名不应误报: {msg}"

    def test_p2_bare_idx_prefix(self, checker):
        """P2: KEY idx_cust (cust_no) → R061 不命中（裸名回归保护）"""
        sql = ("CREATE TABLE t2 (id BIGINT NOT NULL, cust_no VARCHAR(20) NOT NULL, "
               "PRIMARY KEY (id), KEY idx_cust (cust_no)) ENGINE=InnoDB")
        hit, msg = _has_r061(checker, sql)
        assert not hit, f"裸名合规索引不应误报: {msg}"

    def test_e1_backtick_uppercase_idx(self, checker):
        """E1: KEY `IDX_CUST` (`cust_no`) → R061 不命中（大小写不敏感）"""
        sql = ("CREATE TABLE `t3` (`id` BIGINT NOT NULL, `cust_no` VARCHAR(20) NOT NULL, "
               "PRIMARY KEY (`id`), KEY `IDX_CUST` (`cust_no`)) ENGINE=InnoDB")
        hit, msg = _has_r061(checker, sql)
        assert not hit, f"大写反引号合规索引不应误报: {msg}"

    def test_e4_backtick_long_idx(self, checker):
        """E4: KEY `idx_cust_no` (`cust_no`) → R061 不命中"""
        sql = ("CREATE TABLE `t4` (`id` BIGINT NOT NULL, `cust_no` VARCHAR(20) NOT NULL, "
               "PRIMARY KEY (`id`), KEY `idx_cust_no` (`cust_no`)) ENGINE=InnoDB")
        hit, msg = _has_r061(checker, sql)
        assert not hit, f"长名反引号合规索引不应误报: {msg}"


# ── N 类：反向鉴别（真实违规必须命中）──

class TestRealViolations:
    """N1/N2/N3：真实违规必须命中，且消息含原始大小写"""

    def test_n1_backtick_non_idx(self, checker):
        """N1: KEY `cus_IDX1` (`cust_no`) → R061 命中，消息含 cus_IDX1（原始大小写）"""
        sql = ("CREATE TABLE `t5` (`id` BIGINT NOT NULL, `cust_no` VARCHAR(20) NOT NULL, "
               "PRIMARY KEY (`id`), KEY `cus_IDX1` (`cust_no`)) ENGINE=InnoDB")
        hit, msg = _has_r061(checker, sql)
        assert hit, "非 idx_ 前缀的反引号索引名必须命中 R061"
        assert "cus_IDX1" in msg, f"告警消息应含原始大小写索引名 cus_IDX1: {msg}"

    def test_n2_bare_non_idx(self, checker):
        """N2: KEY cus_IDX1 (cust_no) → R061 命中"""
        sql = ("CREATE TABLE t6 (id BIGINT NOT NULL, cust_no VARCHAR(20) NOT NULL, "
               "PRIMARY KEY (id), KEY cus_IDX1 (cust_no)) ENGINE=InnoDB")
        hit, msg = _has_r061(checker, sql)
        assert hit, "裸名非 idx_ 前缀索引必须命中 R061"

    def test_n3_mixed_indexes(self, checker):
        """N3: KEY `idx_a` (`a`), KEY `bad_b` (`b`) → R061 命中，消息指向 bad_b 而非 idx_a"""
        sql = ("CREATE TABLE `t7` (`id` BIGINT NOT NULL, `a` INT, `b` INT, "
               "PRIMARY KEY (`id`), KEY `idx_a` (`a`), KEY `bad_b` (`b`)) ENGINE=InnoDB")
        hit, msg = _has_r061(checker, sql)
        assert hit, "混合索引中 bad_b 必须命中 R061"
        assert "bad_b" in msg, f"告警消息应指向 bad_b 而非 idx_a: {msg}"


# ── E3：无索引 ──

class TestNoIndex:
    def test_e3_no_indexes(self, checker):
        """E3: 无任何索引 → R061 不命中"""
        sql = "CREATE TABLE t8 (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB"
        hit, msg = _has_r061(checker, sql)
        assert not hit, f"无索引表不应命中 R061: {msg}"


# ── U 类：ADJ-5 现状锁定（UNIQUE 分支当前为死代码）──

class TestUniqueDeadCode:
    """U1/U2：锁定 §6.1 现状——parsed.indexes 不产出 UNIQUE 条目（ADJ-5）"""

    def test_u1_unique_uk_prefix(self, checker):
        """U1: UNIQUE KEY `uk_code` (`code`) → R061 不命中

        ⚠️ 当前不命中是因为 parsed.indexes 不产出 UNIQUE 条目（ADJ-5），
        而非 R061 认可 uk_ 前缀；若此断言将来失败，说明 ADJ-5 已被修复，
        需重新评估 R061 的 UNIQUE 分支。
        """
        sql = ("CREATE TABLE `t9` (`id` BIGINT NOT NULL, `code` VARCHAR(16) NOT NULL, "
               "PRIMARY KEY (`id`), UNIQUE KEY `uk_code` (`code`)) ENGINE=InnoDB")
        hit, msg = _has_r061(checker, sql)
        assert not hit, f"UNIQUE 分支当前为死代码（ADJ-5），不应命中: {msg}"

    def test_u2_unique_ux_prefix(self, checker):
        """U2: UNIQUE KEY `ux_code` (`code`) → R061 不命中

        ⚠️ 同上——当前不命中是因为 parsed.indexes 不产出 UNIQUE 条目（ADJ-5），
        而非 R061 认可 ux_ 前缀；若此断言将来失败，说明 ADJ-5 已被修复，
        需重新评估 R061 的 UNIQUE 分支。
        """
        sql = ("CREATE TABLE `t10` (`id` BIGINT NOT NULL, `code` VARCHAR(16) NOT NULL, "
               "PRIMARY KEY (`id`), UNIQUE KEY `ux_code` (`code`)) ENGINE=InnoDB")
        hit, msg = _has_r061(checker, sql)
        assert not hit, f"UNIQUE 分支当前为死代码（ADJ-5），不应命中: {msg}"


# ── G 类：生产报告原样 DDL 回放 ──

class TestProductionReplay:
    """G1/G2：生产报告 6297 原样 DDL"""

    def test_g1_big_audit_trail(self, checker):
        """G1: 生产 #1 big_audit_trail 原样 DDL → R061 不命中，且 R029/R036/R037 仍命中

        第二个断言是本用例的核心安全性质——证明修复"只减 R061"，
        其他规则的审核结果原样保留。
        """
        sql = _read_fixture("report_01_big_audit_trail.sql")
        result = checker.audit_sql(sql, instance_type='distributed')
        rule_ids = {v.rule_id for v in result.violations}
        assert 'R061' not in rule_ids, f"生产 #1 的反引号 idx_ 索引不应误报: {sorted(rule_ids)}"
        assert {'R029', 'R036', 'R037'} <= rule_ids, \
            f"生产 #1 的其他规则必须原样保留（证明只减 R061）: {sorted(rule_ids)}"

    def test_g2_cus_name_list_type(self, checker):
        """G2: 生产 #5 cus_name_list_type 原样 DDL → R061 仍命中（真实违规不放过）

        使用 6297 报告 #5 的原样形态 fixture（含非 idx_ 前缀索引），
        与 v1.6.1.9 的 report_05 简化 fixture（无索引，供 R077/R054 用）区分。
        """
        sql = _read_fixture("report_6297_05_cus_name_list_type_full.sql")
        hit, msg = _has_r061(checker, sql)
        assert hit, "生产 #5 的非 idx_ 前缀索引（真实违规）必须命中 R061"
        assert "CUS_NAME_LIST_TYPE_IDX1" in msg, f"告警消息应含原始大小写索引名: {msg}"
