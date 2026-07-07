# -*- coding: utf-8 -*-
"""Oracle迁移兼容规则(R078-R119) 单元测试
设计说明书第9.1节：每条规则至少1正1反用例 + 防误报专项
"""
import sys
import pytest

sys.path.insert(0, ".")
from backend.engine.checker import RuleChecker


@pytest.fixture(scope="module")
def checker():
    return RuleChecker()


def _hit(checker, sql, rule_id):
    """检查SQL是否命中指定规则"""
    result = checker.audit_sql(sql)
    return any(v.rule_id == rule_id for v in result.violations)


# ═══════════════════════════════════════════════════════════════════
# 基础验证
# ═══════════════════════════════════════════════════════════════════

class TestOracleCompatBasic:
    """基础验证：规则数、ID连续性、元数据"""

    def test_total_rules_119(self, checker):
        info = checker.get_rules_info()
        assert len(info) == 119

    def test_oracle_compat_count_42(self, checker):
        info = checker.get_rules_info()
        oc = [r for r in info if r["category"] == "oracle_compat"]
        assert len(oc) == 42

    def test_r078_to_r119_continuous(self, checker):
        info = checker.get_rules_info()
        oc_ids = sorted(r["rule_id"] for r in info if r["category"] == "oracle_compat")
        expected = [f"R{i:03d}" for i in range(78, 120)]
        assert oc_ids == expected

    def test_all_have_spec_source(self, checker):
        info = checker.get_rules_info()
        for r in info:
            if r["category"] == "oracle_compat":
                assert "ORACLE迁移TDSQL改造适配方案" in r.get("spec_source", ""), f"{r['rule_id']} spec_source格式不符"

    def test_severity_matches_design(self, checker):
        """设计4.4: ERROR 33条, WARNING 8条, INFO 1条"""
        info = {r["rule_id"]: r for r in checker.get_rules_info()}
        WARNING = {"R084", "R093", "R102", "R107", "R109", "R114", "R115", "R119"}
        INFO = {"R113"}
        for i in range(78, 120):
            rid = f"R{i:03d}"
            if rid in WARNING:
                assert info[rid]["severity"] == "WARNING", f"{rid} 应为WARNING"
            elif rid in INFO:
                assert info[rid]["severity"] == "INFO", f"{rid} 应为INFO"
            else:
                assert info[rid]["severity"] == "ERROR", f"{rid} 应为ERROR"


# ═══════════════════════════════════════════════════════════════════
# 42条规则正例命中（设计第5章 ✗ 示例）
# ═══════════════════════════════════════════════════════════════════

class TestOracleCompatHit:
    """正例：设计✗示例必须命中对应规则"""

    CASES = [
        ("R078", "CREATE TABLE t (id NUMBER(10), name VARCHAR2(100))"),
        ("R079", "SELECT * FROM t WHERE rownum < 4"),
        ("R080", "select nvl(max(tempa),0) from t"),
        ("R081", "SELECT decode(substr(c,2,1),'A','1','B','2') FROM t"),
        ("R082", "SELECT to_char(amt,'FM9999999999.09999999') FROM t"),
        ("R083", "SELECT to_number(c) FROM t"),
        ("R084", "SELECT '%' || c || '%' FROM t"),
        ("R085", "SELECT to_date(dt,'YYYYMMDD') FROM t"),
        ("R086", "SELECT trunc(a/b,2) AS x FROM dual"),
        ("R087", "SELECT ltrim(code,'0') FROM t"),
        ("R088", "SELECT add_months(str_to_date(dt,'%Y%m%d'),-1) FROM t"),
        ("R089", "SELECT substr(c,'0','9') FROM t"),
        ("R090", "SELECT TRUNC(sysdate) FROM dual"),
        ("R091", "MERGE INTO t USING s ON (t.id=s.id) WHEN MATCHED THEN UPDATE SET t.v=s.v"),
        ("R092", "WITH a AS (SELECT id,name FROM t1 WHERE id>3) SELECT * FROM a JOIN t2 b ON a.name=b.name"),
        ("R093", "select length(name) from t"),
        ("R094", "select listagg(c,',') within group(order by c) from t"),
        ("R095", "select * from t_eg_01 minus select * from t_eg_02"),
        ("R096", "select * from a full outer join b on a.id=b.id"),
        ("R097", "create table t (data_dt char(8) default date_format(current_timestamp,'%Y%m%d'))"),
        ("R098", "create table t (c varchar(20)) partition by hash(c) partitions 4"),
        ("R099", "select * from (select * from t1)"),
        ("R100", "delete from t1 a where exists (select 1 from t2 b where a.id=b.id)"),
        ("R101", "select condition from t_rule where id=1"),
        ("R102", "select * from t where c like '%\\_%' escape '\\\\'"),
        ("R103", "select * from t where a < = 10"),
        ("R104", "select sum (amt) from t"),
        ("R105", "select f from tabx, tabm where tabx.k = tabm.k(+)"),
        ("R106", "select * from dept start with pid=0 connect by prior id=pid"),
        ("R107", "insert into ta(a,b) select a,b from tb"),
        ("R108", "insert into ta(a,b,c) select a,b,seq_x.nextval from tb"),
        ("R110", "select userenv('SID') from dual"),
        ("R111", "select row_number() over(partition by uid order by ts) rn from t"),
        ("R112", "declare c1 cursor for select * from t"),
        ("R113", "alter table t drop partition p20240101"),
        ("R114", "select * from t order by id limit 100000,20"),
        ("R115", "create table t (id varchar(300) primary key) default charset=utf8mb4"),
        ("R116", "create table t (a int not null, b int) shardkey=a,b"),
        ("R117", "create table t (dt datetime not null, v int) shardkey=dt"),
        ("R118", "create table t (uid bigint, v int) shardkey=uid"),
        ("R119", "select date_format(sysdate()-15,'%Y%m%d') as d"),
    ]

    @pytest.mark.parametrize("rule_id,sql", CASES)
    def test_hit(self, checker, rule_id, sql):
        assert _hit(checker, sql, rule_id), f"{rule_id} 未命中: {sql[:80]}"


# ═══════════════════════════════════════════════════════════════════
# 42条规则反例通过（设计第5章 ✓ 示例）
# ═══════════════════════════════════════════════════════════════════

class TestOracleCompatPass:
    """反例：设计✓示例不应命中对应规则"""

    CASES = [
        ("R078", "CREATE TABLE t (id BIGINT, name VARCHAR(100))"),
        ("R079", "SELECT * FROM t LIMIT 3"),
        ("R080", "select ifnull(max(tempa),0) from t"),
        ("R081", "SELECT CASE substr(c,2,1) WHEN 'A' THEN '1' ELSE '0' END FROM t"),
        ("R082", "SELECT FORMAT(amt,2) FROM t"),
        ("R083", "SELECT cast(c as decimal(10,2)) FROM t"),
        ("R084", "SELECT CONCAT('%',c,'%') FROM t"),
        ("R085", "SELECT str_to_date(dt,'%Y%m%d') FROM t"),
        ("R086", "SELECT truncate(a/b,2) AS x FROM dual"),
        ("R087", "SELECT TRIM(LEADING '0' FROM code) FROM t"),
        ("R088", "SELECT adddate(str_to_date(dt,'%Y%m%d'), INTERVAL -1 MONTH) FROM t"),
        ("R089", "SELECT substr(c,1,9) FROM t"),
        ("R090", "SELECT sysdate() FROM dual"),
        ("R091", "INSERT INTO t(id,v) VALUES(1,2) ON DUPLICATE KEY UPDATE v=2"),
        ("R092", "SELECT * FROM (SELECT id,name FROM t1 WHERE id>3) a JOIN t2 b ON a.name=b.name"),
        ("R093", "select char_length(name) from t"),
        ("R094", "select group_concat(c order by c separator ',') from t"),
        ("R095", "select a.* from t_eg_01 a left join t_eg_02 b on a.id=b.id where b.id is null"),
        ("R096", "select * from a left join b on a.id=b.id union select * from a right join b on a.id=b.id"),
        ("R097", "create table t (data_dt char(8) not null, ts datetime default current_timestamp)"),
        ("R098", "create table t (id bigint) partition by hash(id) partitions 4"),
        ("R099", "select * from (select * from t1) b"),
        ("R100", "delete from t1 where exists (select 1 from t2 where t1.id=t2.id)"),
        ("R101", "select `condition` from t_rule"),
        ("R102", "select * from t where c like '%/_%' escape '/'"),
        ("R103", "select * from t where a <= 10"),
        ("R104", "select sum(amt), count(1) from t"),
        ("R105", "select f from tabx left join tabm on tabx.k=tabm.k"),
        ("R106", "select id from dept where pid=0"),
        ("R107", "insert into ta(a,b) values(1,2)"),
        ("R108", "select seq_x.nextval from dual"),
        ("R110", "select id from t"),
        ("R111", "select id from t order by ts"),
        ("R112", "select * from t where id>? order by id limit 100"),
        ("R114", "select * from t where id>? order by id limit 20"),
        ("R115", "create table t (id varchar(64) primary key)"),
        ("R116", "create table t (a int not null, b int) shardkey=a"),
        ("R117", "create table t (id bigint not null) shardkey=id"),
        ("R118", "create table t (uid bigint not null, v int) shardkey=uid"),
        ("R119", "select date_format(date_add(sysdate(), interval -15 day),'%Y%m%d') as d"),
    ]

    @pytest.mark.parametrize("rule_id,sql", CASES)
    def test_no_hit(self, checker, rule_id, sql):
        assert not _hit(checker, sql, rule_id), f"{rule_id} 误命中: {sql[:80]}"


# ═══════════════════════════════════════════════════════════════════
# 防误报专项（设计第9.1节）
# ═══════════════════════════════════════════════════════════════════

class TestFalsePositives:
    """防误报：字符串字面量/注释内关键字不应命中"""

    FP_CASES = [
        ("字面量内nvl", "SELECT * FROM t WHERE remark = 'use nvl(a,b) here'", "R080"),
        ("行注释内to_char", "SELECT a FROM t -- to_char(x)", "R082"),
        ("块注释内rownum", "SELECT a /* rownum */ FROM t", "R079"),
        ("TRUNCATE不误中TRUNC", "SELECT truncate(a,2) FROM t", "R086"),
        ("char_length不误中length", "SELECT char_length(c) FROM t", "R093"),
        ("IN(不误中函数空格", "SELECT * FROM t WHERE a IN (1,2)", "R104"),
        ("VALUES不误中函数空格", "INSERT INTO t(a) VALUES (1)", "R104"),
        ("EXISTS不误中函数空格", "SELECT 1 FROM t WHERE EXISTS (SELECT 1 FROM s)", "R104"),
        ("无SELECT的INSERT", "INSERT INTO t(a,b) VALUES(1,2)", "R107"),
        ("dual单行取号", "SELECT seq.nextval FROM dual", "R108"),
        ("普通OR不误中R084", "SELECT * FROM t WHERE a=1 OR b=2", "R084"),
        ("普通limit小偏移", "SELECT * FROM t LIMIT 0,20", "R114"),
    ]

    @pytest.mark.parametrize("desc,sql,rule_id", FP_CASES)
    def test_no_false_positive(self, checker, desc, sql, rule_id):
        assert not _hit(checker, sql, rule_id), f"FP误报 [{desc}] {rule_id}: {sql[:60]}"


# ═══════════════════════════════════════════════════════════════════
# E1/E2 增强验证
# ═══════════════════════════════════════════════════════════════════

class TestEnhancements:
    """E1 GTT识别 + E2 唯一索引"""

    def test_e1_gtt_hits_r024(self, checker):
        sql = "CREATE GLOBAL TEMPORARY TABLE tmp_x (id INT) ON COMMIT DELETE ROWS"
        assert _hit(checker, sql, "R024"), "E1: GTT建表未命中R024"

    def test_e2_unique_key_no_shardkey(self, checker):
        sql = "create table t (uid bigint not null, c varchar(20) not null, primary key (uid), unique key uk_c (c)) shardkey=uid"
        assert _hit(checker, sql, "R054"), "E2: 唯一索引不含分片键未命中R054"

    def test_e2_unique_key_with_shardkey_no_hit(self, checker):
        sql = "create table t (uid bigint not null, c varchar(20) not null, primary key (uid), unique key uk_c (c, uid)) shardkey=uid"
        assert not _hit(checker, sql, "R054"), "E2: 唯一索引已含分片键仍命中R054(误报)"


# ═══════════════════════════════════════════════════════════════════
# 缺陷修复验证
# ═══════════════════════════════════════════════════════════════════

class TestDefectFixes:
    """验收报告缺陷修复验证"""

    def test_defect1_bigint_unsigned_no_r117(self, checker):
        """BIGINT UNSIGNED分片键不应误报R117"""
        sql = "CREATE TABLE t_order (id BIGINT UNSIGNED NOT NULL, v INT, PRIMARY KEY (id)) SHARDKEY=id"
        assert not _hit(checker, sql, "R117"), "BIGINT UNSIGNED shardkey误报R117"

    def test_defect2_r054_triggers_without_metadata(self, checker):
        """R054无元数据时也应通过raw_sql触发"""
        sql = "create table t (uid bigint not null, c varchar(20) not null, primary key (uid), unique key uk_c (c)) shardkey=uid"
        assert _hit(checker, sql, "R054"), "R054无元数据时应通过raw_sql回退触发"
