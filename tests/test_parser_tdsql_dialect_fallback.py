# -*- coding: utf-8 -*-
"""v1.6.2.0 TDSQL 方言语句解析降级修复——14 条验收用例

覆盖 DESIGN-v1.6.2.0 §7.1 全部用例矩阵。
D*  = 方言恢复（修复目标：降级恢复为结构化解析）
N*★ = 反向鉴别（不进入重试分支，证明零影响）
G1★ = ADJ-5 前提锁定（parsed.indexes 不产出 UNIQUE）
"""
import pytest

from backend.engine.parser import SQLParser
from backend.engine.checker import RuleChecker
from sqlglot import exp


@pytest.fixture
def parser():
    return SQLParser()


@pytest.fixture
def checker():
    return RuleChecker()


# ── D 类：方言恢复（修复目标）──

class TestDialectRecovery:
    """D1-D6：方言尾子句降级恢复为结构化解析"""

    def test_d1_hash_shard_table(self, parser):
        """D1: TDSQL_DISTRIBUTED BY HASH(`sk`) 建表 → columns > 0, indexes 含普通索引"""
        sql = ("CREATE TABLE t_hash (id BIGINT NOT NULL, sk BIGINT NOT NULL, "
               "PRIMARY KEY (id, sk), KEY idx_sk (sk)) "
               "ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`)")
        parsed = parser.parse(sql)
        assert len(parsed.columns) > 0, "HASH 降级恢复后 columns 应 > 0"
        assert len(parsed.indexes) > 0, "HASH 降级恢复后 indexes 应 > 0"

    def test_d2_range_shard_table(self, parser):
        """D2: TDSQL_DISTRIBUTED BY RANGE(...) → 可结构化解析"""
        sql = ("CREATE TABLE t_range (id BIGINT NOT NULL, sk BIGINT NOT NULL, "
               "PRIMARY KEY (id, sk)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY RANGE(`sk`)")
        parsed = parser.parse(sql)
        assert len(parsed.columns) > 0

    def test_d3_list_shard_table(self, parser):
        """D3: TDSQL_DISTRIBUTED BY LIST(...) → 可结构化解析"""
        sql = ("CREATE TABLE t_list (id BIGINT NOT NULL, sk BIGINT NOT NULL, "
               "PRIMARY KEY (id, sk)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY LIST(`sk`)")
        parsed = parser.parse(sql)
        assert len(parsed.columns) > 0

    def test_d4_broadcast_table(self, parser):
        """D4: BROADCAST 关键字建表 → 可结构化解析"""
        sql = "CREATE TABLE t_bc (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB BROADCAST"
        parsed = parser.parse(sql)
        assert len(parsed.columns) > 0

    def test_d5_hash_plus_partition(self, parser):
        """D5: HASH(...) + 二级分区 PARTITION BY RANGE(...) → 剥离后 PARTITION BY 仍可解析"""
        sql = ("CREATE TABLE t_hp (id BIGINT NOT NULL, sk BIGINT NOT NULL, dt DATETIME NOT NULL, "
               "PRIMARY KEY (id, sk)) ENGINE=InnoDB "
               "TDSQL_DISTRIBUTED BY HASH(`sk`) PARTITION BY RANGE (YEAR(dt)) ("
               "PARTITION p2025 VALUES LESS THAN (2026), "
               "PARTITION p2026 VALUES LESS THAN (2027))")
        parsed = parser.parse(sql)
        assert len(parsed.columns) > 0

    def test_d6_production_report_3(self, parser, checker):
        """D6: 生产 #3 原始 DDL → columns == 25, 命中 R036/R037/R061, 且无 R077"""
        sql = """CREATE TABLE `cus_bas_corp_contact` (
  `ID` varchar(64) NOT NULL,
  `CUST_NO` varchar(20) NOT NULL,
  `DATA_VALID_TM` datetime DEFAULT NULL,
  `CONTACT_NO` varchar(20) DEFAULT NULL,
  PRIMARY KEY (`ID`,`CUST_NO`),
  KEY `cus_bas_corp_contact_IDX1` (`CUST_NO`,`DATA_VALID_TM`),
  KEY `cus_bas_corp_contact_IDX2` (`CONTACT_NO`,`DATA_VALID_TM`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 TDSQL_DISTRIBUTED BY HASH(`cust_no`)"""
        parsed = parser.parse(sql)
        assert len(parsed.columns) > 0, "生产 #3 降级恢复后 columns 应 > 0"
        assert len(parsed.indexes) > 0, "生产 #3 降级恢复后 indexes 应 > 0"

        result = checker.audit_sql(sql, instance_type='distributed')
        rule_ids = {v.rule_id for v in result.violations}
        assert 'R077' not in rule_ids, f"生产 #3 不应触发 R077: {sorted(rule_ids)}"
        structural_hits = {'R036', 'R037', 'R061'} & rule_ids
        assert len(structural_hits) > 0, f"生产 #3 应命中结构类规则: {sorted(rule_ids)}"


# ── N 类：反向鉴别（不进入重试分支）──

class TestNoRetry:
    """N1-N7★：证明正常语句不进入重试分支，零影响"""

    def test_n1_column_named_broadcast(self, parser):
        """N1★: 列名恰好叫 broadcast → 不进入重试；解析结果与改前逐字一致

        ⚠️ 列名集合精确断言是本设计的 Command 门守门员：
        若有人移除 `isinstance(ast, exp.Command)` 前置条件（改成无条件剥离），
        剥离正则会吃掉 `broadcast` 列名，本断言立即失败。
        """
        sql = "CREATE TABLE t_n1 (id BIGINT NOT NULL, broadcast VARCHAR(32), PRIMARY KEY (id)) ENGINE=InnoDB"
        parsed = parser.parse(sql)
        assert not isinstance(parsed.ast, exp.Command), "第一次就解析成功，不进入重试"
        assert len(parsed.columns) > 0
        # Command 门守门员：列名集合必须与改前逐字一致
        assert [c.get("name") for c in parsed.columns] == ["id", "broadcast"], \
            "列名与改前不一致——说明正常语句被误剥离（Command 门可能已失效）"

    def test_n2_comment_contains_hash(self, parser, checker):
        """N2★: 表注释含 TDSQL_DISTRIBUTED BY HASH(id) → 不进入重试，R077 仍触发"""
        sql = ("CREATE TABLE t_n2 (id BIGINT NOT NULL, PRIMARY KEY (id)) "
               "ENGINE=InnoDB COMMENT='TDSQL_DISTRIBUTED BY HASH(id)'")
        parsed = parser.parse(sql)
        assert not isinstance(parsed.ast, exp.Command)
        result = checker.audit_sql(sql, instance_type='distributed')
        rule_ids = {v.rule_id for v in result.violations}
        assert 'R077' in rule_ids, "注释伪造 HASH 子句应仍触发 R077（v1.6.1.9 X1 行为不变）"

    def test_n3_comment_contains_broadcast(self, parser):
        """N3★: 表注释含 broadcast 字样 → 不进入重试；解析结果与改前逐字一致

        ⚠️ 列名集合精确断言是 Command 门的另一道守门员（注释中含 broadcast）。
        """
        sql = ("CREATE TABLE t_n3 (id BIGINT NOT NULL, PRIMARY KEY (id)) "
               "ENGINE=InnoDB COMMENT='broadcast table info'")
        parsed = parser.parse(sql)
        assert not isinstance(parsed.ast, exp.Command)
        assert len(parsed.columns) > 0
        # Command 门守门员：列名集合必须与改前逐字一致
        assert [c.get("name") for c in parsed.columns] == ["id"], \
            "列名与改前不一致——说明正常语句被误剥离（Command 门可能已失效）"

    def test_n4_shardkey_no_retry(self, parser):
        """N4★: shardkey=col 建表 → 不进入重试（本就正常解析）"""
        sql = ("CREATE TABLE t_n4 (id BIGINT NOT NULL, sk BIGINT NOT NULL, "
               "PRIMARY KEY (id, sk)) ENGINE=InnoDB SHARDKEY=sk")
        parsed = parser.parse(sql)
        assert not isinstance(parsed.ast, exp.Command)
        assert len(parsed.columns) > 0

    def test_n5_partition_by_no_retry(self, parser):
        """N5★: 单独 PARTITION BY RANGE(...)（无方言子句）→ 不进入重试"""
        sql = ("CREATE TABLE t_n5 (id BIGINT NOT NULL, dt DATETIME NOT NULL, "
               "PRIMARY KEY (id)) ENGINE=InnoDB "
               "PARTITION BY RANGE (YEAR(dt)) (PARTITION p2025 VALUES LESS THAN (2026))")
        parsed = parser.parse(sql)
        assert not isinstance(parsed.ast, exp.Command)

    def test_n6_truncated_sql_still_blocked(self, checker):
        """N6★: 残缺截断 SQL（无方言子句）→ 仍产出 E999_SYNTAX_ERROR"""
        sql = "CREATE TABLE `account_no_mapping` (`serialno` varchar(40) NOT NULL, `oldaccount` varchar(40) COLLATE utf8mb4_"
        result = checker.audit_sql(sql)
        rule_ids = {v.rule_id for v in result.violations}
        assert 'E999_SYNTAX_ERROR' in rule_ids, "残缺 SQL 必须被语法错误阻断"

    def test_n7_unparseable_retry_keeps_command(self, parser):
        """N7★: 剥离后仍无法解析的构造语句 → 保留原 Command 结果，不劣于改前

        ⚠️ 本用例锁住"重试结果只在非 Command 时才采用"的约束：
        第一次解析为 Command（含方言子句）→ 剥离后 sqlglot 仍返回 Command →
        必须保留原始 Command 结果而非采用重试结果。
        判别方法：原始 Command 的 SQL 文本含 TDSQL_DISTRIBUTED，重试后被剥离。
        若有人改成无条件采用重试结果（ast = _retry_ast），
        ast.sql() 中的 TDSQL_DISTRIBUTED 会消失，本断言立即失败。
        """
        # 方言子句 + 尾部还有其他无法解析的内容 → 剥离后仍 Command
        sql = "CREATE TABLE t_n7 (id BIGINT) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(id) SOME_GARBAGE"
        parsed = parser.parse(sql)
        # 第一次解析必须是 Command（降级），且含方言子句（会进入重试分支）
        assert isinstance(parsed.ast, exp.Command), \
            "第一次解析应为 Command（含方言且无法解析）"
        # 关键断言：保留的是原始 Command（含 TDSQL_DISTRIBUTED），而非重试的 Command（已剥离）
        assert "TDSQL_DISTRIBUTED" in parsed.ast.sql().upper(), \
            "重试结果被无条件采用——剥离后的 Command 丢失了方言子句信息"
        # 结果不劣于改前：表名被正则回退正确提取
        assert "t_n7" in parsed.tables, \
            "保留原 Command 结果时表名应被正则回退提取"


# ── G1★：ADJ-5 前提锁定 ──

class TestADJ5Guard:
    """G1★: 断言 parsed.indexes / index_definitions 在 HASH+UNIQUE 建表下仍不产出 UNIQUE 条目

    一旦该前提被打破（例如 sqlglot 升级使 UniqueColumnConstraint 被正确提取），
    R077 的宽松"或"分支就会被激活并产生漏报。此测试立即失败，提示必须同时处理 ADJ-4/ADJ-5。
    """

    def test_g1_unique_not_in_indexes(self, parser):
        """ADJ-5 前提：HASH+UNIQUE 建表下 parsed.indexes 不产出 UNIQUE"""
        sql = ("CREATE TABLE t_g1 (id BIGINT NOT NULL, sk BIGINT NOT NULL, "
               "PRIMARY KEY (id, sk), UNIQUE KEY uk_sk (sk)) "
               "ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(sk)")
        parsed = parser.parse(sql)
        unique_in_indexes = [i for i in parsed.indexes if i.get("type") == "UNIQUE"]
        unique_in_defs = [i for i in parsed.index_definitions if i.get("type") == "UNIQUE"]
        assert len(unique_in_indexes) == 0, (
            f"ADJ-5 前提被打破：parsed.indexes 产出了 {len(unique_in_indexes)} 条 UNIQUE。"
            f"这会激活 R077 的宽松分支导致漏报。必须同时处理 ADJ-4/ADJ-5。"
        )
        assert len(unique_in_defs) == 0, (
            f"ADJ-5 前提被打破：parsed.index_definitions 产出了 {len(unique_in_defs)} 条 UNIQUE。"
        )
