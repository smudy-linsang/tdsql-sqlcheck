# -*- coding: utf-8 -*-
"""验收脚本：设计说明书第5章42条规则 ✗命中/✓通过 逐条实测 + 防误报专项 + E1/E2增强"""
import sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "/home/user/tdsql-sqlcheck")
sys.path.insert(0, ".")
from backend.engine.checker import RuleChecker

c = RuleChecker()

# (规则ID, ✗必须命中的SQL, ✓必须不命中的SQL)
CASES = [
 ("R078", "CREATE TABLE t (id NUMBER(10), name VARCHAR2(100))", "CREATE TABLE t (id BIGINT, name VARCHAR(100))"),
 ("R079", "SELECT * FROM t WHERE rownum < 4", "SELECT * FROM t LIMIT 3"),
 ("R080", "select nvl(max(tempa),0) from t", "select ifnull(max(tempa),0) from t"),
 ("R081", "SELECT decode(substr(c,2,1),'A','1','B','2') FROM t", "SELECT CASE substr(c,2,1) WHEN 'A' THEN '1' ELSE '0' END FROM t"),
 ("R082", "SELECT to_char(amt,'FM9999999999.09999999') FROM t", "SELECT FORMAT(amt,2) FROM t"),
 ("R083", "SELECT to_number(c) FROM t", "SELECT cast(c as decimal(10,2)) FROM t"),
 ("R084", "SELECT '%' || c || '%' FROM t", "SELECT CONCAT('%',c,'%') FROM t"),
 ("R085", "SELECT to_date(dt,'YYYYMMDD') FROM t", "SELECT str_to_date(dt,'%Y%m%d') FROM t"),
 ("R086", "SELECT trunc(a/b,2) AS x FROM dual", "SELECT truncate(a/b,2) AS x FROM dual"),
 ("R087", "SELECT ltrim(code,'0') FROM t", "SELECT TRIM(LEADING '0' FROM code) FROM t"),
 ("R088", "SELECT add_months(str_to_date(dt,'%Y%m%d'),-1) FROM t", "SELECT adddate(str_to_date(dt,'%Y%m%d'), INTERVAL -1 MONTH) FROM t"),
 ("R089", "SELECT substr(c,'0','9') FROM t", "SELECT substr(c,1,9) FROM t"),
 ("R090", "SELECT TRUNC(sysdate) FROM dual", "SELECT sysdate() FROM dual"),
 ("R091", "MERGE INTO t USING s ON (t.id=s.id) WHEN MATCHED THEN UPDATE SET t.v=s.v", "INSERT INTO t(id,v) VALUES(1,2) ON DUPLICATE KEY UPDATE v=2"),
 ("R092", "WITH a AS (SELECT id,name FROM t1 WHERE id>3) SELECT * FROM a JOIN t2 b ON a.name=b.name", "SELECT * FROM (SELECT id,name FROM t1 WHERE id>3) a JOIN t2 b ON a.name=b.name"),
 ("R093", "select length(name) from t", "select char_length(name) from t"),
 ("R094", "select listagg(c,',') within group(order by c) from t", "select group_concat(c order by c separator ',') from t"),
 ("R095", "select * from t_eg_01 minus select * from t_eg_02", "select a.* from t_eg_01 a left join t_eg_02 b on a.id=b.id where b.id is null"),
 ("R096", "select * from a full outer join b on a.id=b.id", "select * from a left join b on a.id=b.id union select * from a right join b on a.id=b.id"),
 ("R097", "create table t (data_dt char(8) default date_format(current_timestamp,'%Y%m%d'))", "create table t (data_dt char(8) not null, ts datetime default current_timestamp)"),
 ("R098", "create table t (c varchar(20)) partition by hash(c) partitions 4", "create table t (id bigint) partition by hash(id) partitions 4"),
 ("R099", "select * from (select * from t1)", "select * from (select * from t1) b"),
 ("R100", "delete from t1 a where exists (select 1 from t2 b where a.id=b.id)", "delete from t1 where exists (select 1 from t2 where t1.id=t2.id)"),
 ("R101", "select condition from t_rule where id=1", "select `condition` from t_rule"),
 ("R102", "select * from t where c like '%\\_%' escape '\\\\'", "select * from t where c like '%/_%' escape '/'"),
 ("R103", "select * from t where a < = 10", "select * from t where a <= 10"),
 ("R104", "select sum (amt) from t", "select sum(amt), count(1) from t"),
 ("R104b", "select count （1） from t", None),  # 全角括号
 ("R105", "select f from tabx, tabm where tabx.k = tabm.k(+)", "select f from tabx left join tabm on tabx.k=tabm.k"),
 ("R106", "select * from dept start with pid=0 connect by prior id=pid", "select id from dept where pid=0"),
 ("R107", "insert into ta(a,b) select a,b from tb", "insert into ta(a,b) values(1,2)"),
 ("R108", "insert into ta(a,b,c) select a,b,seq_x.nextval from tb", "select seq_x.nextval from dual"),
 ("R109", "update t set stcd=case stcd when 1 then 2 else stcd end, tm=case stcd when 1 then sysdate() else tm end where stcd=1",
          "update t set tm=case stcd when 1 then sysdate() else tm end, stcd=case stcd when 1 then 2 else stcd end where stcd=1"),
 ("R110", "select userenv('SID') from dual", "select id from t"),
 ("R111", "select row_number() over(partition by uid order by ts) rn from t", "select id from t order by ts"),
 ("R112", "declare c1 cursor for select * from t", "select * from t where id>? order by id limit 100"),
 ("R113", "alter table t drop partition p20240101", "alter table t add column c int"),
 ("R114", "select * from t order by id limit 100000,20", "select * from t where id>? order by id limit 20"),
 ("R115", "create table t (id varchar(300) primary key) default charset=utf8mb4", "create table t (id varchar(64) primary key)"),
 ("R116", "create table t (a int not null, b int) shardkey=a,b", "create table t (a int not null, b int) shardkey=a"),
 ("R117", "create table t (dt datetime not null, v int) shardkey=dt", "create table t (id bigint not null) shardkey=id"),
 ("R118", "create table t (uid bigint, v int) shardkey=uid", "create table t (uid bigint not null, v int) shardkey=uid"),
 ("R119", "select date_format(sysdate()-15,'%Y%m%d') as d", "select date_format(date_add(sysdate(), interval -15 day),'%Y%m%d') as d"),
]

# 防误报专项: (说明, SQL, 不得命中的规则ID)
FP = [
 ("字面量内nvl", "SELECT * FROM t WHERE remark = 'use nvl(a,b) here'", "R080"),
 ("行注释内to_char", "SELECT a FROM t -- to_char(x)", "R082"),
 ("块注释内rownum", "SELECT a /* rownum */ FROM t", "R079"),
 ("TRUNCATE不误中TRUNC", "SELECT truncate(a,2) FROM t", "R086"),
 ("char_length不误中length", "SELECT char_length(c) FROM t", "R093"),
 ("IN(不误中函数空格", "SELECT * FROM t WHERE a IN (1,2)", "R104"),
 ("VALUES (不误中", "INSERT INTO t(a) VALUES (1)", "R104"),
 ("EXISTS (不误中", "SELECT 1 FROM t WHERE EXISTS (SELECT 1 FROM s)", "R104"),
 ("无SELECT的INSERT", "INSERT INTO t(a,b) VALUES(1,2)", "R107"),
 ("dual单行取号", "SELECT seq.nextval FROM dual", "R108"),
 ("普通OR不误中R084", "SELECT * FROM t WHERE a=1 OR b=2", "R084"),
 ("普通limit小偏移", "SELECT * FROM t LIMIT 0,20", "R114"),
]

fails = []
hits_detail = []
for row in CASES:
    rid, bad, good = row[0].rstrip("b"), row[1], row[2]
    tag = row[0]
    r = c.audit_sql(bad)
    got = {v.rule_id for v in r.violations}
    if rid not in got:
        fails.append(f"✗未命中 {tag}: {bad[:60]} → 实际命中{sorted(got)}")
    if good is not None:
        r2 = c.audit_sql(good)
        got2 = {v.rule_id for v in r2.violations}
        if rid in got2:
            fails.append(f"✓误命中 {tag}: {good[:60]}")

for name, sql, rid in FP:
    r = c.audit_sql(sql)
    got = {v.rule_id for v in r.violations}
    if rid in got:
        fails.append(f"FP误报 [{name}] {rid}: {sql[:60]}")

# E1: Oracle GTT → R024
r = c.audit_sql("CREATE GLOBAL TEMPORARY TABLE tmp_x (id INT) ON COMMIT DELETE ROWS")
if "R024" not in {v.rule_id for v in r.violations}:
    fails.append("E1失败: GTT建表未命中R024")

# E2: 唯一索引不含分片键 → R054
r = c.audit_sql("create table t (uid bigint not null, c varchar(20) not null, primary key (uid), unique key uk_c (c)) shardkey=uid")
if "R054" not in {v.rule_id for v in r.violations}:
    fails.append("E2失败: 唯一索引不含分片键未命中R054")
# E2反例: 唯一索引含分片键不误报
r = c.audit_sql("create table t (uid bigint not null, c varchar(20) not null, primary key (uid), unique key uk_c (c, uid)) shardkey=uid")
if "R054" in {v.rule_id for v in r.violations}:
    fails.append("E2误报: 唯一索引已含分片键仍命中R054")

# 元数据字段完整性抽查
info = {x["rule_id"]: x for x in c.get_rules_info()}
for rid in [f"R{i:03d}" for i in range(78, 120)]:
    x = info[rid]
    if not x["description"] or not x["spec_source"] or not x["fix_suggestion"]:
        fails.append(f"元数据缺失 {rid}: desc/spec/fix 有空")
    if "ORACLE迁移TDSQL改造适配方案" not in x["spec_source"]:
        fails.append(f"spec_source格式不符 {rid}: {x['spec_source'][:40]}")

# 级别核对（设计4.4）
SEV = {"WARNING": {"R084","R093","R102","R107","R109","R114","R115","R119"}, "INFO": {"R113"}}
for rid in [f"R{i:03d}" for i in range(78,120)]:
    expect = "ERROR"
    for s, ids in SEV.items():
        if rid in ids: expect = s
    if info[rid]["severity"] != expect:
        fails.append(f"级别不符 {rid}: 期望{expect} 实际{info[rid]['severity']}")

print(f"用例总数: {len(CASES)*2-1 + len(FP) + 3 + 42*2}")
if fails:
    print(f"\n[FAIL] {len(fails)} items failed:")
    for f in fails: print("  " + f)
else:
    print("\n[OK] All passed")
