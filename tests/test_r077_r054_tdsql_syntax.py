# -*- coding: utf-8 -*-
"""v1.6.1.9 R077/R054 TDSQL 建表语法识别缺陷修复——41 条验收用例

覆盖 DESIGN-v1.6.1.9 §9.1 全部用例矩阵 + §C.1 四条补充测试。
执行口径：instance_type="distributed"，table_metadata=None（X9 除外）。
判定方式：(R077 是否触发, R054 是否触发) 与期望逐位比对。

用例分类：
  P*  = 正向合规（修复目标：误报消除）
  N*★ = 反向鉴别（必须触发，证明没有把功能改没）
  C*  = 对照（既有正确行为不变）
  X*★ = 边界反例（安全防线，证明不会被伪造/注入绕过）
"""
import os

import pytest

from backend.engine.checker import RuleChecker

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _read_fixture(filename: str) -> str:
    with open(os.path.join(FIXTURE_DIR, filename), encoding="utf-8") as f:
        # 剔除 -- 起始的元信息行
        return "\n".join(
            line for line in f.read().splitlines()
            if not line.strip().startswith("--")
        ).strip()


# ── 生产报告 fixture ──
SQL_P1 = _read_fixture("report_03_cus_bas_corp_contact.sql")
SQL_P2 = _read_fixture("report_05_cus_name_list_type.sql")
SQL_P3 = _read_fixture("report_08_t_branch.sql")
SQL_P4 = _read_fixture("report_11_t_dict.sql")
SQL_P5 = _read_fixture("report_13_t_product.sql")
SQL_N1 = _read_fixture("report_04_single_table.sql")

# ── 全部 41 条用例（含期望值）──
# 格式: (用例编号, 场景描述, SQL, 期望_R077触发, 期望_R054触发)
CASES = [
    # ── P 类：正向合规（修复目标）──
    ("P1", "现场#3 HASH 分片表，cust_no ∈ 主键", SQL_P1, False, False),
    ("P2", "现场#5 广播表 noshardkey_allset", SQL_P2, False, False),
    ("P3", "现场#8 t_branch 广播表（含 UNIQUE KEY）", SQL_P3, False, False),
    ("P4", "现场#11 t_dict 广播表", SQL_P4, False, False),
    ("P5", "现场#13 t_product 广播表", SQL_P5, False, False),

    # ── N 类：反向鉴别（必须触发）──
    ("N1", "现场#4 无任何分片声明", SQL_N1, True, False),
    ("N2", "SHARDKEY=cust_id 不在主键",
     "CREATE TABLE t_bad (id BIGINT NOT NULL, cust_id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB SHARDKEY=cust_id",
     True, True),
    ("N3", "HASH 分片键不在主键",
     "CREATE TABLE `t3` (`id` bigint NOT NULL, `cust_no` varchar(20) NOT NULL, PRIMARY KEY (`id`)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`cust_no`)",
     True, True),
    ("N8", "HASH 分片键只在普通 KEY 里（守 NJ-1）",
     "CREATE TABLE `t8` (`id` varchar(64) NOT NULL, `cust_no` varchar(20) NOT NULL, `dt` datetime, PRIMARY KEY (`id`), KEY `idx1` (`cust_no`,`dt`)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`cust_no`)",
     True, True),

    # ── C 类：对照（既有正确行为不变）──
    ("C1", "合规分片表（分片键 ∈ 主键）",
     "CREATE TABLE t_ok (id BIGINT NOT NULL, cust_id BIGINT NOT NULL, PRIMARY KEY (id, cust_id)) ENGINE=InnoDB SHARDKEY=cust_id",
     False, False),
    ("C2", "BROADCAST 关键字广播表",
     "CREATE TABLE t_bc (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB BROADCAST",
     False, False),
    ("C3", "分片键在反引号 UNIQUE 但不在主键（守 NJ-2）",
     "CREATE TABLE `tu1` (`id` bigint NOT NULL, `code` varchar(16) NOT NULL, PRIMARY KEY (`id`), UNIQUE KEY `uk_code` (`code`)) ENGINE=InnoDB shardkey=code",
     True, True),
    ("C6", "HASH 大小写混排 + 多空格，键 ∈ 主键",
     "CREATE TABLE t6 (id bigint NOT NULL, sk bigint NOT NULL, PRIMARY KEY (id,sk)) ENGINE=InnoDB tdsql_Distributed  By  Hash( SK )",
     False, False),
    ("C7", "CTAS",
     "CREATE TABLE t_ctas AS SELECT * FROM t_src",
     False, False),
    ("C8", "临时表",
     "CREATE TEMPORARY TABLE t_tmp (id bigint NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB",
     False, False),
    ("C9", "非建表语句",
     "SELECT * FROM t_account WHERE id=1",
     False, False),
    ("N7", "注释含哨兵字样但真实分片键合规",
     "CREATE TABLE t_cmt (id BIGINT NOT NULL, sk BIGINT NOT NULL, PRIMARY KEY (id, sk)) ENGINE=InnoDB COMMENT='noshardkey_allset 说明' SHARDKEY=sk",
     False, False),
    ("N4", "反引号 UNIQUE 不含分片键且不在主键",
     "CREATE TABLE `tu4` (`id` bigint NOT NULL, `code` varchar(16) NOT NULL, `sk` bigint NOT NULL, PRIMARY KEY (`id`), UNIQUE KEY `uk_code` (`code`)) ENGINE=InnoDB shardkey=sk",
     True, True),
    ("N5", "普通 KEY 含分片键（守 NJ-1）",
     "CREATE TABLE `tk1` (`id` bigint NOT NULL, `sk` bigint NOT NULL, PRIMARY KEY (`id`), KEY `idx_sk` (`sk`)) ENGINE=InnoDB shardkey=sk",
     True, True),

    # ── X 类：边界反例（安全防线）──
    ("X1", "表 COMMENT 伪造 HASH 子句",
     "CREATE TABLE t_x1 (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB COMMENT='TDSQL_DISTRIBUTED BY HASH(id)'",
     True, False),
    ("X2", "块注释伪造 HASH 子句",
     "CREATE TABLE t_x2 (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB /* TDSQL_DISTRIBUTED BY HASH(id) */",
     True, False),
    ("X2b", "行注释伪造 HASH 子句",
     "CREATE TABLE t_x2b (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB -- TDSQL_DISTRIBUTED BY HASH(id)",
     True, False),
    ("X3", "HASH('id') 单引号非标识符",
     "CREATE TABLE t_x3 (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH('id')",
     True, False),
    ("X4", "BY KEY(id) 无权威依据",
     "CREATE TABLE t_x4 (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY KEY(id)",
     True, False),
    ("X5", "noshardkey_shadow 是真实列且不在主键",
     "CREATE TABLE t_x5 (id BIGINT NOT NULL, noshardkey_shadow BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB shardkey=noshardkey_shadow",
     True, True),
    ("X6", "HASH 键 ∈ 主键，反引号 UNIQUE 不含键（违反 J-3）",
     "CREATE TABLE t_x6 (id BIGINT NOT NULL, sk BIGINT NOT NULL, PRIMARY KEY (id, sk), UNIQUE KEY `uk_id` (`id`)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(sk)",
     False, True),
    ("X7", "HASH 键 ∉ 主键、只在裸名 UNIQUE（违反 J-2）",
     "CREATE TABLE t_x7 (id BIGINT NOT NULL, sk BIGINT NOT NULL, PRIMARY KEY (id), UNIQUE KEY uk_sk (sk)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(sk)",
     False, True),
    ("X8", "两个 UNIQUE 仅一个含 HASH 键（守 NJ-3）",
     "CREATE TABLE t_x8 (id BIGINT NOT NULL, sk BIGINT NOT NULL, c BIGINT NOT NULL, PRIMARY KEY (id, sk), UNIQUE KEY `uk_a` (`sk`,`c`), UNIQUE KEY `uk_b` (`c`)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(sk)",
     False, True),
    ("X12", "现场#3 换行/大小写/空白变体",
     SQL_P1.replace("TDSQL_DISTRIBUTED BY HASH(`cust_no`)", "tdsql_distributed\n  by  hash (  `CUST_NO` )"),
     False, False),
    ("X14", "COMMENT 字符串伪造广播哨兵",
     "CREATE TABLE fk1 (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB COMMENT='shardkey=noshardkey_allset'",
     True, False),
    ("X15", "块注释伪造广播哨兵",
     "CREATE TABLE fk2 (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB /* shardkey=noshardkey_allset */",
     True, False),
    ("X16", "-- 行注释伪造广播哨兵",
     "CREATE TABLE fk3 (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB -- shardkey=noshardkey_allset",
     True, False),
    ("X17", "# 行注释伪造广播哨兵",
     "CREATE TABLE fk4 (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB # shardkey=noshardkey_allset",
     True, False),
    ("X18", "真实哨兵 + 注释另有干扰文本",
     "CREATE TABLE fk5 (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB COMMENT='干扰文本 shardkey=zzz' shardkey=noshardkey_allset",
     False, False),
    ("X19", "HASH，无主键，裸名 UNIQUE 含键（违反 J-2）",
     "CREATE TABLE mp1 (id BIGINT, sk BIGINT, UNIQUE KEY uk_sk (sk)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(sk)",
     False, True),
    ("X20", "HASH，无主键，反引号 UNIQUE 含键",
     "CREATE TABLE `mp2` (`id` BIGINT, `sk` BIGINT, UNIQUE KEY `uk_sk` (`sk`)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`)",
     True, True),
    ("X21", "legacy SHARDKEY=，无主键，UNIQUE 含键",
     "CREATE TABLE mp3 (id BIGINT, sk BIGINT, UNIQUE KEY uk_sk (sk)) ENGINE=InnoDB SHARDKEY=sk",
     False, True),
    ("X22", "HASH，有主键且含键，无 UNIQUE",
     "CREATE TABLE mp4 (id BIGINT NOT NULL, sk BIGINT NOT NULL, PRIMARY KEY (id, sk)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(sk)",
     False, False),
    ("X23", "CHECK(a--b > 0) + 合法 HASH（MySQL -- 词法）",
     "CREATE TABLE dm (a BIGINT NOT NULL, b BIGINT NOT NULL, PRIMARY KEY(a), CHECK(a--b > 0)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(a)",
     False, False),
    ("X24", "真正的 -- 行注释含 HASH 子句",
     "CREATE TABLE dm2 (a BIGINT NOT NULL, PRIMARY KEY(a)) ENGINE=InnoDB -- TDSQL_DISTRIBUTED BY HASH(a)",
     True, False),
    ("X25", "noshardkey_allset-x 畸形值（token 边界）",
     "CREATE TABLE tk (id BIGINT NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB shardkey=noshardkey_allset-x",
     True, False),
]


@pytest.fixture
def checker():
    return RuleChecker()


@pytest.mark.parametrize("case_id,desc,sql,expect_r077,expect_r054",
                         CASES, ids=[c[0] for c in CASES])
def test_r077_r054_matrix(checker, case_id, desc, sql, expect_r077, expect_r054):
    """41 条验收用例矩阵：R077/R054 触发状态与期望逐位比对"""
    result = checker.audit_sql(sql, instance_type='distributed')
    rule_ids = {v.rule_id for v in result.violations}
    has_r077 = 'R077' in rule_ids
    has_r054 = 'R054' in rule_ids

    assert has_r077 == expect_r077, (
        f"[{case_id}] {desc}: R077 期望={'触发' if expect_r077 else '不触发'}, "
        f"实际={'触发' if has_r077 else '不触发'}\n"
        f"  全部违规: {sorted(rule_ids)}\n"
        f"  SQL: {sql[:200]}"
    )
    assert has_r054 == expect_r054, (
        f"[{case_id}] {desc}: R054 期望={'触发' if expect_r054 else '不触发'}, "
        f"实际={'触发' if has_r054 else '不触发'}\n"
        f"  全部违规: {sorted(rule_ids)}\n"
        f"  SQL: {sql[:200]}"
    )


# ── C.1 补充测试 ──

class TestSupplementary:
    """附录 C.1 四条补充测试"""

    def test_x9_metadata_broadcast_sentinel(self, checker):
        """X9: DDL 含 shardkey=noshardkey_allset 且 table_metadata 也返回该哨兵 → 零违规"""
        sql = ("CREATE TABLE t_meta (id BIGINT NOT NULL, PRIMARY KEY (id)) "
               "ENGINE=InnoDB shardkey=noshardkey_allset")
        metadata = {"t_meta": {"shard_key": "noshardkey_allset", "is_shard_table": False}}
        result = checker.audit_sql(sql, instance_type='distributed', table_metadata=metadata)
        rule_ids = {v.rule_id for v in result.violations}
        assert 'R077' not in rule_ids, f"X9 元数据通道广播哨兵不应触发 R077: {sorted(rule_ids)}"
        assert 'R054' not in rule_ids, f"X9 元数据通道广播哨兵不应触发 R054: {sorted(rule_ids)}"

    def test_x10_characterization_broadcast_plus_shardkey(self, checker):
        """X10: BROADCAST + 真实 shardkey=col 冲突（特征化测试）

        锁定 §8.3 现状：本行为源于用户对 ADJ-6 的关闭决策，
        不代表 TDSQL 官方合规语法。行为一旦变化立即报警。
        """
        # 变体 1: sk ∈ 主键
        sql_v1 = ("CREATE TABLE t_con1 (id BIGINT NOT NULL, sk BIGINT NOT NULL, "
                  "PRIMARY KEY (id, sk)) ENGINE=InnoDB BROADCAST SHARDKEY=sk")
        result_v1 = checker.audit_sql(sql_v1, instance_type='distributed')
        ids_v1 = {v.rule_id for v in result_v1.violations}

        # 变体 2: sk ∉ 主键
        sql_v2 = ("CREATE TABLE t_con2 (id BIGINT NOT NULL, sk BIGINT NOT NULL, "
                  "PRIMARY KEY (id)) ENGINE=InnoDB BROADCAST SHARDKEY=sk")
        result_v2 = checker.audit_sql(sql_v2, instance_type='distributed')
        ids_v2 = {v.rule_id for v in result_v2.violations}

        # 特征化断言：锁定当前行为（ADJ-6 已关闭）
        # BROADCAST 快速通道 → R077 不触发（两个变体相同）
        assert 'R077' not in ids_v1, f"X10-v1: BROADCAST+sk∈主键不应触发R077: {sorted(ids_v1)}"
        assert 'R077' not in ids_v2, f"X10-v2: BROADCAST+sk∉主键不应触发R077: {sorted(ids_v2)}"

    def test_x11_bare_vs_backtick_unique_index(self, checker):
        """X11: 裸索引名与反引号索引名的同语义 DDL 结果必须一致"""
        sql_bare = ("CREATE TABLE t_bare (id BIGINT NOT NULL, sk BIGINT NOT NULL, "
                    "PRIMARY KEY (id, sk), UNIQUE KEY uk_sk (sk)) "
                    "ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(sk)")
        sql_bt = ("CREATE TABLE `t_bt` (`id` BIGINT NOT NULL, `sk` BIGINT NOT NULL, "
                  "PRIMARY KEY (`id`, `sk`), UNIQUE KEY `uk_sk` (`sk`)) "
                  "ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`)")

        result_bare = checker.audit_sql(sql_bare, instance_type='distributed')
        result_bt = checker.audit_sql(sql_bt, instance_type='distributed')

        ids_bare = {v.rule_id for v in result_bare.violations if v.rule_id in ('R077', 'R054')}
        ids_bt = {v.rule_id for v in result_bt.violations if v.rule_id in ('R077', 'R054')}

        assert ids_bare == ids_bt, (
            f"X11: 裸索引名与反引号索引名结果不一致: "
            f"bare={sorted(ids_bare)}, backtick={sorted(ids_bt)}"
        )

    def test_x13_unique_re_atomic_guard(self, checker):
        """X13: ADJ-5 承重性断言——只放宽 _UNIQUE_RE 不动 R077 判定会漏报

        以子类方式构造"只修 _UNIQUE_RE 支持反引号、不动 R077 判定"的变体，
        断言其在反引号 UNIQUE + 分片键 ∉ 主键的场景下会漏报。
        作用：将来若有人或依赖升级激活了 R077 的宽松分支，此测试立即失败。
        """
        from backend.engine.rules.distributed import R077CreateTableMustHaveShardKey
        import re as _re

        class R077WithFixedUniqueRe(R077CreateTableMustHaveShardKey):
            """只放宽 _UNIQUE_RE 支持反引号索引名，不改判定逻辑的假想变体"""
            _UNIQUE_RE = _re.compile(
                r"unique\s+(?:key|index)\s*(?:`[^`]+`|\w+)?\s*\(([^)]+)\)",
                _re.IGNORECASE,
            )

        rule_fixed = R077WithFixedUniqueRe()
        rule_orig = R077CreateTableMustHaveShardKey()

        from backend.engine.parser import SQLParser
        parser = SQLParser()

        # 反引号 UNIQUE 含分片键、分片键不在主键 → 应该触发 R077
        sql = ("CREATE TABLE `t_guard` (`id` bigint NOT NULL, `sk` bigint NOT NULL, "
               "PRIMARY KEY (`id`), UNIQUE KEY `uk_sk` (`sk`)) "
               "ENGINE=InnoDB shardkey=sk")
        parsed = parser.parse(sql)

        v_orig = rule_orig.check(parsed)
        v_fixed = rule_fixed.check(parsed)

        # 原版（_UNIQUE_RE 不认反引号）→ _collect_unique_index_cols 找不到 → R077 触发
        assert v_orig is not None, "原版 R077 应触发（分片键不在主键，_UNIQUE_RE 不认反引号）"

        # 假想修复版（_UNIQUE_RE 支持反引号）→ _collect_unique_index_cols 找到 sk →
        # R077 的宽松"或"分支放行 → 漏报
        assert v_fixed is None, (
            "X13 承重性断言失败：只修 _UNIQUE_RE 后 R077 仍触发。"
            "这说明 R077 的宽松'或'分支可能已被收紧（ADJ-4 被重新打开），"
            "需要重新评估 ADJ-5 的原子变更约束。"
        )
