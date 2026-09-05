# -*- coding: utf-8 -*-
# R5-01 (UAT round-6 / O section 8.4): routine construct-stack splitter + routine syntax
# compat layer + three-entry consistency. GATE-2 acceptance regression locks.
import pytest

from backend.engine.checker import RuleChecker
from backend.engine.parser import split_sql_statements_for_audit


@pytest.fixture()
def checker():
    return RuleChecker()


def _ids(r):
    return {v.rule_id for v in r.violations}


P = "CREATE PROCEDURE p() BEGIN %s END;"


# splitter: construct stack keeps one routine as a single unit (O section 5.1)
@pytest.mark.parametrize("body", [
    "SET @a=1; SET @b=2;",
    "SET @a=1; BEGIN SET @x=1; SET @y=2; END; SET @b=2;",
    "IF @x>0 THEN SET @a=1; ELSE SET @a=2; END IF; SET @b=2;",
    "IF @x>0 THEN IF @y>0 THEN SET @a=1; END IF; END IF; SET @b=2;",
    "CASE @x WHEN 1 THEN SET @a=1; ELSE SET @a=2; END CASE; SET @b=2;",
    "SET @x = CASE WHEN @a>1 THEN 1 ELSE 0 END; SET @b=2;",
    "WHILE @x<10 DO SET @x=@x+1; END WHILE; SET @b=2;",
    "myloop: LOOP SET @x=@x+1; IF @x>5 THEN LEAVE myloop; END IF; END LOOP; SET @b=2;",
    "REPEAT SET @x=@x+1; UNTIL @x>10 END REPEAT; SET @b=2;",
    "DECLARE cur CURSOR FOR SELECT id FROM t; OPEN cur; CLOSE cur;",
    "lbl: BEGIN SET @a=1; END lbl;",
])
def test_routine_body_kept_as_one_statement(body):
    segs = split_sql_statements_for_audit(P % body)
    assert len(segs) == 1, segs


def test_split_normal_multi_and_transaction_unchanged():
    assert split_sql_statements_for_audit("SELECT 1; SELECT 2;") == ["SELECT 1", "SELECT 2"]
    assert split_sql_statements_for_audit("BEGIN; SELECT 1; COMMIT;") == ["BEGIN", "SELECT 1", "COMMIT"]


def test_split_routine_then_table_two_units():
    segs = split_sql_statements_for_audit("CREATE PROCEDURE p() BEGIN SET @a=1; END; CREATE TABLE t(id INT);")
    assert len(segs) == 2, segs


def test_split_delimiter_dollar_keeps_routine(checker):
    sql = "DELIMITER $$\nCREATE PROCEDURE p() BEGIN SET @a=1; SET @b=2; END$$\nDELIMITER ;"
    assert len(checker._split_sql_file(sql)) == 1


# compat layer: official param modes + control flow, centralized clean
@pytest.mark.parametrize("sql,kind", [
    ("CREATE PROCEDURE p(x INT) SELECT x", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p(IN x INT) SELECT x", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p(OUT x INT) SELECT 1 INTO x", "CREATE PROCEDURE"),
    ("CREATE PROCEDURE p(INOUT x INT, IN y INT) SET x=x+y", "CREATE PROCEDURE"),
    ("CREATE FUNCTION f(x INT) RETURNS INT RETURN x", "CREATE FUNCTION"),
    ("CREATE FUNCTION f(x INT) RETURNS INT DETERMINISTIC RETURN x", "CREATE FUNCTION"),
    ("CREATE PROCEDURE p() BEGIN IF @x>0 THEN SET @a=1; END IF; SET @b=2; END", "CREATE PROCEDURE"),
])
def test_routine_compat_centralized_clean(checker, sql, kind):
    r = checker.audit_sql(sql, instance_type="centralized")
    assert r.sql_type == kind, (r.sql_type, [v.rule_id for v in r.violations])
    assert r.violations == [], [v.rule_id for v in r.violations]


@pytest.mark.parametrize("sql,expect", [
    ("CREATE PROCEDURE p(IN x INT) SELECT x", {"R030"}),
    ("CREATE PROCEDURE p(IN x INT, OUT y INT) BEGIN SET y=x; END", {"R030"}),
    ("CREATE FUNCTION f(x INT) RETURNS INT RETURN x", {"R030", "R031"}),
])
def test_routine_compat_distributed_governance(checker, sql, expect):
    ids = _ids(checker.audit_sql(sql, instance_type="distributed"))
    assert expect <= ids, sorted(ids)
    assert not ({"E999_SYNTAX_ERROR", "R003", "R004", "R005", "R028"} & ids), sorted(ids)


# fail-closed negatives must still produce E999 (O section 8.4)
@pytest.mark.parametrize("sql", [
    "CREATE PROCEDURE p(IN x INT SELECT x",
    "CREATE FUNCTION f(x INT) RETURN x",
    "CREATE FUNCTION f(x INT) RETURNS INT",
    "CREATE PROCEDURE p() BEGIN SET @a=1;",
    "CREATE PROCEDURE p() BEGIN IF @x THEN SET @a=1; END;",
    "CREATE PROCEDURE p() BEGIN SET @a=1; END IF; END;",
    "CREATE TRIGGER tr BEFORE INSERT ON",
])
def test_routine_negative_fails_closed(checker, sql):
    assert "E999_SYNTAX_ERROR" in _ids(checker.audit_sql(sql, instance_type="centralized"))


# three entries stay consistent on a multi-line routine (O section 8.4)
ROUTINE_MULTI = ("CREATE PROCEDURE p_x(IN pid INT) BEGIN "
                 "IF pid>0 THEN SET @a=pid; END IF; "
                 "UPDATE t SET n=pid WHERE id=pid; END")


def test_three_entries_keep_routine_one_unit(checker):
    assert len(split_sql_statements_for_audit(ROUTINE_MULTI)) == 1
    assert len(checker._split_sql_file(ROUTINE_MULTI)) == 1
    res = checker.audit_file(ROUTINE_MULTI, file_path="t.sql")
    assert len(res) == 1, [r.sql_type for r in res]
    assert res[0].sql_type == "CREATE PROCEDURE"

