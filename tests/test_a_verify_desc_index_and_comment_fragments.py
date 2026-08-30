# -*- coding: utf-8 -*-
"""A 核查报告（VERIFY-v1.6.2.2-R1.1）§6 两个既有缺陷的修复回归测试

A-6.1：解析降级时 `--` 注释残片被当表名 → R001 误报
  - 降级路径表名提取前剥离注释；
  - R001 跳过纯标点残片候选（防御双保险）。
A-6.2：MySQL 8.0 降序索引 `PRIMARY KEY (col DESC)` 在 sqlglot 29/30 全版本解析失败
  - 解析层纯文本预处理剥离索引列 ASC/DESC 修饰（仅 DDL、跳过 CTAS、字符串/注释外）；
  - R054/R077 正则回退的列 token 去除 ASC/DESC 后缀，分片键合规判定不再误报。
"""
import pytest

from backend.engine.checker import RuleChecker


@pytest.fixture(scope="module")
def checker():
    return RuleChecker()


def _fired(checker, sql):
    r = checker.audit_sql(sql, instance_type="distributed")
    return {v.rule_id for v in r.violations}, r


# ── A-6.2：DESC/ASC 索引修饰解析与分片键合规 ────────────────────

DESC_PK_DDL = """CREATE TABLE kitp_rate_plan (
  id INT NOT NULL,
  rate_plan_code VARCHAR(32) NOT NULL,
  PRIMARY KEY (`id` DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='x' shardkey=id
"""


class TestDescIndexParsing:
    def test_desc_pk_parses_no_e999(self, checker):
        """O 报告现场样例：PRIMARY KEY (`id` DESC) 不得解析失败"""
        fired, r = _fired(checker, DESC_PK_DDL)
        assert "E999_SYNTAX_ERROR" not in fired, "DESC 主键不得解析失败"
        assert r.sql_type == "CREATE TABLE"
        p = checker.parser.parse(DESC_PK_DDL)
        assert p.tables == ["kitp_rate_plan"]
        # 结构类规则恢复完整覆盖：主键已识别，R003/R004/R005 不得误报
        assert "R003" not in fired
        assert "R004" not in fired
        assert "R005" not in fired

    def test_desc_pk_shardkey_compliant(self, checker):
        """分片键在 DESC 主键内 → R054/R077 不得误报"""
        fired, _ = _fired(checker, DESC_PK_DDL)
        assert "R054" not in fired
        assert "R077" not in fired

    def test_desc_pk_shardkey_absent_still_flags(self, checker):
        """反向鉴别：分片键不在 DESC 主键内仍必须触发（功能未被改没）"""
        ddl = """CREATE TABLE t_desc_bad (
          id INT NOT NULL,
          cust_id BIGINT NOT NULL,
          PRIMARY KEY (`id` DESC)
        ) ENGINE=InnoDB COMMENT='x' shardkey=cust_id
        """
        fired, _ = _fired(checker, ddl)
        assert "R077" in fired
        assert "R054" in fired

    def test_multi_col_desc_index(self, checker):
        """多列索引混合 ASC/DESC：解析成功且列提取完整"""
        ddl = """CREATE TABLE t_multi (
          a INT NOT NULL, b INT NOT NULL, c INT NOT NULL,
          PRIMARY KEY (`a`),
          KEY idx_ab (`a` DESC, `b` ASC),
          UNIQUE KEY uk_abc (`a`, `b` DESC, `c`)
        ) ENGINE=InnoDB COMMENT='x' shardkey=a
        """
        fired, r = _fired(checker, ddl)
        assert "E999_SYNTAX_ERROR" not in fired
        # 唯一索引含分片键 a → R054/R077 不触发
        assert "R054" not in fired
        assert "R077" not in fired

    def test_ctas_not_stripped(self, checker):
        """CTAS 保持原文（可能携带 ORDER BY，不得剥离方向语义）"""
        from backend.engine.parser.parser_legacy import _strip_index_order_modifiers
        ctas = "CREATE TABLE t_ct AS SELECT a, b FROM t_src ORDER BY a DESC"
        assert _strip_index_order_modifiers(ctas) == ctas

    def test_literal_with_desc_not_stripped(self, checker):
        """字符串字面量内的 ' DESC,' 不得被剥离"""
        from backend.engine.parser.parser_legacy import _strip_index_order_modifiers
        ddl = "CREATE TABLE t_lit (a INT DEFAULT 'x DESC,', PRIMARY KEY (a))"
        out = _strip_index_order_modifiers(ddl)
        assert "'x DESC,'" in out

    def test_select_order_by_not_stripped(self, checker):
        """非 DDL 语句不做剥离（ORDER BY 方向语义保留）"""
        from backend.engine.parser.parser_legacy import _strip_index_order_modifiers
        sel = "SELECT a FROM t ORDER BY a DESC"
        assert _strip_index_order_modifiers(sel) == sel

    def test_asc_desc_in_comment_not_stripped(self, checker):
        """注释内的 DESC 文本保持原样（注释完整性）"""
        from backend.engine.parser.parser_legacy import _strip_index_order_modifiers
        ddl = ("CREATE TABLE t_c (\n"
               "  a INT, PRIMARY KEY (a) -- keep DESC here\n"
               ") ENGINE=InnoDB")
        out = _strip_index_order_modifiers(ddl)
        assert "-- keep DESC here" in out


# ── A-6.1：降级路径注释残片表名 ────────────────────────────────

COMMENT_HEADER_BROKEN_DDL = """-- SQL Object: CREATE TABLE
-- Table: kitp_rate_plan
CREATE TABLE kitp_rate_plan (
  id INT NOT NULL,
  PRIMARY KEY (`id` DESC, `oops`
) ENGINE=InnoDB shardkey=id
"""


class TestCommentFragmentTableName:
    def test_no_double_dash_table_name(self, checker):
        """解析失败 + 注释头：表名不得提取成 `--` 残片，R001 不得误报"""
        fired, r = _fired(checker, COMMENT_HEADER_BROKEN_DDL)
        p = checker.parser.parse(COMMENT_HEADER_BROKEN_DDL)
        assert "--" not in p.tables
        assert not any(v.rule_id == "R001" and "'--'" in v.message
                       for v in r.violations), \
            "R001 不得对 `--` 注释残片报命名违规"

    def test_fallback_extracts_real_table_name(self, checker):
        """降级路径剥离注释后仍能提取真实表名"""
        p = checker.parser.parse(COMMENT_HEADER_BROKEN_DDL)
        assert "kitp_rate_plan" in p.tables

    def test_r001_skips_pure_punctuation_candidates(self, checker):
        """R001 单元级：纯标点候选名直接跳过（防御双保险）"""
        from backend.engine.parser import ParsedSQL
        from backend.engine.rules.naming import R001NamingLength
        rule = R001NamingLength()
        parsed = ParsedSQL(raw_sql="x")
        parsed.tables = ["--", "...", "``"]
        assert rule.check(parsed) is None

    def test_r001_still_flags_bad_names(self, checker):
        """反向鉴别：真实不合规表名仍必须报（功能未被改没）"""
        from backend.engine.parser import ParsedSQL
        from backend.engine.rules.naming import R001NamingLength
        rule = R001NamingLength()
        parsed = ParsedSQL(raw_sql="x")
        parsed.tables = ["BadName"]
        assert rule.check(parsed) is not None
        parsed2 = ParsedSQL(raw_sql="x")
        parsed2.tables = ["9starts_with_digit"]
        assert rule.check(parsed2) is not None
