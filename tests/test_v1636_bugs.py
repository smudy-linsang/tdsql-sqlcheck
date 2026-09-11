# -*- coding: utf-8 -*-
"""v1.6.3.6 内网大库问题回归锁（BUG-01 子表容错 / BUG-02 包预检与轻量压缩）。

BUG-01：TDSQL 物理分片子表（_tdsql_subp*）不可 SHOW CREATE，单表失败不得杀全库。
BUG-02：publish 预检不得 *2 虚假翻倍；超大结果启用向后兼容 list 压缩。
"""
import json

import pytest

from backend.services import metadata_audit_pipeline as P
from backend.services import metadata_audit_repository as R


# ══ BUG-01：TDSQL 内部物理子表"失败后分类"（R2-M-02：取消前置过滤，全靠 try/except）══
def _err(code):
    return Exception(code, "Proxy ERROR: does not exist")


def test_classify_extract_failure_benign():
    """分布式 + 660 + 命名匹配 + 父表在枚举 → tdsql_internal（良性）。"""
    assert P.classify_extract_failure(
        "cus_bas_merge_log_tdsql_subp190001", _err(660), "distributed",
        {"cus_bas_merge_log"}) == "tdsql_internal"


def test_classify_extract_failure_conditions():
    """四条件缺一即 extract_failed（异常）。"""
    nm = "orders_tdsql_subp202601"
    av = {"orders"}
    # 集中式 → 异常（条件 a）
    assert P.classify_extract_failure(nm, _err(660), "centralized", av) == "extract_failed"
    # 非"不存在"错误（1146/660 之外）→ 异常（条件 b）
    assert P.classify_extract_failure(nm, Exception(1044, "access denied"),
                                      "distributed", av) == "extract_failed"
    # 无数字命名（biz_tdsql_subp）→ 异常（条件 c）
    assert P.classify_extract_failure("biz_tdsql_subp", _err(660),
                                      "distributed", {"biz"}) == "extract_failed"
    # 父表不在枚举 → 异常（条件 d）
    assert P.classify_extract_failure(nm, _err(660), "distributed", {"other"}) == "extract_failed"
    # instance_type 为空（探测失败）→ 异常（安全方向，R2-M-03）
    assert P.classify_extract_failure(nm, _err(660), "", av) == "extract_failed"


class _FakeCursor:
    def __init__(self, rows, fail_names=()):
        self._rows = rows
        self._fail = set(fail_names)
        self._last_sql = ""

    def execute(self, sql, params=None):
        self._last_sql = sql
        # SHOW CREATE 对 fail_names 中的对象抛 Proxy 660
        for n in self._fail:
            if f"`{n}`" in sql and "SHOW CREATE" in sql.upper():
                raise Exception(f"(660, \"Proxy ERROR: Table:'x.{n}' does not exist\")")
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        if "SHOW CREATE" in self._last_sql.upper():
            for n in self._fail:
                if f"`{n}`" in self._last_sql:
                    raise Exception("(660, 'Proxy ERROR')")
            return {"Create Table": "CREATE TABLE t (id INT)"}
        return None


class _FakeConn:
    def __init__(self, rows, fail_names=()):
        self._cur = _FakeCursor(rows, fail_names)

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakePool:
    def __init__(self, rows, fail_names=()):
        self._conn = _FakeConn(rows, fail_names)

    def get_connection(self):
        return self._conn


# ══ BUG-01：单表 SHOW CREATE 失败容错跳过，不杀全库 ══
def test_single_table_show_create_error_resilient():
    rows = [
        {"TABLE_NAME": "t_good1", "TABLE_TYPE": "BASE TABLE"},
        {"TABLE_NAME": "t_bad", "TABLE_TYPE": "BASE TABLE"},   # 将抛 660
        {"TABLE_NAME": "t_good2", "TABLE_TYPE": "BASE TABLE"},
    ]
    pool = _FakePool(rows, fail_names=("t_bad",))
    lines, stats = P.extract_metadata(pool, "db1", ["TABLE"], instance_label="x", instance_type="distributed")
    sql = "\n".join(lines)
    # 任务不因单表失败而抛异常；成功 2、跳过 1
    assert stats["extracted_objects"] == 2
    assert stats["skipped_objects"] == 1
    # SQL 中含 [SKIPPED] 存证注释与跳过对象名
    assert "[SKIPPED]" in sql and "t_bad" in sql
    # 正常表仍在
    assert "t_good1" in sql and "t_good2" in sql


def test_tdsql_subp_filtered_before_show_create():
    """R2-M-02：子表 SHOW CREATE 失败（660）→ 分类良性 tdsql_internal 并跳过存证。

    （取消前置过滤后，子表仍会被尝试 SHOW CREATE，Proxy 660 失败 → 分类良性跳过。）
    """
    rows = [
        {"TABLE_NAME": "t_main", "TABLE_TYPE": "BASE TABLE"},
        {"TABLE_NAME": "t_main_tdsql_subp190001", "TABLE_TYPE": "BASE TABLE"},
    ]
    # 子表 SHOW CREATE 抛 660（Proxy 不允许读物理分片）
    pool = _FakePool(rows, fail_names=("t_main_tdsql_subp190001",))
    lines, stats = P.extract_metadata(pool, "db1", ["TABLE"], instance_label="x", instance_type="distributed")
    assert stats["extracted_objects"] == 1        # 只有主表成功
    assert stats["skipped_objects"] == 1          # 子表失败被跳过
    assert stats["skipped_benign"] == 1 and stats["skipped_abnormal"] == 0  # 分类为良性


def test_subp_pattern_but_show_create_succeeds_is_extracted():
    """A 第三轮硬指标：名字像子表但 SHOW CREATE **成功** → 正常提取，不跳过（零误杀）。"""
    rows = [
        {"TABLE_NAME": "orders", "TABLE_TYPE": "BASE TABLE"},
        {"TABLE_NAME": "orders_tdsql_subp202601", "TABLE_TYPE": "BASE TABLE"},  # 命名像子表但可读
    ]
    pool = _FakePool(rows)   # 都不失败 → SHOW CREATE 成功
    lines, stats = P.extract_metadata(pool, "db1", ["TABLE"], instance_label="x", instance_type="distributed")
    # 两张都成功提取（命名像子表但 SHOW CREATE 成功 = 真逻辑表，不得跳过）
    assert stats["extracted_objects"] == 2
    assert stats["skipped_objects"] == 0


def test_all_objects_fail_still_raises():
    """全部对象失败仍应报 NO_AUDITABLE_OBJECTS（不产空成功）。"""
    rows = [{"TABLE_NAME": "t_bad", "TABLE_TYPE": "BASE TABLE"}]
    pool = _FakePool(rows, fail_names=("t_bad",))
    with pytest.raises(P.MetadataExtractError) as ei:
        P.extract_metadata(pool, "db1", ["TABLE"], instance_label="x", instance_type="distributed")
    assert ei.value.code == "NO_AUDITABLE_OBJECTS"


# ══ BUG-02：轻量压缩向后兼容（list 格式）══
def test_compact_not_triggered_under_threshold():
    small = json.dumps([{"sql": "SELECT 1", "passed": True, "violations": []}])
    out, omitted = R.compact_results_for_audit_history(small, "job1")
    assert out == small and omitted == 0   # 未超阈值原样返回 + omitted=0


def test_big_payload_compaction_fallback_is_list():
    """70MB 巨大结果 → 压缩为 list（违规全留 + 通过样例 50），仍为 JSON list。"""
    records = []
    # 1 条违规 + 大量通过项，撑到 >32MB
    pad = "x" * 2000
    records.append({"sql": "CREATE TABLE bad (id INT)", "passed": False,
                    "violations": [{"rule_id": "R001", "severity": "ERROR",
                                    "message": "v"}]})
    for i in range(20000):
        records.append({"sql": f"CREATE TABLE t{i} (a VARCHAR(2000) COMMENT '{pad}')",
                        "passed": True, "violations": []})
    big = json.dumps(records, ensure_ascii=False)
    assert len(big.encode("utf-8")) > R.MAX_DB_PAYLOAD_THRESHOLD
    out, omitted = R.compact_results_for_audit_history(big, "job2")
    parsed = json.loads(out)
    assert isinstance(parsed, list), "压缩结果必须仍为 list（向后兼容消费方）"
    assert len(parsed) < len(records)
    # 违规条目必须保留
    assert any((not r.get("passed", True)) or r.get("violations") for r in parsed)
    # 压缩后远小于阈值
    assert len(out.encode("utf-8")) <= R.MAX_DB_PAYLOAD_THRESHOLD
    # omitted == 实际省略的通过项条数（R2-M-05 §2.4）
    assert omitted == len(records) - len(parsed) and omitted > 0


def test_publish_precheck_no_false_double():
    """B-03：预检不再有 *2 虚假翻倍——真调 publish() 验证 40MB（<64MB包限）能落库。

    （修复 D 发现的假绿：旧用例只在测试里重算公式，没真调 publish。）
    """
    from backend.services.database import ensure_db, _get_connection
    ensure_db()
    job, token = _mk_publishing_job("pprecheck")
    # 33MB 结果（区分带：*1.25→41MB<64MB 放行；*2→66MB>64MB 拦）。真实调 publish。
    big_sql = "CREATE TABLE t (a INT); -- " + ("x" * (33 * 1024 * 1024))
    results = [{"sql": big_sql, "passed": True, "violations": []}]
    results_json = json.dumps(results, ensure_ascii=False)
    rid = R.repository.publish(job["id"], token, audit_columns_values=_audit_cols(results_json),
                               results_json=results_json)
    assert rid > 0
    # M8：读回 audit_history.results_json，确认是完整 list 且写进了正确列（非 pass_rate）
    conn = _get_connection()
    row = conn.execute("SELECT results_json, total_sql FROM audit_history WHERE id=?",
                       (rid,)).fetchone()
    conn.close()
    stored = json.loads(row["results_json"])
    assert isinstance(stored, list) and len(stored) == 1  # 完整保留，未压成样例
    assert stored[0]["sql"] == big_sql
    R.repository.release_slot(job["id"])
    _cleanup_jobs("pprecheck")


def test_adaptive_threshold_no_compact_at_64mb():
    """R2-M-01 + R2-M-04：真调 publish()，载荷压在自适应阈值下方 → 无损落库（不压缩）。

    读真实元数据库 @@max_allowed_packet 反算载荷（不写死 64MiB）；包限不足以构造
    区分带时 skip。不允许在用例里自算阈值喂给压缩函数（那是假绿）。
    """
    from backend.services.database import ensure_db, _get_connection
    ensure_db()
    conn = _get_connection()
    max_pkt = int(conn.execute("SELECT @@session.max_allowed_packet AS p").fetchone()["p"])
    conn.close()
    adaptive = int((max_pkt - R._PACKET_MARGIN) / R._ESCAPE_FACTOR)
    # 需要包限至少够装下约 8MiB 的区分带载荷才有意义
    if adaptive < 8 * 1024 * 1024:
        pytest.skip(f"元数据库 max_allowed_packet={max_pkt}，不足以构造区分带载荷")
    # 载荷取自适应阈值的 80%（明确落在"转义后仍装得下、不应压缩"区间）
    target = int(adaptive * 0.8)
    big_sql = "CREATE TABLE t (a INT); -- " + ("x" * target)
    results = [{"sql": big_sql, "passed": True, "violations": []}]
    results_json = json.dumps(results, ensure_ascii=False)
    job, token = _mk_publishing_job("adaptive")
    rid = R.repository.publish(job["id"], token,
                               audit_columns_values=_audit_cols(results_json),
                               results_json=results_json)
    assert rid > 0
    conn = _get_connection()
    row = conn.execute("SELECT results_json, omitted_results FROM audit_history WHERE id=?",
                       (rid,)).fetchone()
    conn.close()
    stored = json.loads(row["results_json"])
    # 无损：条数与原文一致（未压缩），omitted_results=0
    assert isinstance(stored, list) and len(stored) == 1 and stored[0]["sql"] == big_sql
    assert int(row["omitted_results"]) == 0
    R.repository.release_slot(job["id"])
    _cleanup_jobs("adaptive")


def test_m9_skipped_list_truncated_at_extract_return():
    """M9：skipped_list 在 extract_metadata 返回处截断为前 50，计数仍全量。"""
    rows = [{"TABLE_NAME": f"t_fail{i}", "TABLE_TYPE": "BASE TABLE"} for i in range(120)]
    pool = _FakePool(rows, fail_names={f"t_fail{i}" for i in range(120)})
    # 全失败会抛 NO_AUDITABLE_OBJECTS；改留 1 个成功
    rows.append({"TABLE_NAME": "t_ok", "TABLE_TYPE": "BASE TABLE"})
    pool = _FakePool(rows, fail_names={f"t_fail{i}" for i in range(120)})
    lines, stats = P.extract_metadata(pool, "db1", ["TABLE"], instance_label="x", instance_type="distributed")
    assert stats["skipped_objects"] == 120       # 计数全量
    assert len(stats["skipped_list"]) == 50       # list 截断为 50
    assert stats["skipped_abnormal"] == 120       # 非良性跳过计数


# ══ 公共辅助 ══
def _mk_publishing_job(tag):
    """建一个进入 PUBLISHING 状态的测试任务（真实库）。返回 (job, token)。"""
    from backend.services.database import ensure_db, _get_connection
    ensure_db()
    conn = _get_connection()
    conn.execute("DELETE FROM metadata_audit_jobs WHERE created_by=?", (f"v1636_{tag}",))
    conn.execute("UPDATE metadata_audit_slot SET active_job_id=NULL, accepting=1 WHERE id=1")
    conn.commit(); conn.close()
    job, _ = R.repository.create_job(
        created_by=f"v1636_{tag}", request_id="r", idempotency_key=f"k_{tag}",
        request_hash=f"h_{tag}", connection_id="c", db_name="d", request_json="{}",
        execution_context_json="{}", connection_fingerprint="f",
        report_deadline_seconds=600)
    R.repository.claim_next_accepted("r", "tok", R._now())
    R.repository.cas_state(job["id"], "tok", (R.STATE_RUNNING,), R.STATE_PUBLISHING)
    return job, "tok"


def _audit_cols(results_json, skipped_objects=None, skipped_benign=None,
                skipped_abnormal=None):
    from backend.services.metadata_audit_repository import _now
    # audit_history 21 基列 + 3 个跳过计数（publish 再补 omitted_results 成 25 列）
    return ("extracted_schema", "f.sql", 1, 1, 0, 0, 0, 100.0, results_json,
            "v1636", "", None, "", _now(), "c", "d", None, "centralized", "auto",
            0, None, skipped_objects, skipped_benign, skipped_abnormal)


def _cleanup_jobs(tag):
    from backend.services.database import _get_connection
    conn = _get_connection()
    conn.execute("DELETE FROM metadata_audit_jobs WHERE created_by=?", (f"v1636_{tag}",))
    conn.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
    conn.commit(); conn.close()


# ══ R2-N-01：补锁 ══
def test_n10_object_name_sanitized_in_sql():
    """N10：含 CR/LF 的对象名，[SKIPPED] 注释行必须全部以 -- 开头（不逃逸出注释）。"""
    rows = [{"TABLE_NAME": "t_ok", "TABLE_TYPE": "BASE TABLE"},
            {"TABLE_NAME": "evil\r\nDROP TABLE x--", "TABLE_TYPE": "BASE TABLE"}]
    pool = _FakePool(rows, fail_names=("evil\r\nDROP TABLE x--",))
    lines, stats = P.extract_metadata(pool, "db1", ["TABLE"], instance_label="x",
                                      instance_type="distributed")
    for ln in lines:
        if "evil" in ln or "DROP" in ln:
            assert ln.lstrip().startswith("--"), f"含恶意对象名的行未包进注释: {ln!r}"


def test_n12_report_html_scope_line_has_skip_counts():
    """N12：report.html 口径行必须含'跳过'与'异常'。"""
    from backend.workers.metadata_audit_worker import _build_report_html
    job = {"id": "x", "db_name": "d"}
    html = _build_report_html(job, {}, {"total_sql": 5, "passed": 4, "failed": 1,
                                        "error_count": 1, "warning_count": 0,
                                        "pass_rate": 80.0},
                              {"enumerated_objects": 5, "selected_objects": 5,
                               "extracted_objects": 4, "skipped_objects": 1,
                               "skipped_abnormal": 1},
                              [{"sql": "CREATE TABLE t (id INT)", "passed": True,
                                "sql_type": "CREATE", "violations": []}])
    assert "跳过" in html and "异常" in html


def test_n4_compaction_writes_results_json_column_not_pass_rate():
    """N4：触发压缩的路径上，results_json 列写压缩后 list，pass_rate 列不被污染。"""
    from backend.services.database import ensure_db, _get_connection
    ensure_db()
    job, token = _mk_publishing_job("n4")
    # 构造超自适应阈值的大结果（全通过，触发压缩）
    pad = "x" * 2000
    records = [{"sql": f"CREATE TABLE t{i} (a VARCHAR(2000) COMMENT '{pad}')",
                "passed": True, "violations": []} for i in range(20000)]
    results_json = json.dumps(records, ensure_ascii=False)
    cols = list(_audit_cols(results_json))
    cols[7] = 97.5   # pass_rate 列
    rid = R.repository.publish(job["id"], token, audit_columns_values=tuple(cols),
                               results_json=results_json)
    conn = _get_connection()
    row = conn.execute("SELECT results_json, pass_rate, omitted_results FROM audit_history WHERE id=?",
                       (rid,)).fetchone()
    conn.close()
    # results_json 列是压缩后的合法 list；pass_rate 列未被 results_json 污染
    stored = json.loads(row["results_json"])
    assert isinstance(stored, list)
    assert abs(float(row["pass_rate"]) - 97.5) < 0.01   # pass_rate 仍是数值
    assert int(row["omitted_results"]) >= 0
    R.repository.release_slot(job["id"])
    _cleanup_jobs("n4")


def test_n9_rollback_wrapped_so_metadata_error_raises():
    """N9：publish 的 except 里 rollback 包 try/except——INSERT 失败（包限/连接重置）
    时抛出的必须是 MetadataJobError（含真因），不裸穿 2006。"""
    import inspect
    src = inspect.getsource(R.MetadataJobRepository.publish)
    # except 分支的 rollback 必须包在 try 里（结构性锁）
    assert "conn.rollback()" in src
    assert "except MetadataJobError" in src
    # rollback 行必须出现在一个 except 包裹的 try 块内（不能裸调）
    assert "try:\n                conn.rollback()" in src or \
           "try:\n            conn.rollback()" in src, "rollback 未包 try/except"


def test_n2_escape_factor_present_and_bounded():
    """N2：预检用实测转义系数（1.25，在 1.0 与 2.0 之间的实测区间），不是 1.0 或 2.0。"""
    assert R._ESCAPE_FACTOR == 1.25
    assert 1.0 < R._ESCAPE_FACTOR < 2.0
