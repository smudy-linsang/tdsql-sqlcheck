# -*- coding: utf-8 -*-
"""v1.6.3.2 规则调整与解析器新通道专项测试（DESIGN-v1.6.3.2 §10.1/§10.2）。

覆盖：
  · R011 收窄为仅 TEXT(INFO)，CREATE 与 ALTER ADD/MODIFY/CHANGE 均覆盖；
  · R120 五种受限 LOB(ERROR)，CREATE 与 ALTER 覆盖；TINYTEXT/TINYBLOB/JSON 不命中；
  · R030/R032 仅分布式（集中式跳过、规则集不能绕过适用域）；
  · R035 只比较规范化基础类型（括号参数不参与），批内跨表上下文可触发；
  · R058 上限 2000 且结构化判定（注释/字符串不放行、占位符/offset 不可证明）；
  · R121 二级分区 MAXVALUE（CREATE/ALTER_ADD/ALTER_REORGANIZE 三出口）；
  · 解析器 dml_limit / alter_column_types / secondary_partition 三通道结构断言；
  · 质量门禁 strict/normal 放宽与收紧矩阵（§10.2）。
"""
import pytest

from backend.engine.checker import RuleChecker
from backend.engine.parser import SQLParser


@pytest.fixture()
def parser():
    return SQLParser(dialect="mysql")


@pytest.fixture()
def checker():
    return RuleChecker()


def _ids(result):
    return {v.rule_id for v in result.violations}


def _sev(result, rule_id):
    for v in result.violations:
        if v.rule_id == rule_id:
            return v.severity.value if hasattr(v.severity, "value") else str(v.severity)
    return None


# ════════════════════════════════════════════════════════════
# R011 / R120
# ════════════════════════════════════════════════════════════

def test_r011_text_only_info(parser, checker):
    r = checker.audit_sql("CREATE TABLE t (id INT, body TEXT)", instance_type="distributed")
    assert "R011" in _ids(r)
    assert _sev(r, "R011") == "INFO"


def test_r011_alter_add_modify_change(parser, checker):
    for sql in (
        "ALTER TABLE t ADD COLUMN body TEXT",
        "ALTER TABLE t MODIFY COLUMN body TEXT",
        "ALTER TABLE t CHANGE COLUMN a body TEXT",
    ):
        r = checker.audit_sql(sql, instance_type="distributed")
        assert "R011" in _ids(r), sql
        assert _sev(r, "R011") == "INFO", sql


def test_r011_not_hit_other_types(parser, checker):
    for t in ("MEDIUMTEXT", "LONGTEXT", "TINYTEXT", "BLOB", "JSON", "VARCHAR(2000)"):
        r = checker.audit_sql(f"CREATE TABLE t (id INT, body {t})", instance_type="distributed")
        assert "R011" not in _ids(r), t


def test_r120_five_lob_types(parser, checker):
    for t in ("BLOB", "MEDIUMTEXT", "LONGBLOB", "MEDIUMBLOB", "LONGTEXT"):
        r = checker.audit_sql(f"CREATE TABLE t (id INT, body {t})", instance_type="distributed")
        assert "R120" in _ids(r), t
        assert _sev(r, "R120") == "ERROR", t


def test_r120_alter_coverage(parser, checker):
    for sql in (
        "ALTER TABLE t ADD COLUMN body MEDIUMTEXT",
        "ALTER TABLE t MODIFY COLUMN body LONGBLOB",
        "ALTER TABLE t CHANGE COLUMN a body LONGTEXT",
    ):
        r = checker.audit_sql(sql, instance_type="distributed")
        assert "R120" in _ids(r), sql


def test_r120_not_hit_tiny_or_json_or_columnname(parser, checker):
    for t in ("TINYTEXT", "TINYBLOB", "JSON"):
        r = checker.audit_sql(f"CREATE TABLE t (id INT, body {t})", instance_type="distributed")
        assert "R120" not in _ids(r), t
    # 列名含 blob / 字符串 'LONGTEXT' 不得误命中
    r = checker.audit_sql("CREATE TABLE t (id INT, blob_url VARCHAR(64), note VARCHAR(32) DEFAULT 'LONGTEXT')",
                          instance_type="distributed")
    assert "R120" not in _ids(r)


def test_r011_and_r120_coexist(parser, checker):
    r = checker.audit_sql("CREATE TABLE t (id INT, a TEXT, b MEDIUMTEXT)", instance_type="distributed")
    assert "R011" in _ids(r) and "R120" in _ids(r)
    assert _sev(r, "R011") == "INFO" and _sev(r, "R120") == "ERROR"


# ════════════════════════════════════════════════════════════
# R030 / R032 适用域
# ════════════════════════════════════════════════════════════

def test_r030_r032_distributed_only(parser, checker):
    view = "CREATE VIEW v AS SELECT 1"
    tmp = "CREATE TEMPORARY TABLE tt (id INT)"
    rd = checker.audit_sql(view, instance_type="distributed")
    td = checker.audit_sql(tmp, instance_type="distributed")
    rc = checker.audit_sql(view, instance_type="centralized")
    tc = checker.audit_sql(tmp, instance_type="centralized")
    assert "R030" in _ids(rd)
    assert "R032" in _ids(td)
    assert "R030" not in _ids(rc)      # 集中式跳过
    assert "R032" not in _ids(tc)


def test_ruleset_cannot_bypass_scope(parser, checker):
    # 规则集把 R030 设为启用，也不能在集中式上执行（INV-2 适用域只做减法）
    overrides = {"R030": {"enabled": True}}
    rc = checker.audit_sql("CREATE VIEW v AS SELECT 1", rule_overrides=overrides,
                           instance_type="centralized")
    assert "R030" not in _ids(rc)


# ════════════════════════════════════════════════════════════
# R035 跨表上下文
# ════════════════════════════════════════════════════════════

def test_r035_same_base_type_different_length_passes(checker):
    sql = ("CREATE TABLE a (uid VARCHAR(32));\n"
           "CREATE TABLE b (uid VARCHAR(128));")
    results = checker.audit_file(sql, instance_type="distributed")
    ids = set().union(*[_ids(r) for r in results]) if results else set()
    assert "R035" not in ids


def test_r035_different_base_type_errors(checker):
    sql = ("CREATE TABLE a (uid VARCHAR(32));\n"
           "CREATE TABLE b (uid BIGINT);")
    results = checker.audit_file(sql, instance_type="distributed")
    ids = set().union(*[_ids(r) for r in results]) if results else set()
    assert "R035" in ids


def test_r035_unsigned_matters(checker):
    sql = ("CREATE TABLE a (uid INT);\n"
           "CREATE TABLE b (uid INT UNSIGNED);")
    results = checker.audit_file(sql, instance_type="distributed")
    ids = set().union(*[_ids(r) for r in results]) if results else set()
    assert "R035" in ids


def test_r035_single_statement_skips(checker):
    r = checker.audit_sql("CREATE TABLE a (uid VARCHAR(32))", instance_type="distributed")
    assert "R035" not in _ids(r)


def test_r035_message_no_length_wording(checker):
    sql = ("CREATE TABLE a (uid VARCHAR(32));\n"
           "CREATE TABLE b (uid BIGINT);")
    results = checker.audit_file(sql, instance_type="distributed")
    for r in results:
        for v in r.violations:
            if v.rule_id == "R035":
                assert "长度必须一致" not in v.message
                assert "长度" not in (v.suggestion or "") or "可按各表实际容量" in (v.suggestion or "")


# ════════════════════════════════════════════════════════════
# R058 结构化 LIMIT
# ════════════════════════════════════════════════════════════

_SHARD_META = {"t": {"shard_key": "id", "is_shard_table": True}}


def test_r058_no_limit_warns(parser, checker):
    r = checker.audit_sql("UPDATE t SET a=1 WHERE b=2", table_metadata=_SHARD_META,
                          instance_type="distributed")
    assert "R058" in _ids(r)


@pytest.mark.parametrize("limit", ["0", "1", "2000"])
def test_r058_within_limit_passes(parser, checker, limit):
    r = checker.audit_sql(f"UPDATE t SET a=1 WHERE b=2 LIMIT {limit}",
                          table_metadata=_SHARD_META, instance_type="distributed")
    assert "R058" not in _ids(r), limit


@pytest.mark.parametrize("limit", ["2001", "999999"])
def test_r058_over_limit_warns(parser, checker, limit):
    r = checker.audit_sql(f"UPDATE t SET a=1 WHERE b=2 LIMIT {limit}",
                          table_metadata=_SHARD_META, instance_type="distributed")
    assert "R058" in _ids(r), limit


def test_r058_placeholder_unprovable(parser, checker):
    r = checker.audit_sql("UPDATE t SET a=1 WHERE b=? LIMIT ?",
                          table_metadata=_SHARD_META, instance_type="distributed")
    assert "R058" in _ids(r)
    assert "无法在审核阶段确定" in next(v.message for v in r.violations if v.rule_id == "R058")


def test_r058_two_param_offset_unprovable(parser, checker):
    r = checker.audit_sql("UPDATE t SET a=1 WHERE b=2 LIMIT 1, 2000",
                          table_metadata=_SHARD_META, instance_type="distributed")
    assert "R058" in _ids(r)
    assert "无法在审核阶段确定" in next(v.message for v in r.violations if v.rule_id == "R058")


def test_r058_comment_and_string_not_limit(parser, checker):
    for sql in ("UPDATE t SET a=1 WHERE b=2 /* limit 10 */",
                "UPDATE t SET a=1 WHERE b=2 AND note='limit 10'"):
        r = checker.audit_sql(sql, table_metadata=_SHARD_META, instance_type="distributed")
        assert "R058" in _ids(r), sql     # 注释/字符串不构成 LIMIT → 视为未设置


def test_r058_subquery_limit_not_outer(parser, checker):
    r = checker.audit_sql("UPDATE t SET a=1 WHERE b=(SELECT c FROM x LIMIT 5)",
                          table_metadata=_SHARD_META, instance_type="distributed")
    assert "R058" in _ids(r)     # 子查询 LIMIT 不算外层 → 视为未设置


# ════════════════════════════════════════════════════════════
# R121 二级分区 MAXVALUE
# ════════════════════════════════════════════════════════════

def test_r121_create_bare_maxvalue(parser, checker):
    sql = ("CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id) "
           "PARTITION BY RANGE(id) (PARTITION pmax VALUES LESS THAN MAXVALUE)")
    r = checker.audit_sql(sql, instance_type="distributed")
    assert "R121" in _ids(r)
    # GATE-3（林桑签署决议 §5）：bare MAXVALUE 已在 parse_one 前规整为等价括号形态、
    # 正常解析为 Create，绝不报 E999（推翻原 §4.7.5「bare 失败关闭 E999+R121」口径）。
    assert "E999_SYNTAX_ERROR" not in _ids(r)
    assert _sev(r, "R121") == "ERROR"


def test_r121_create_paren_maxvalue(parser, checker):
    sql = ("CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id) "
           "PARTITION BY RANGE(id) (PARTITION pmax VALUES LESS THAN (MAXVALUE))")
    r = checker.audit_sql(sql, instance_type="distributed")
    assert "R121" in _ids(r)
    assert "E999_SYNTAX_ERROR" not in _ids(r)


def test_r121_alter_add_and_reorganize(parser, checker):
    add = "ALTER TABLE t ADD PARTITION (PARTITION pmax VALUES LESS THAN MAXVALUE)"
    reorg = "ALTER TABLE t REORGANIZE PARTITION p0 INTO (PARTITION pmax VALUES LESS THAN (MAXVALUE))"
    ra = checker.audit_sql(add, instance_type="distributed")
    rr = checker.audit_sql(reorg, instance_type="distributed")
    assert "R121" in _ids(ra)
    assert "R121" in _ids(rr)


def test_r121_normal_bound_passes(parser, checker):
    sql = ("CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id) "
           "PARTITION BY RANGE(id) (PARTITION p1 VALUES LESS THAN (202702))")
    r = checker.audit_sql(sql, instance_type="distributed")
    assert "R121" not in _ids(r)


def test_r121_first_level_not_hit(parser, checker):
    sql = "CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY RANGE(id) (PARTITION pmax VALUES LESS THAN MAXVALUE)"
    r = checker.audit_sql(sql, instance_type="distributed")
    assert "R121" not in _ids(r)


def test_r121_decoys_not_hit(parser, checker):
    for sql in ("CREATE TABLE t (id INT, note VARCHAR(32) DEFAULT 'MAXVALUE')",
                "CREATE TDSQL_SEQUENCE s TDSQL_MAXVALUE 100"):
        r = checker.audit_sql(sql, instance_type="distributed")
        assert "R121" not in _ids(r), sql


def test_r121_centralized_skipped(parser, checker):
    sql = ("CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id) "
           "PARTITION BY RANGE(id) (PARTITION pmax VALUES LESS THAN MAXVALUE)")
    r = checker.audit_sql(sql, instance_type="centralized")
    assert "R121" not in _ids(r)


# ════════════════════════════════════════════════════════════
# 解析器三通道结构断言
# ════════════════════════════════════════════════════════════

def test_parser_dml_limit_fields(parser):
    p = parser.parse("UPDATE t SET a=1 WHERE b=2 LIMIT 2000")
    assert p.dml_limit["present"] is True
    assert p.dml_limit["row_count"] == 2000
    assert p.dml_limit["verifiable"] is True
    p2 = parser.parse("UPDATE t SET a=1 WHERE b=2 LIMIT 1, 2000")
    assert p2.dml_limit["offset"] == 1
    assert p2.dml_limit["verifiable"] is False
    p3 = parser.parse("UPDATE t SET a=1 WHERE b=2")
    assert p3.dml_limit["present"] is False


def test_parser_alter_column_types(parser):
    p = parser.parse("ALTER TABLE t ADD COLUMN c1 MEDIUMTEXT")
    assert p.alter_column_types and p.alter_column_types[0]["type"] == "MEDIUMTEXT"
    assert p.alter_column_types[0]["operation"] == "ADD"
    p2 = parser.parse("ALTER TABLE t CHANGE COLUMN c1 c2 LONGBLOB")
    assert p2.alter_column_types[0]["operation"] == "CHANGE"


def test_parser_secondary_partition_three_exits(parser):
    bare = ("CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id) "
            "PARTITION BY RANGE(id) (PARTITION pmax VALUES LESS THAN MAXVALUE)")
    p = parser.parse(bare)     # GATE-3：bare 已归一化为括号形态，正常 Create 出口
    assert p.secondary_partition["maxvalue_partitions"] == ("pmax",)
    add = "ALTER TABLE t ADD PARTITION (PARTITION pmax VALUES LESS THAN MAXVALUE)"
    pa = parser.parse(add)     # ALTER ADD：sqlglot 不支持该语法，ParseError 出口
    assert pa.secondary_partition["source_context"] == "ALTER_ADD"
    reorg = "ALTER TABLE t REORGANIZE PARTITION p0 INTO (PARTITION pmax VALUES LESS THAN (MAXVALUE))"
    pr = parser.parse(reorg)   # ALTER REORGANIZE：sqlglot 正常降级 Command 出口
    assert pr.secondary_partition["source_context"] == "ALTER_REORGANIZE"
    sel = parser.parse("SELECT 1")
    assert sel.secondary_partition["has_definition"] is False


# ════════════════════════════════════════════════════════════
# 质量门禁 strict/normal 矩阵（§10.2）
# ════════════════════════════════════════════════════════════

def _gate_pass(result, mode):
    errs = [v for v in result.violations
            if (v.severity.value if hasattr(v.severity, "value") else v.severity) == "ERROR"]
    warns = [v for v in result.violations
             if (v.severity.value if hasattr(v.severity, "value") else v.severity) == "WARNING"]
    if mode == "strict":
        return not errs and not warns
    return not errs


def _audit_only(checker, sql, keep, instance_type):
    """§10.2：门禁必须以无其他违规的最小规则集隔离测试——只保留 keep 中的规则。"""
    overrides = {r.rule_id: {"enabled": False} for r in checker.rules}
    for rid in keep:
        overrides[rid] = {"enabled": True}
    return checker.audit_sql(sql, rule_overrides=overrides, instance_type=instance_type)


def test_gate_text_relaxed(parser, checker):
    r = _audit_only(checker, "CREATE TABLE t (id INT, body TEXT)", {"R011", "R120"}, "distributed")
    assert _gate_pass(r, "strict")      # R011 改 INFO → strict 通过（相对旧 WARNING 放宽）
    assert _gate_pass(r, "normal")


def test_gate_lob_tightened(parser, checker):
    r = _audit_only(checker, "CREATE TABLE t (id INT, body MEDIUMTEXT)", {"R011", "R120"}, "distributed")
    assert not _gate_pass(r, "strict")
    assert not _gate_pass(r, "normal")


def test_gate_tinytext_relaxed(parser, checker):
    r = _audit_only(checker, "CREATE TABLE t (id INT, body TINYTEXT)", {"R011", "R120"}, "distributed")
    assert _gate_pass(r, "strict")
    assert _gate_pass(r, "normal")


def test_gate_centralized_r030_relaxed(parser, checker):
    r = _audit_only(checker, "CREATE VIEW v AS SELECT 1", {"R030"}, "centralized")
    assert _gate_pass(r, "strict")      # R030 改域后集中式放宽


def test_gate_distributed_maxvalue_tightened(parser, checker):
    sql = ("CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id) "
           "PARTITION BY RANGE(id) (PARTITION pmax VALUES LESS THAN (MAXVALUE))")
    r = _audit_only(checker, sql, {"R121"}, "distributed")
    assert not _gate_pass(r, "strict")
    assert not _gate_pass(r, "normal")


# ════════════════════════════════════════════════════════════
# SIT 第一轮整改回归锁（DEF-SIT-01 / DEF-SIT-02 / DEF-SIT-03）
# ════════════════════════════════════════════════════════════

_BASE_CREATE_PART = ("CREATE TABLE t_part (id BIGINT NOT NULL, dt DATE NOT NULL, "
                     "PRIMARY KEY (id, dt)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")

# 真实 MariaDB SHOW CREATE TABLE 产物（DEF-SIT-01 的主战场形态：
# to_days + 反引号 + ENGINE = InnoDB + 多行 + COLLATE）
_REAL_SHOW_CREATE_TABLE = """CREATE TABLE `t_part` (
  `id` bigint(20) NOT NULL,
  `dt` date NOT NULL,
  PRIMARY KEY (`id`,`dt`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
 PARTITION BY RANGE (to_days(`dt`))
(PARTITION `p0` VALUES LESS THAN (738000) ENGINE = InnoDB,
 PARTITION `pmax` VALUES LESS THAN MAXVALUE ENGINE = InnoDB)"""


@pytest.mark.parametrize("expr", [
    "(dt)", "(`dt`)", "(YEAR(dt))", "(MONTH(dt))", "(DAY(dt))",
    "(TO_DAYS(dt))", "(to_days(`dt`))", "(TO_SECONDS(dt))",
    "(UNIX_TIMESTAMP(dt))", "(EXTRACT(YEAR FROM dt))",
    "(ABS(id))", "(MOD(id,7))", "(FLOOR(id/100))",
    "COLUMNS(dt)", "COLUMNS(`dt`,id)", "(id,dt)",
])
def test_r121_covers_all_partition_expression_forms(checker, expr):
    """DEF-SIT-01：R121 不得因分区表达式形态而漏报——SHOW CREATE TABLE 的真实形态面。

    首版 _PARTITION_FUNCS 白名单（YEAR/MONTH/DAY）使 TO_DAYS/UNIX_TIMESTAMP/
    COLUMNS/多列表达式全部读不出 maxvalue_partitions；括号 (MAXVALUE) 形态下
    更是连 E999 都没有、完全静默通过。整改后策略扫描改用只跳过不校验的
    宽松表达式消费器（_consume_partition_expr_lenient）。
    """
    for boundary in ("MAXVALUE", "(MAXVALUE)"):
        sql = (_BASE_CREATE_PART + f" PARTITION BY RANGE {expr} "
               f"(PARTITION p0 VALUES LESS THAN (738000), "
               f"PARTITION pmax VALUES LESS THAN {boundary}) SHARDKEY=id")
        ids = _ids(checker.audit_sql(sql, instance_type="distributed"))
        assert "R121" in ids, f"{expr} + {boundary} 漏报 R121"


def test_r121_hits_real_show_create_table_output(parser, checker):
    """DEF-SIT-01：真实 SHOW CREATE TABLE 产物端到端锁（在线元数据审核主战场）。"""
    ids = _ids(checker.audit_sql(_REAL_SHOW_CREATE_TABLE, instance_type="distributed"))
    assert "R121" in ids
    fact = parser.parse(_REAL_SHOW_CREATE_TABLE).secondary_partition
    assert fact["has_definition"] is True
    assert fact["method"] == "RANGE"              # 整改要求③：method 正常回填
    assert fact["maxvalue_partitions"] == ("pmax",)
    assert fact["source_context"] == "CREATE"
    # 集中式：R121 按适用域跳过
    assert "R121" not in _ids(
        checker.audit_sql(_REAL_SHOW_CREATE_TABLE, instance_type="centralized"))


def test_lenient_expr_does_not_widen_recovery_gate():
    """DEF-SIT-01 反向锁：宽松表达式消费器只服务策略扫描，不得放宽 AST 恢复门禁。

    _consume_partition_expr 的白名单同时服务 _plan_recovery 的恢复门禁
    （v1.6.2.2 十三轮评审收敛的最敏感面），放宽它会让原先失败关闭的语句
    开始被恢复。本锁保证恢复链三个函数源码不引用宽松消费器。
    """
    import inspect
    import backend.engine.parser.parser_legacy as PL
    for fn in (PL._plan_recovery, PL._scan_table_tail, PL._consume_secondary_partition):
        src = inspect.getsource(fn)
        assert "_consume_partition_expr_lenient" not in src, \
            f"{fn.__name__} 不得使用宽松表达式消费器"
        assert "_skip_balanced_parens" not in src, \
            f"{fn.__name__} 不得使用只跳过不校验的括号消费器"


@pytest.mark.parametrize("boundary", ["MAXVALUE", "(MAXVALUE)"])
@pytest.mark.parametrize("inst", ["distributed", "centralized"])
def test_reorganize_maxvalue_must_not_fabricate_e999(checker, boundary, inst):
    """DEF-SIT-02 / GATE-3：ALTER … REORGANIZE 的 Command 降级是该语法的正常形态，
    不得合成为语法错误。首版对任何 Command + MAXVALUE 都合成 parse_error，使合法
    DDL 在集中式凭空多出 ERROR 级 E999。DEF-SIT-02 曾把守卫限定 source_context==CREATE；
    GATE-3 进一步彻底删除该合成守卫（bare MAXVALUE 已在 parse_one 前归一化为括号形态）。
    """
    sql = ("ALTER TABLE t REORGANIZE PARTITION p0 INTO ("
           "PARTITION p0 VALUES LESS THAN (2020), "
           f"PARTITION pmax VALUES LESS THAN {boundary})")
    ids = _ids(checker.audit_sql(sql, instance_type=inst))
    assert "E999_SYNTAX_ERROR" not in ids, "REORGANIZE 的正常 Command 降级不得报语法错误"
    assert ("R121" in ids) is (inst == "distributed")


def test_create_bare_maxvalue_no_e999_only_r121(checker):
    """GATE-3（林桑签署决议 §5 步骤3）：CREATE bare MAXVALUE 绝不报 E999，只精准命中 R121。

    推翻原 DEF-SIT-02 的「bare 失败关闭 E999+R121」口径——DBA 明确否定用假阳性
    E999 兜底业务拦截。bare 已在 parse_one 前归一化为等价括号形态，PK/引擎/字符集
    正常提取，R003/R004/R005 等级联假阳性归零。
    """
    sql = ("CREATE TABLE t (id INT NOT NULL, dt DATE NOT NULL, PRIMARY KEY(id, dt)) "
           "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 shardkey=id "
           "PARTITION BY RANGE (to_days(dt)) ("
           "PARTITION p0 VALUES LESS THAN (738000), "
           "PARTITION pmax VALUES LESS THAN MAXVALUE)")
    ids = _ids(checker.audit_sql(sql, instance_type="distributed"))
    assert "E999_SYNTAX_ERROR" not in ids, "绝对不得误报 E999"
    assert "R003" not in ids, "绝对不得误报未指定主键"
    assert "R004" not in ids, "绝对不得误报未指定引擎"
    assert "R005" not in ids, "绝对不得误报未指定字符集"
    assert "R121" in ids, "必须精准命中 R121"


# 林桑 GATE-3 拒签时在分布式即时审核页面实测失败的真实建表 DDL；整改前爆发 7 项
# 违规（E999 + R003/R004/R005/R028/R118 级联假阳性 + R121）。
_GATE3_USER_DDL = """CREATE TABLE `t_order_history` (
  `order_id` BIGINT NOT NULL COMMENT '订单ID（一级分片键）',
  `user_id` BIGINT NOT NULL COMMENT '用户ID',
  `amount` DECIMAL(10, 2) NOT NULL DEFAULT '0.00' COMMENT '订单金额',
  `create_time` DATETIME NOT NULL COMMENT '创建时间（二级Range分区键）',
  `status` TINYINT NOT NULL DEFAULT '0' COMMENT '订单状态',
  PRIMARY KEY (`order_id`, `create_time`),
  KEY `idx_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
shardkey=order_id
PARTITION BY RANGE (YEAR(create_time)) (
  PARTITION p2023 VALUES LESS THAN (2024),
  PARTITION p2024 VALUES LESS THAN (2025),
  PARTITION p2025 VALUES LESS THAN (2026),
  PARTITION p2026 VALUES LESS THAN (2027),
  PARTITION p_max VALUES LESS THAN MAXVALUE
)"""


def test_gate3_user_ddl_no_cascade_false_positives(checker):
    """GATE-3 验收锁：林桑实测拒签的建表 DDL，整改后必须消除全部级联假阳性。

    整改前：E999 + R003(未指定主键) + R004(未指定引擎) + R005(未指定字符集) +
    R118(分片键未 NOT NULL) 五项假阳性 + R121。整改后这五项必须全部消失，仅保留
    R121（真实命中）；该表本就缺失的表级 COMMENT(R028)/update_time(R036 INFO) 属
    真阳性，不在本锁的假阳性断言范围内。
    """
    ids = _ids(checker.audit_sql(_GATE3_USER_DDL, instance_type="distributed"))
    for fp in ("E999_SYNTAX_ERROR", "R003", "R004", "R005", "R118"):
        assert fp not in ids, f"GATE-3 级联假阳性未消除: {fp} ∈ {sorted(ids)}"
    assert "R121" in ids, "必须精准命中 R121（p_max 二级分区 MAXVALUE）"


def test_create_paren_maxvalue_still_no_e999(checker):
    """§4.7.5：CREATE 括号形态命中 R121、不报 E999（DEF-SIT-02 整改后仍须保持）。"""
    sql = (_BASE_CREATE_PART + " PARTITION BY RANGE (dt) "
           "(PARTITION p0 VALUES LESS THAN (100), "
           "PARTITION pmax VALUES LESS THAN (MAXVALUE)) SHARDKEY=id")
    ids = _ids(checker.audit_sql(sql, instance_type="distributed"))
    assert "R121" in ids
    assert "E999_SYNTAX_ERROR" not in ids


def test_dml_limit_does_not_add_tokenization_when_ast_is_sound(parser):
    """DEF-SIT-03：AST 完好时不得为「确认没有 LIMIT」再做一次全量词法化（设计 §5.4）。

    首版 _extract_dml_limit 的回退条件把「AST 不可靠」与「AST 里没有 limit
    节点」混为一谈，无 LIMIT 的 UPDATE/DELETE 每条多付一次全量词法化
    （非 DDL 批 15→17）。整改后 AST 完好即早退。
    """
    import sqlglot
    import sqlglot.tokens
    orig = sqlglot.tokens.Tokenizer.tokenize
    calls = {"n": 0}

    def spy(self, sql, *a, **k):
        calls["n"] += 1
        return orig(self, sql, *a, **k)

    sqlglot.tokens.Tokenizer.tokenize = spy
    try:
        counts = {}
        for s in ("SELECT * FROM t WHERE id=1",
                  "INSERT INTO t (a) VALUES (1)",
                  "UPDATE t SET a=1 WHERE id=1",
                  "DELETE FROM t WHERE id=1",
                  "UPDATE t SET a=1 WHERE id>0 LIMIT 2000"):
            calls["n"] = 0
            parser.parse(s)
            counts[s] = calls["n"]
    finally:
        sqlglot.tokens.Tokenizer.tokenize = orig
    assert len(set(counts.values())) == 1, f"各类语句的词法化次数应一致，实测 {counts}"
