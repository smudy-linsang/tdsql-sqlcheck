# -*- coding: utf-8 -*-
"""UAT-O-14 回归测试：换行规范化 + parse_error 失败关闭不变量

覆盖 O 第四轮 UAT 报告 O-14（BLOCK）的验收标准：
1. `-- ordinary\\rCREATE VIEW v AS SELECT 1 +` 在三入口（即时/文件/拆句）
   均不得 passed=true，文件入口不得以 0 条成功结束。
2. LF、CRLF、单 CR、末尾无换行、前导空语句、字符串诱饵、反引号诱饵全覆盖。
3. 非 KFN 的 parse_error 必有一个 ERROR 级阻断项（语句头豁免不得吞掉解析失败）。
4. 全业务规则关闭时不变量仍兜底（强制门禁独立于业务规则集）。
5. 真实特殊语句（无 parse_error）不受误伤。
"""
import pytest

from backend.engine.checker import RuleChecker
from backend.engine.parser import normalize_newlines
from backend.services.database import split_sql_statements


@pytest.fixture
def checker():
    return RuleChecker()


def _fired(checker, sql, instance_type="distributed"):
    r = checker.audit_sql(sql, instance_type=instance_type)
    return {v.rule_id for v in r.violations}, r.passed


# O-14 核心反例的换行变体（同一条残缺 CREATE VIEW）
BROKEN_VIEW_BODIES = [
    "-- ordinary\rCREATE VIEW v AS SELECT 1 +",      # 单 CR（O-14 原始输入）
    "-- ordinary\r\nCREATE VIEW v AS SELECT 1 +",    # CRLF
    "-- ordinary\nCREATE VIEW v AS SELECT 1 +",      # LF 对照
    "CREATE VIEW v AS SELECT 1 +",                   # 无注释、末尾无换行
]

# 其他语句头 + 残缺语法（豁免不得吞掉解析失败）
# 注：每条均实测存在非 KFN parse_error；"LOAD XML INFILE INTO" 会被词法器
# 回退为 Command 而无 parse_error，由 R042 拦截，不属本不变量范围。
OTHER_BROKEN_HEADS = [
    "CREATE PROCEDURE p( BEGIN END",
    "CREATE FUNCTION f( RETURNS INT BEGIN END",
    "CREATE TRIGGER tr BEFORE INSERT ON",
    "LOAD DATA INFILE INTO",
]


class TestNewlineNormalization:
    """拆句/解析/语句头判定必须消费同一份规范化文本"""

    def test_normalize_crlf_cr_to_lf(self):
        assert normalize_newlines("a\r\nb\rc\nd") == "a\nb\nc\nd"

    def test_normalize_idempotent(self):
        assert normalize_newlines(normalize_newlines("a\r\nb\rc")) == "a\nb\nc"

    @pytest.mark.parametrize("sql", BROKEN_VIEW_BODIES)
    def test_file_entry_never_zero_statements(self, checker, sql):
        """文件入口不得把残缺 VIEW 拆成 0 条、不得以成功结束"""
        results = checker.audit_file(sql, file_path="synthetic.sql")
        assert len(results) >= 1, f"文件入口不得拆出 0 条: {sql[:40]!r}"
        assert all(not r.passed for r in results), "残缺 VIEW 不得绿色通过"

    @pytest.mark.parametrize("sql", BROKEN_VIEW_BODIES)
    def test_batch_stream_split_never_zero(self, sql):
        """batch-stream 拆句入口同样不得吞掉注释后的真实语句"""
        assert len(split_sql_statements(sql)) >= 1


class TestParseErrorNeverGreen:
    """解析错误绝不绿色：三入口 × 换行变体 × 语句头诱饵"""

    @pytest.mark.parametrize("sql", BROKEN_VIEW_BODIES + OTHER_BROKEN_HEADS)
    def test_immediate_entry_never_green(self, checker, sql):
        fired, passed = _fired(checker, sql)
        assert passed is False, f"残缺语句不得通过: {sql[:40]!r}"
        assert any("ERROR" in str(s) for s in
                   (v.severity for v in checker.audit_sql(sql).violations)), \
            "parse_error 存在时必有一个 ERROR 级阻断项"

    @pytest.mark.parametrize("sql", BROKEN_VIEW_BODIES)
    def test_file_entry_never_green(self, checker, sql):
        results = checker.audit_file(sql, file_path="synthetic.sql")
        assert results and all(not r.passed for r in results)

    def test_leading_empty_statement_no_green(self, checker):
        """前导空语句 + 残缺 VIEW"""
        results = checker.audit_file("; \nCREATE VIEW v AS SELECT 1 +", file_path="s.sql")
        assert results and all(not r.passed for r in results)

    @pytest.mark.parametrize("sql", [
        "SELECT 'CREATE VIEW' FROM",          # 字符串诱饵（VIEW）
        "SELECT `CREATE PROCEDURE` FROM",     # 反引号诱饵
        "UPDATE t SET n='LOAD DATA' WHERE",   # 字符串诱饵（LOAD）
    ])
    def test_decoy_with_syntax_error_never_green(self, checker, sql):
        fired, passed = _fired(checker, sql)
        assert passed is False
        assert "E999_SYNTAX_ERROR" in fired


class TestFailClosedInvariant:
    """硬性不变量：非 KFN parse_error 不得被语句头豁免吞掉"""

    @pytest.mark.parametrize("sql", BROKEN_VIEW_BODIES + OTHER_BROKEN_HEADS)
    def test_invariant_holds_with_all_rules_disabled(self, checker, sql):
        """全业务规则关闭时，不变量仍必须兜底产出 ERROR"""
        disabled = {r.rule_id: {"enabled": False} for r in checker.rules}
        r = checker.audit_sql(sql, rule_overrides=disabled)
        assert r.passed is False, f"规则全关时仍不得绿色: {sql[:40]!r}"
        assert any(str(v.severity) == "Severity.ERROR" or v.severity == "ERROR"
                   for v in r.violations), "必有一个 ERROR 级阻断项"
        assert any(v.rule_id == "E999_SYNTAX_ERROR" for v in r.violations)


class TestRealSpecialStatementsNotCollateral:
    """真实特殊语句（解析成功，无 parse_error）不得被不变量误伤"""

    @pytest.mark.parametrize("sql", [
        "CREATE VIEW v AS SELECT 1 AS id",
        "CREATE OR REPLACE VIEW v AS SELECT 1 AS id",
        "CREATE PROCEDURE p() BEGIN SELECT 1; END",
        "CREATE FUNCTION f() RETURNS INT DETERMINISTIC BEGIN DECLARE x INT DEFAULT 1; RETURN x; END",
        "CREATE TRIGGER tr BEFORE INSERT ON t FOR EACH ROW SET NEW.id = 1",
        "LOAD DATA INFILE '/tmp/synthetic.csv' INTO TABLE t FIELDS TERMINATED BY ','",
        "LOAD XML INFILE '/tmp/synthetic.xml' INTO TABLE t",
    ])
    def test_no_e999_for_real_statements(self, checker, sql):
        fired, _ = _fired(checker, sql)
        assert "E999_SYNTAX_ERROR" not in fired, f"真实语句不得误报解析错误: {sql[:50]}"

    def test_r042_still_fires_after_normalization(self, checker):
        """O-09 的 LOAD 注释矩阵在换行规范化后不得退化"""
        for comment in ["", "# note\r\n", "# note\r", "-- note\n"]:
            sql = comment + "LOAD XML INFILE '/tmp/synthetic.xml' INTO TABLE t"
            fired, passed = _fired(checker, sql)
            assert "R042" in fired, f"真实 LOAD 必须命中 R042: {sql[:40]!r}"
            assert passed is False
