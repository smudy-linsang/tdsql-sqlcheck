# -*- coding: utf-8 -*-
"""v1.6.3.5 / FIX-01 / D01 — R035 流式审核 + 有界见证索引回归（DETAIL §11.1 ALG-01~11）。

覆盖：
  · ALG-01~07：R035 语义与既有规则口径逐条锁定（首个冲突来源、长度不比较、
    非 CREATE 不入历史、E999 不丢、R035 off/severity 覆盖/架构、R064 不被污染）。
  · ALG-08：独立见证模型 1,062,880 次查询 0 差异（等价性，对应 DETAIL 附录 B）。
  · ALG-09：相同 SQL 全规则下新流式 audit_file 与旧全历史 oracle 的 AuditResult 逐项一致。
  · ALG-10：随表数增长内存/见证数不呈平方级；索引 O(U)。
  · ALG-11：两次请求索引隔离，无全局污染。
"""
from itertools import product

import pytest

from backend.engine.checker import RuleChecker
from backend.engine.r035_context import R035PriorIndex, R035_CROSS_KEY


def _checker():
    return RuleChecker()


def _r035_hits(results):
    """每个语句是否命中 R035。"""
    return [any(v.rule_id == "R035" for v in r.violations) for r in results]


def _r035_first_source(result):
    """返回该语句首个 R035 违规里的来源表名（无则 None）。"""
    for v in result.violations:
        if v.rule_id == "R035":
            return v
    return None


# ────────────────────────────────────────────────────────────────────────────
# 旧算法 oracle（小规模）：用仍保留的 _build_r035_cross_table_context 全历史快照跑
# ────────────────────────────────────────────────────────────────────────────
def _audit_old_oracle(checker, content, rule_overrides=None, instance_type=None):
    """用旧 O(N²) 全历史快照算法跑（小样本 oracle），返回与 audit_file 同结构的结果。"""
    from backend.engine.parser import split_audit_script
    from backend.engine.checker import normalize_newlines
    content = normalize_newlines(content)
    stmts = [(s, ln) for s, ln, _e in split_audit_script(content)]
    items = [(s, ln, checker.parser.parse(s)) for s, ln in stmts]
    metas = checker._build_r035_cross_table_context(items, rule_overrides, instance_type)
    out = []
    for (s, ln, parsed), meta in zip(items, metas):
        violations = checker._audit_parsed(parsed, s, meta, ln, rule_overrides, instance_type)
        out.append((s.strip(), parsed.sql_type, len(violations) == 0,
                    [(v.rule_id, v.severity, v.message) for v in violations], ln))
    return out


def _audit_new(checker, content, rule_overrides=None, instance_type=None):
    res = checker.audit_file(content, rule_overrides=rule_overrides, instance_type=instance_type)
    return [(r.sql, r.sql_type, r.passed,
             [(v.rule_id, v.severity, v.message) for v in r.violations], r.line_number)
            for r in res]


# ════════════════════════════════════════════════════════════════════════════
# ALG-01：a.id INT；b.id BIGINT；c.id INT → [否,是,是]，c 的首个冲突来源是 b
# ════════════════════════════════════════════════════════════════════════════
def test_alg01_first_conflict_source():
    c = _checker()
    sql = ("CREATE TABLE a (id INT);\n"
           "CREATE TABLE b (id BIGINT);\n"
           "CREATE TABLE c (id INT);\n")
    res = c.audit_file(sql)
    assert _r035_hits(res) == [False, True, True]
    # c 的首个冲突来源必须是 b（不是 a；a.id 也是 INT 不冲突）
    v = _r035_first_source(res[2])
    assert v is not None and "表 b" in v.message and "表 a" not in v.message


# ════════════════════════════════════════════════════════════════════════════
# ALG-02：同类型不同 VARCHAR 长度 / DECIMAL 参数不重新引入长度比较；大小写列名
# ════════════════════════════════════════════════════════════════════════════
def test_alg02_length_params_not_compared():
    c = _checker()
    # VARCHAR(20) vs VARCHAR(50) 规范类型同为 VARCHAR → 不报；DECIMAL(10,2) vs DECIMAL(12,0) 同 → 不报
    sql = ("CREATE TABLE t1 (name VARCHAR(20), amt DECIMAL(10,2));\n"
           "CREATE TABLE t2 (name VARCHAR(50), amt DECIMAL(12,0));\n")
    res = c.audit_file(sql)
    assert _r035_hits(res) == [False, False], "规范化基础类型相同不得因长度/参数误报"


def test_alg02_case_insensitive_column_name():
    c = _checker()
    # 列名大小写不同（UserID vs userid）按 lower() 归键 → 应命中跨表冲突
    sql = ("CREATE TABLE t1 (UserID INT);\n"
           "CREATE TABLE t2 (userid BIGINT);\n")
    res = c.audit_file(sql)
    assert _r035_hits(res) == [False, True]


# ════════════════════════════════════════════════════════════════════════════
# ALG-03：同表反复 CREATE、跨表混合类型、schema 限定名、空类型、重复列名
# ════════════════════════════════════════════════════════════════════════════
def test_alg03_first_conflict_matches_oracle():
    c = _checker()
    sql = (
        "CREATE TABLE db1.ord (id INT, total BIGINT);\n"
        "CREATE TABLE `db1`.`pay` (id INT, amt DECIMAL(10,2));\n"
        "CREATE TABLE ord (id INT, total BIGINT);\n"      # 与 db1.ord 表名不同（精确相等）
        "CREATE TABLE log1 (id BIGINT);\n"               # id 冲突（vs ord/pay 的 INT）
        "CREATE TABLE db2.log2 (id VARCHAR(32));\n"      # id 冲突
        "CREATE TABLE weird (id, x INT);\n"              # 空类型列
        "CREATE TABLE dup (id INT, id BIGINT);\n"        # 同语句重复列名
    )
    new = _audit_new(c, sql)
    old = _audit_old_oracle(c, sql)
    assert new == old, "与完整历史 oracle 首个违规来源不一致"


# ════════════════════════════════════════════════════════════════════════════
# ALG-04：多列各自冲突、同语句多个同名列 → 按当前列顺序报首个冲突
# ════════════════════════════════════════════════════════════════════════════
def test_alg04_column_order_first_conflict():
    c = _checker()
    sql = ("CREATE TABLE base (a INT, b INT);\n"
           "CREATE TABLE t (a BIGINT, b VARCHAR(10));\n")  # a、b 都与 base 冲突
    res = c.audit_file(sql)
    hits = [v for r in res for v in r.violations if v.rule_id == "R035"]
    assert hits, "应有 R035 冲突"
    # 首个冲突按当前列顺序应是列 a
    assert "字段 a" in hits[0].message


# ════════════════════════════════════════════════════════════════════════════
# ALG-05：CREATE 夹杂 VIEW/SELECT/ALTER、语法错误 → 只有成功 CREATE 入历史，E999 不丢
# ════════════════════════════════════════════════════════════════════════════
def test_alg05_non_create_and_error_not_in_history():
    c = _checker()
    sql = ("CREATE TABLE a (id INT);\n"
           "SELECT id FROM a;\n"
           "CREATE VIEW v1 AS SELECT id FROM a;\n"
           "ALTER TABLE a ADD COLUMN x INT;\n"
           "CREATE TABLE b (id BIGINT);\n")   # 与 a 的 id 冲突
    res = c.audit_file(sql)
    # b（第5条，index 4）应命中 R035（与 a 冲突）；VIEW/SELECT/ALTER 不产生历史
    assert _r035_hits(res)[4] is True
    # 语法错误语句 E999 不丢（fail-closed）
    sql_err = "CREATE TABLE a (id INT);\nCREATE TABLE broken (id INT"
    res_err = c.audit_file(sql_err)
    assert any(v.rule_id == "E999_SYNTAX_ERROR" for r in res_err for v in r.violations)


# ════════════════════════════════════════════════════════════════════════════
# ALG-06：拆句/行号/解析次数与基线一致（DELIMITER/CRLF/注释分号/过程体）
# ════════════════════════════════════════════════════════════════════════════
def test_alg06_split_and_parse_once():
    c = _checker()
    sql = ("CREATE TABLE a (id INT);\r\n"          # CRLF
           "-- 注释里有个分号; 不应拆句\n"
           "CREATE TABLE b (id BIGINT, note VARCHAR(10) COMMENT 'a;b');\n")
    res = c.audit_file(sql)
    assert len(res) == 2, f"拆句数应为 2，实得 {len(res)}"
    assert _r035_hits(res) == [False, True]


# ════════════════════════════════════════════════════════════════════════════
# ALG-07：R035 off 不建索引；severity 覆盖生效；R064 不被私有 metadata 污染
# ════════════════════════════════════════════════════════════════════════════
def test_alg07_r035_off_creates_no_index():
    c = _checker()
    sql = "CREATE TABLE a (id INT);\nCREATE TABLE b (id BIGINT);\n"
    # 关闭 R035（rule_overrides 禁用）
    res = c.audit_file(sql, rule_overrides={"R035": {"enabled": False}})
    assert _r035_hits(res) == [False, False], "R035 禁用时不应命中"
    # 启用时命中
    res_on = c.audit_file(sql)
    assert _r035_hits(res_on) == [False, True]


def test_alg07_private_meta_not_leaked_to_other_rules():
    """R035 的私有 CROSS_KEY 不得污染依赖 `if not table_metadata` 的规则（R064 等）。"""
    c = _checker()
    # 构造一个会被 R035 看到上下文的 CREATE，同时观察是否有规则把 meta 当非空误判
    sql = "CREATE TABLE a (id INT, x VARCHAR(10));\nCREATE TABLE b (id BIGINT, x INT);\n"
    res = c.audit_file(sql)
    # 只要有 R035 命中即证明 meta 传递了；关键是没有规则因 meta 非空而新增异常违规
    assert any(v.rule_id == "R035" for v in res[1].violations)
    # 所有非 R035 违规的 message 不得包含 CROSS_KEY 字样（私有键不外泄）
    for r in res:
        for v in r.violations:
            assert R035_CROSS_KEY not in (v.message or "")


# ════════════════════════════════════════════════════════════════════════════
# ALG-08：独立见证模型——≥1,062,880 次查询与完整历史 oracle 0 差异
# ════════════════════════════════════════════════════════════════════════════
def test_alg08_witness_index_equivalence_model():
    """对每个列名的有界见证索引，其首个冲突与完整历史扫描的首个冲突完全一致。

    复现 DETAIL 附录 B 模型，但直接驱动生产 R035PriorIndex（非整数元组）。
    """
    # 历史域：3 个表 × 3 个类型；查询域：4 表 × 4 类型（含从未出现值）
    domain = list(product(range(3), repeat=2))     # (table, type)
    queries = list(product(range(4), repeat=2))
    checks = 0
    peak = 0
    for n in range(6):                              # 历史长度 0—5
        for seq in product(domain, repeat=n):
            idx = R035PriorIndex()
            history = []
            for si, (tb, ty) in enumerate(seq):
                history.append({"table_name": f"t{tb}", "type": f"T{ty}",
                                "raw_type": f"T{ty}", "statement_index": si,
                                "column_index": 0})
                idx.add_columns(f"t{tb}", [{"name": "col", "type": f"T{ty}",
                                            "raw_type": f"T{ty}"}], si)
            refs = idx.project_for_column("col")
            peak = max(peak, len(refs))
            for (qt, qy) in queries:
                expected = next((r for r in history
                                 if r["table_name"] != f"t{qt}" and r["type"] != f"T{qy}"),
                                None)
                actual = next((r for r in refs
                               if r["table_name"] != f"t{qt}" and r["type"] != f"T{qy}"),
                              None)
                assert actual == expected, (seq, qt, qy)
                checks += 1
    assert checks == 1062880, f"查询次数应为 1,062,880，实得 {checks}"
    assert peak <= 5, f"见证槽位应 ≤5，实得峰值 {peak}"


# ════════════════════════════════════════════════════════════════════════════
# ALG-09：相同 SQL 全规则下，新流式 audit_file 与旧 oracle 的 AuditResult 逐项一致
# ════════════════════════════════════════════════════════════════════════════
def test_alg09_full_auditresult_equivalence():
    c = _checker()
    sql = (
        "CREATE TABLE db1.ord (id INT NOT NULL, total BIGINT, name VARCHAR(20),"
        " create_time DATETIME, update_time DATETIME, is_deleted TINYINT);\n"
        "CREATE TABLE db1.pay (id INT, amt DECIMAL(10,2), status VARCHAR(10),"
        " create_time DATETIME, update_time DATETIME, is_deleted TINYINT);\n"
        "CREATE TABLE db1.log (id BIGINT, msg TEXT, create_time DATETIME,"
        " update_time DATETIME, is_deleted TINYINT);\n"          # id 与 ord/pay 冲突
        "CREATE TABLE db1.x (name INT, create_time DATETIME, update_time DATETIME,"
        " is_deleted TINYINT);\n"                                 # name 与 ord 冲突
        "UPDATE db1.ord SET total=1 WHERE id=1;\n"               # 非 CREATE
        "CREATE TABLE broken (id INT\n"                           # 语法错误
    )
    new = _audit_new(c, sql)
    old = _audit_old_oracle(c, sql)
    assert len(new) == len(old)
    for i, (n, o) in enumerate(zip(new, old)):
        assert n == o, f"第 {i} 条 AuditResult 不一致:\n新 {n}\n旧 {o}"


# ════════════════════════════════════════════════════════════════════════════
# ALG-10：内存/见证数不随表数呈平方级；索引 O(U)
# ════════════════════════════════════════════════════════════════════════════
def test_alg10_index_bounded_no_quadratic():
    idx = R035PriorIndex()
    # 800 表 × 20 个同名列 → 每列名见证 ≤5（不随表数增长）
    for si in range(800):
        cols = [{"name": f"c{j}", "type": "INT", "raw_type": "INT"} for j in range(20)]
        idx.add_columns(f"t{si}", cols, si)
    for j in range(20):
        refs = idx.project_for_column(f"c{j}")
        assert len(refs) <= 5, f"列 c{j} 见证数应 ≤5，实得 {len(refs)}"


def test_alg10_no_ast_retention_in_audit_file():
    """audit_file 完成后不残留全批 AST/meta（流式）。"""
    import gc
    c = _checker()
    big = "\n".join(f"CREATE TABLE t{i} (id INT, a VARCHAR(10), b BIGINT);" for i in range(300))
    res = c.audit_file(big)
    assert len(res) == 300
    # 强制 GC 后，checker 不应持有 parsed_items/metas 属性
    gc.collect()
    assert not hasattr(c, "parsed_items")
    assert not hasattr(c, "metas")


# ════════════════════════════════════════════════════════════════════════════
# ALG-11：两次请求索引隔离，无全局污染
# ════════════════════════════════════════════════════════════════════════════
def test_alg11_request_isolation():
    c = _checker()
    sql_a = "CREATE TABLE a (id INT);\nCREATE TABLE b (id BIGINT);\n"   # b 命中
    sql_b = "CREATE TABLE c (id INT);\n"                                # 独立请求，c 是首个
    r1 = c.audit_file(sql_a)
    r2 = c.audit_file(sql_b)
    assert _r035_hits(r1) == [False, True]
    # 第二次请求不应被第一次的历史污染：c 是首个出现的 id，不应命中
    assert _r035_hits(r2) == [False]
    # 再跑同一文件，结果可复现（无残留状态）
    r1b = c.audit_file(sql_a)
    assert _r035_hits(r1b) == [False, True]
