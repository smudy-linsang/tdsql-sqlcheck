# -*- coding: utf-8 -*-
"""UAT-O-1632-R7 整改回归锁（第七轮门禁，O §7.3）。

R7-01：例程兼容层收紧为失败关闭校验器——对称正反向语法矩阵（每条正例配负例）。
R7-02：唯一 DELIMITER-aware 拆分器 split_audit_script 的合同测试（$$、//、双字符），
       断言结果文本无 DELIMITER 指令/尾分隔符、一例程恰一结果、类型准确、集中式零违规、
       例程后跟 SELECT/CREATE TABLE 时数量与行号准确（不得只断言 len==1）。

所有负例统一断言：集中式含 E999_SYNTAX_ERROR 且 passed=False；
所有合法集中式正例断言：sql_type 准确且 violations==[]。
"""
import pytest

from backend.engine.checker import RuleChecker
from backend.engine.parser import split_audit_script


@pytest.fixture()
def checker():
    return RuleChecker()


def _ids(r):
    return {v.rule_id for v in r.violations}


# ── R7-01 正例：集中式必须通过、类型准确、零违规（O §7.3）──────────────────────
@pytest.mark.parametrize("sql,kind", [
    # 参数括号必选：无参空括号合法
    ("CREATE PROCEDURE p() SELECT 1", "CREATE PROCEDURE"),
    # PROCEDURE 省略模式 / IN / OUT / INOUT / 混合
    ("CREATE PROCEDURE p(x INT) SELECT x", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p(IN x INT) SELECT x", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p(OUT x INT) SELECT 1 INTO x", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p(INOUT x INT) SET x=x+1", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p(IN x INT, OUT y INT, INOUT z INT) SET y=x+z", "CREATE PROCEDURE"),
    # 复杂类型
    ("CREATE PROCEDURE p(x DECIMAL(10,2)) SELECT x", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p(x VARCHAR(255)) SELECT x", "CREATE PROCEDURE"),
    # FUNCTION：名 + 类型 + RETURNS
    ("CREATE FUNCTION f(x INT) RETURNS INT RETURN x", "CREATE FUNCTION"),
    ("CREATE FUNCTION f(x INT) RETURNS DECIMAL(10,2) RETURN x", "CREATE FUNCTION"),
    # DEFINER 全形态
    ("CREATE DEFINER='admin'@'localhost' PROCEDURE p(IN x INT) SELECT x", "CREATE PROCEDURE"),
    ("CREATE DEFINER=`admin`@`localhost` PROCEDURE p(IN x INT) SELECT x", "CREATE PROCEDURE"),
    ("CREATE DEFINER=admin@localhost PROCEDURE p(IN x INT) SELECT x", "CREATE PROCEDURE"),
    ("CREATE DEFINER=CURRENT_USER PROCEDURE p(IN x INT) SELECT x", "CREATE PROCEDURE"),
    ("CREATE DEFINER=CURRENT_USER() PROCEDURE p(IN x INT) SELECT x", "CREATE PROCEDURE"),
    # schema-qualified
    ("CREATE PROCEDURE db1.p(IN x INT) SELECT x", "CREATE PROCEDURE"),
    # characteristics 白名单
    ("CREATE PROCEDURE p(x INT) DETERMINISTIC CONTAINS SQL SELECT x", "CREATE PROCEDURE"),
    ("CREATE FUNCTION f(x INT) RETURNS INT NOT DETERMINISTIC READS SQL DATA RETURN x", "CREATE FUNCTION"),
    ("CREATE PROCEDURE p(x INT) COMMENT 'demo' SQL SECURITY INVOKER SELECT x", "CREATE PROCEDURE"),
    # 复合体控制流
    ("CREATE PROCEDURE p() BEGIN IF @x>0 THEN SET @a=1; END IF; END", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p() BEGIN IF @x>0 THEN SET @a=1; ELSEIF @x<0 THEN SET @a=2; ELSE SET @a=3; END IF; END", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p() BEGIN CASE @x WHEN 1 THEN SET @a=1; ELSE SET @a=2; END CASE; END", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p() BEGIN WHILE @x<10 DO SET @x=@x+1; END WHILE; END", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p() BEGIN myloop: LOOP SET @x=@x+1; IF @x>5 THEN LEAVE myloop; END IF; END LOOP; END", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p() BEGIN REPEAT SET @x=@x+1; UNTIL @x>10 END REPEAT; END", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p() BEGIN BEGIN SET @a=1; END; SET @b=2; END", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p() lbl: BEGIN SET @a=1; END lbl", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p() BEGIN DECLARE cur CURSOR FOR SELECT id FROM t; OPEN cur; CLOSE cur; END", "CREATE PROCEDURE"),
    # IF() 函数与 CASE 表达式位于同一 SET（不得误判未闭 IF）
    ("CREATE PROCEDURE p() BEGIN SET @a=IF(1=1, CASE WHEN 1 THEN 2 ELSE 3 END, 4); END", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p() BEGIN SET @x = CASE WHEN @a>1 THEN 1 ELSE 0 END; END", "CREATE PROCEDURE"),
])
def test_routine_legal_centralized_clean(checker, sql, kind):
    r = checker.audit_sql(sql, instance_type="centralized")
    assert r.sql_type == kind, (r.sql_type, sorted(_ids(r)))
    assert r.violations == [], sorted(_ids(r))
    assert r.passed is True


# ── R7-01 负例：集中式必须失败关闭（含 E999），绝不 passed=True（O §7.3）────────
@pytest.mark.parametrize("sql", [
    # 参数括号必选：缺括号非法
    "CREATE PROCEDURE p SELECT 1",
    "CREATE PROCEDURE p_bad SELECT 1",
    # 前置/尾随/连续逗号、缺逗号
    "CREATE PROCEDURE p(,x INT) BEGIN END",
    "CREATE PROCEDURE p(x INT,) BEGIN END",
    "CREATE PROCEDURE p(x INT,,y INT) BEGIN END",
    "CREATE PROCEDURE p(x INT y INT) BEGIN END",
    # 参数缺名/缺类型
    "CREATE PROCEDURE p(INT) BEGIN END",
    "CREATE PROCEDURE p(x) BEGIN END",
    # FUNCTION 禁止参数模式
    "CREATE FUNCTION f(IN x INT) RETURNS INT RETURN x",
    "CREATE FUNCTION f(OUT x INT) RETURNS INT RETURN x",
    "CREATE FUNCTION f(INOUT x INT) RETURNS INT RETURN x",
    # FUNCTION 缺 RETURNS
    "CREATE FUNCTION f(x INT) RETURN x",
    "CREATE FUNCTION f(x INT) RETURNS INT",
    # 参数括号不闭合
    "CREATE PROCEDURE p(IN x INT SELECT x",
    # body 非法：垃圾 token
    "CREATE PROCEDURE p() GARBAGE TOKEN",
    "CREATE FUNCTION f(x INT) RETURNS INT GARBAGE TOKEN",
    # body 未闭合 / END 类型错配
    "CREATE PROCEDURE p() BEGIN SET @a=1;",
    "CREATE PROCEDURE p() BEGIN IF @x THEN SET @a=1; END;",
    "CREATE PROCEDURE p() BEGIN SET @a=1; END IF; END;",
    "CREATE PROCEDURE p() BEGIN WHILE @x DO SET @a=1; END;",
    "CREATE PROCEDURE p() BEGIN CASE @x WHEN 1 THEN SET @a=1; END;",
])
def test_routine_illegal_fails_closed(checker, sql):
    r = checker.audit_sql(sql, instance_type="centralized")
    assert "E999_SYNTAX_ERROR" in _ids(r), sorted(_ids(r))
    assert r.passed is False


# ── R7-01 触发器：完整头正例 + 逐项负例（O §7.1.E）────────────────────────────
@pytest.mark.parametrize("sql", [
    "CREATE TRIGGER tr BEFORE INSERT ON t FOR EACH ROW SET NEW.id=1",
    "CREATE TRIGGER tr AFTER UPDATE ON t FOR EACH ROW BEGIN SET NEW.id=1; END",
    "CREATE TRIGGER tr BEFORE DELETE ON db1.t FOR EACH ROW SET @a=OLD.id",
])
def test_trigger_legal_no_e999(checker, sql):
    r = checker.audit_sql(sql, instance_type="centralized")
    assert "E999_SYNTAX_ERROR" not in _ids(r), sorted(_ids(r))
    assert r.sql_type == "CREATE TRIGGER", r.sql_type


@pytest.mark.parametrize("sql", [
    "CREATE TRIGGER tr GARBAGE TOKEN",
    "CREATE TRIGGER tr BEFORE INSERT ON t SET NEW.id=1",          # 缺 FOR EACH ROW
    "CREATE TRIGGER tr SOMETIME INSERT ON t FOR EACH ROW SET NEW.id=1",  # 非法时机
    "CREATE TRIGGER tr BEFORE UPSERT ON t FOR EACH ROW SET NEW.id=1",    # 非法事件
    "CREATE TRIGGER tr BEFORE INSERT ON",                          # 缺表/体
    "CREATE TRIGGER tr BEFORE INSERT ON t FOR EACH ROW GARBAGE",   # 垃圾体
])
def test_trigger_illegal_fails_closed(checker, sql):
    r = checker.audit_sql(sql, instance_type="centralized")
    assert "E999_SYNTAX_ERROR" in _ids(r), sorted(_ids(r))
    assert r.passed is False


# ── R7-01 元数据：schema-qualified 对象名准确（O §5.3）─────────────────────────
def test_schema_qualified_object_name_accurate(checker):
    parsed = checker.parser.parse("CREATE PROCEDURE db1.p(IN x INT) SELECT x")
    assert parsed.created_object_name == "db1.p", parsed.created_object_name
    assert parsed.created_object_kind == "PROCEDURE"


# ── R7-01 分布式治理不因收紧而误报 E999（O §4.2/§8）───────────────────────────
@pytest.mark.parametrize("sql,expect", [
    ("CREATE PROCEDURE p(IN x INT) SELECT x", {"R030"}),
    ("CREATE FUNCTION f(x INT) RETURNS INT RETURN x", {"R030", "R031"}),
])
def test_routine_distributed_governance_intact(checker, sql, expect):
    ids = _ids(checker.audit_sql(sql, instance_type="distributed"))
    assert expect <= ids, sorted(ids)
    assert not ({"E999_SYNTAX_ERROR", "R003", "R004", "R005", "R028"} & ids), sorted(ids)


# ── R7-02 DELIMITER 合同测试：$$、//、双字符（O §7.3）─────────────────────────
def _routine_script(delim):
    return (f"DELIMITER {delim}\n"
            f"CREATE PROCEDURE p_d(IN x INT)\n"
            f"BEGIN\n"
            f"  SET @a = x;\n"
            f"  SET @b = x;\n"
            f"END{delim}\n"
            f"DELIMITER ;\n")


@pytest.mark.parametrize("delim", ["$$", "//", "##"])
def test_split_audit_script_strips_delimiter(checker, delim):
    segs = split_audit_script(_routine_script(delim))
    assert len(segs) == 1, segs
    sql = segs[0][0]
    assert delim not in sql, sql                       # 尾分隔符已剥离
    assert "DELIMITER" not in sql.upper(), sql         # 指令不入结果
    assert sql.rstrip().endswith("END"), sql


@pytest.mark.parametrize("delim", ["$$", "//", "##"])
def test_delimiter_routine_one_result_all_entries(checker, delim):
    script = _routine_script(delim)
    # 拆分器
    segs = split_audit_script(script)
    assert len(segs) == 1
    # 文件入口（audit_file 走统一拆分器）
    res = checker.audit_file(script, file_path="t.sql", instance_type="centralized")
    assert len(res) == 1, [r.sql_type for r in res]
    assert res[0].sql_type == "CREATE PROCEDURE", res[0].sql_type
    assert res[0].violations == [], [v.rule_id for v in res[0].violations]
    assert res[0].passed is True


def test_delimiter_routine_then_statements_count_and_lines(checker):
    script = ("DELIMITER $$\n"                             # line 1
              "CREATE PROCEDURE p() BEGIN SET @a=1; END$$\n"  # line 2
              "DELIMITER ;\n"                              # line 3
              "SELECT 1;\n"                                # line 4
              "CREATE TABLE t(id INT);\n")                 # line 5
    segs = split_audit_script(script)
    assert len(segs) == 3, segs
    sqls = [s for s, _, _ in segs]
    lines = [ln for _, ln, _ in segs]
    assert sqls[0].startswith("CREATE PROCEDURE") and "END" in sqls[0]
    assert sqls[1] == "SELECT 1"
    assert sqls[2] == "CREATE TABLE t(id INT)"
    assert lines == [2, 4, 5], lines                       # 行号准确（DELIMITER 行不计入）
    # 文件入口三结果，类型准确
    res = checker.audit_file(script, file_path="t.sql", instance_type="centralized")
    assert [r.sql_type for r in res] == ["CREATE PROCEDURE", "SELECT", "CREATE TABLE"], \
        [r.sql_type for r in res]


def test_batch_stream_split_matches_file(checker):
    """batch-stream 与 /file 共用 split_audit_script：同一 DELIMITER 脚本结果一致。"""
    script = _routine_script("$$")
    stream_stmts = [s for s, _, _ in split_audit_script(script)]
    file_res = checker.audit_file(script, file_path="t.sql", instance_type="centralized")
    assert len(stream_stmts) == len(file_res) == 1
    assert stream_stmts[0].strip() == file_res[0].sql.strip()


def test_no_delimiter_multistatement_unchanged(checker):
    """无 DELIMITER 时不得回归：同行多语句、事务 BEGIN 仍按分号拆。"""
    assert [s for s, _, _ in split_audit_script("SELECT 1; SELECT 2;")] == ["SELECT 1", "SELECT 2"]
    assert [s for s, _, _ in split_audit_script("BEGIN; SELECT 1; COMMIT;")] == ["BEGIN", "SELECT 1", "COMMIT"]
