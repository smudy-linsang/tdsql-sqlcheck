# -*- coding: utf-8 -*-
"""UAT-O-01-R2 回归测试：KFN 结构化强制失败关闭 + 特殊语句豁免边界

覆盖 O 第二轮 UAT 报告 §4.4 的全部要求：
1. known_fidelity_failures 非空 → 无条件 E999 + passed=False（不依赖消息 marker）
2. 全业务规则关闭（rule_overrides 全 disabled）时 KFN 仍强制失败
3. ParseError 提前 return 路径的 KFN 归一化（消息含 marker + 原始异常）
4. 字符串诱饵不启动豁免：COMMENT='LOAD DATA' / SELECT 'CREATE VIEW' 等
5. 真实特殊语句豁免保留：真 CREATE VIEW/PROCEDURE/FUNCTION/TRIGGER/LOAD
6. 不扩大拒绝域：真对象、marker 字面量存储程序不受影响
"""
import pytest

from backend.engine.checker import RuleChecker


@pytest.fixture
def checker():
    return RuleChecker()


def _fired(checker, sql, instance_type="distributed", rule_overrides=None):
    r = checker.audit_sql(sql, instance_type=instance_type, rule_overrides=rule_overrides)
    return {v.rule_id for v in r.violations}, r.passed


# ── 1. KFN 强制失败关闭：结构化信号驱动，不信豁免 ─────────────

class TestKfnFailClosed:
    """已证明的保真失败必须无条件产出 E999，任何豁免都不得生效"""

    # O 第二轮残留的核心样例：KFN-5 + COMMENT='LOAD DATA'（字符串诱饵）
    KFN_LOAD_DATA = (
        "CREATE TABLE t_guard (id BIGINT NOT NULL COMMENT 'id',"
        "sk BIGINT NOT NULL COMMENT 'sk',u INT NOT NULL COMMENT 'u',"
        "PRIMARY KEY(id,sk),s INT SERIAL DEFAULT VALUE) "
        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='LOAD DATA' shardkey=sk"
    )
    # KFN-5 + COMMENT='LOAD XML'
    KFN_LOAD_XML = KFN_LOAD_DATA.replace("LOAD DATA", "LOAD XML")
    # KFN-5 + COMMENT='plain'（对照）
    KFN_PLAIN = KFN_LOAD_DATA.replace("'LOAD DATA'", "'plain'")
    # KFN-1 CONSTRAINT UNIQUE + COMMENT='CREATE VIEW'
    KFN_CONSTRAINT = (
        "CREATE TABLE t_guard2 (id BIGINT NOT NULL COMMENT 'id',"
        "sk BIGINT NOT NULL COMMENT 'sk',u INT NOT NULL COMMENT 'u',"
        "create_time DATETIME COMMENT 'c',update_time DATETIME COMMENT 'd',"
        "is_deleted INT COMMENT 'e',PRIMARY KEY(id,sk),"
        "CONSTRAINT uk_u UNIQUE(u)) "
        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='CREATE VIEW' shardkey=sk"
    )
    # KFN-3 SERIAL + COMMENT='CREATE PROCEDURE'
    KFN_SERIAL = (
        "CREATE TABLE t_guard3 (id BIGINT NOT NULL COMMENT 'id',"
        "sk BIGINT NOT NULL COMMENT 'sk',u INT NOT NULL COMMENT 'u',"
        "create_time DATETIME COMMENT 'c',update_time DATETIME COMMENT 'd',"
        "is_deleted INT COMMENT 'e',PRIMARY KEY(id,sk),s SERIAL) "
        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='CREATE PROCEDURE' shardkey=sk"
    )

    @pytest.mark.parametrize("sql,label", [
        (KFN_LOAD_DATA, "KFN5+LOAD DATA诱饵"),
        (KFN_LOAD_XML, "KFN5+LOAD XML诱饵"),
        (KFN_PLAIN, "KFN5+plain对照"),
        (KFN_CONSTRAINT, "KFN1+CREATE VIEW诱饵"),
        (KFN_SERIAL, "KFN3+CREATE PROCEDURE诱饵"),
    ])
    def test_kfn_always_e999(self, checker, sql, label):
        fired, passed = _fired(checker, sql)
        assert "E999_SYNTAX_ERROR" in fired, f"{label}: KFN 必须产出 E999"
        assert passed is False, f"{label}: KFN 必须 passed=False"

    @pytest.mark.parametrize("sql,label", [
        (KFN_LOAD_DATA, "KFN5+LOAD DATA诱饵"),
        (KFN_CONSTRAINT, "KFN1+CREATE VIEW诱饵"),
    ])
    def test_kfn_fail_closed_with_all_business_rules_disabled(self, checker, sql, label):
        """全业务规则关闭时 KFN 仍强制失败（强制门禁独立于业务规则集）"""
        disabled = {r.rule_id: {"enabled": False} for r in checker.rules}
        fired, passed = _fired(checker, sql, rule_overrides=disabled)
        assert "E999_SYNTAX_ERROR" in fired, f"{label}: 规则全关时 E999 仍必须产出"
        assert passed is False, f"{label}: 规则全关时仍必须 passed=False"

    def test_kfn_structural_signal_drives_decision(self, checker):
        """结构化信号为真值源：known_fidelity_failures 非空即判 KFN，
        且 parse_error 归一化后同时携带 marker 与原始异常文本"""
        parsed = checker.parser.parse(self.KFN_LOAD_DATA)
        assert parsed.known_fidelity_failures, "preflight 必须写入结构化信号"
        assert parsed.parse_error is not None
        assert "KNOWN_FIDELITY_GAP[" in parsed.parse_error, "消息须归一化携带 marker"
        assert "Expecting" in parsed.parse_error, "消息须保留原始异常文本"

    def test_kfn_no_load_data_false_positive(self, checker):
        """COMMENT='LOAD DATA' 不得再置 has_load_data，R042 不得误报"""
        parsed = checker.parser.parse(self.KFN_LOAD_DATA)
        assert parsed.has_load_data is False
        fired, _ = _fired(checker, self.KFN_LOAD_DATA)
        assert "R042" not in fired, "字符串诱饵不得触发禁 LOAD DATA 规则"


# ── 2. 字符串诱饵不启动豁免（普通解析错误必须报 E999）────────

class TestLiteralBaitNoExemption:
    """SQL 注释、COMMENT/DEFAULT 字符串、反引号标识符不能启动特殊语句豁免"""

    @pytest.mark.parametrize("sql", [
        "SELECT 'CREATE VIEW' FROM",                       # 语法错误 + VIEW 诱饵
        "SELECT 'CREATE PROCEDURE' FROM",
        "SELECT 'CREATE FUNCTION' FROM",
        "SELECT 'CREATE TRIGGER' FROM",
        "SELECT 'LOAD DATA' FROM",
        "SELECT 'LOAD XML' FROM",
        "UPDATE t SET name='CREATE VIEW' WHERE",           # 截断 UPDATE + 诱饵
        "UPDATE t SET name='LOAD DATA' WHERE",
        "CREATE TABLE t (id INT, u INT COMMENT 'LOAD DATA'",  # 未闭合 + 诱饵
    ])
    def test_ordinary_error_with_bait_still_e999(self, checker, sql):
        fired, passed = _fired(checker, sql)
        assert "E999_SYNTAX_ERROR" in fired, f"语法错误不得被字符串诱饵豁免: {sql[:50]}"
        assert passed is False

    def test_comment_bait_no_exemption(self, checker):
        """块注释里的 CREATE VIEW 不得豁免"""
        sql = ("CREATE TABLE t_guard (id BIGINT NOT NULL COMMENT 'id',"
               "sk BIGINT NOT NULL COMMENT 'sk', PRIMARY KEY(id,sk)) "
               "ENGINE=InnoDB COMMENT='plain' shardkey=sk /* CREATE VIEW */")
        # 该语句结构完整（无 KFN）但…用一个真语法错误+注释诱饵
        sql_bad = "SELECT * FROM /* CREATE VIEW */"
        fired, _ = _fired(checker, sql_bad)
        assert "E999_SYNTAX_ERROR" in fired


# ── 3. 真实特殊语句豁免保留（不扩大拒绝域）────────────────────

class TestRealSpecialStatementsStillExempt:
    """真实 VIEW/PROCEDURE/FUNCTION/TRIGGER/LOAD 语句不得因修复被误伤"""

    @pytest.mark.parametrize("sql", [
        "CREATE VIEW v AS SELECT 1 AS id",
        "CREATE OR REPLACE VIEW v AS SELECT 1 AS id",
        "CREATE PROCEDURE p() BEGIN SELECT 1; END",
        "CREATE PROCEDURE p() BEGIN DECLARE x INT DEFAULT 0; SET x=1; SELECT x; END",
        "CREATE FUNCTION f() RETURNS INT DETERMINISTIC BEGIN DECLARE x INT DEFAULT 1; RETURN x; END",
        "CREATE TRIGGER tr BEFORE INSERT ON t FOR EACH ROW SET NEW.id = 1",
        "CREATE DEFINER='synthetic'@'localhost' PROCEDURE p() BEGIN SELECT 1; END",
        "LOAD DATA INFILE '/tmp/synthetic.csv' INTO TABLE t FIELDS TERMINATED BY ','",
        "LOAD XML INFILE '/tmp/synthetic.xml' INTO TABLE t",
    ])
    def test_real_special_statement_no_e999(self, checker, sql):
        fired, _ = _fired(checker, sql)
        assert "E999_SYNTAX_ERROR" not in fired, f"真实特殊语句不得报 E999: {sql[:60]}"

    @pytest.mark.parametrize("marker", ["plain", "KNOWN_FIDELITY_GAP", "UNIQUE_SEMANTICS_INCOMPLETE"])
    def test_marker_literal_in_procedure_no_e999(self, checker, marker):
        """marker 字面量出现在存储程序字符串里不得触发 KFN 误判"""
        sql = (f"CREATE PROCEDURE p() BEGIN DECLARE x VARCHAR(80) DEFAULT '{marker}'; "
               "WHILE x <> '' DO SET x=''; END WHILE; END")
        parsed = checker.parser.parse(sql)
        assert not parsed.known_fidelity_failures, "字符串字面量不得产生 KFN"
        fired, _ = _fired(checker, sql)
        assert "E999_SYNTAX_ERROR" not in fired


# ── 4. 首轮 UAT-O-01 场景保持（注释诱饵 + KFN）────────────────

class TestRound1ScenariosPreserved:
    def test_kfn_with_view_comment_blocked(self, checker):
        """首轮核心样例：KFN + /* CREATE VIEW */ 注释 → E999"""
        sql = ("CREATE TABLE t_guard (id BIGINT NOT NULL COMMENT 'id',"
               "sk BIGINT NOT NULL COMMENT 'sk',u INT NOT NULL COMMENT 'u',"
               "create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'c',"
               "update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'd',"
               "is_deleted TINYINT NOT NULL DEFAULT 0 COMMENT 'e',"
               "PRIMARY KEY(id,sk),CONSTRAINT uk_u UNIQUE(u)) "
               "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='plain' shardkey=sk /* CREATE VIEW */")
        fired, passed = _fired(checker, sql)
        assert "E999_SYNTAX_ERROR" in fired
        assert passed is False

    def test_real_view_no_e999(self, checker):
        fired, _ = _fired(checker, "CREATE VIEW v_test AS SELECT id FROM t1")
        assert "E999_SYNTAX_ERROR" not in fired
