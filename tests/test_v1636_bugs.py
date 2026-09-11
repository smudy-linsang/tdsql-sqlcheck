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


def test_adaptive_threshold_no_compact_below_threshold(monkeypatch):
    """R3-M-01：>50 条通过记录、载荷压在自适应阈值下方 → 不压缩（omitted=0，全保留）。

    用 fake conn 控制 max_packet=64MiB（阈值≈53.7Mi），载荷取 0.8×阈值≈43Mi（>32Mi）。
    变异 P7（阈值还原写死 32MiB）时：43Mi>32Mi → 压缩 → omitted>0 → 本用例变红。
    （R3-M-01：旧版用单条记录，压缩退化成恒等变换，变异下观测不到。）
    """
    fake = _FakePublishConn(max_pkt=67108864)
    monkeypatch.setattr(R, "_get_connection", lambda: fake)
    threshold = int((67108864 - R._PACKET_MARGIN) / R._ESCAPE_FACTOR)   # ≈53.7Mi
    target = int(threshold * 0.8)                                       # ≈43Mi
    assert target > 32 * 1024 * 1024, "载荷须 >32Mi 才能在变异(写死32Mi)下触发压缩"
    n_rec = 2000
    per = target // n_rec
    records = [{"sql": "CREATE TABLE t%d (a INT); -- " % i + "x" * per,
                "passed": True, "violations": []} for i in range(n_rec)]
    results_json = json.dumps(records, ensure_ascii=False)
    rid = R.repository.publish("j", "tok",
                               audit_columns_values=_audit_cols(results_json),
                               results_json=results_json)
    assert rid == 1
    # 未压缩：omitted_count（INSERT 参数末位）==0，results_json 列保留全部 n_rec 条
    assert int(fake.insert_params[-1]) == 0
    assert len(json.loads(fake.insert_params[8])) == n_rec


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
    """N10/R3-M-03：含 CR/LF 的对象名，**落盘文本逐行**必须全部以 -- 开头（不逃逸出注释）。

    R3-M-03：检查口径是 `"\\n".join(lines).splitlines()`（落盘后的真实行），
    不是 list 元素——后者会把内嵌 \\r\\n 的元素当成一行，去掉 sanitize 也照样以 -- 开头。
    """
    rows = [{"TABLE_NAME": "t_ok", "TABLE_TYPE": "BASE TABLE"},
            {"TABLE_NAME": "evil\r\nDROP TABLE x--", "TABLE_TYPE": "BASE TABLE"}]
    pool = _FakePool(rows, fail_names=("evil\r\nDROP TABLE x--",))
    lines, stats = P.extract_metadata(pool, "db1", ["TABLE"], instance_label="x",
                                      instance_type="distributed")
    text_lines = "\n".join(lines).splitlines()   # 落盘后的真实文本行
    matched = [ln for ln in text_lines if "evil" in ln or "DROP" in ln]
    assert matched, "恶意对象名应出现在 [SKIPPED] 注释中"
    for ln in matched:
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


def test_n4_compaction_writes_results_json_column_not_pass_rate(monkeypatch):
    """R3-M-02：载荷 > 自适应阈值（1.2x）→ 触发压缩；results_json 列（索引8）写压缩后
    list，pass_rate 列（索引7）不被污染，omitted>0。（fake conn 控制 max_packet，载荷随阈值反算）

    R3-M-02：旧版载荷 39.9Mi 低于阈值→压缩是死代码，P9/P10 变异打不响。
    变异 P9（列索引 8→7）：压缩值写到 pass_rate 位 → p[7]!=97.5 → 变红。
    变异 P10（omitted 恒 0）：p[-1]==0 → 变红。
    """
    fake = _FakePublishConn(max_pkt=67108864)
    monkeypatch.setattr(R, "_get_connection", lambda: fake)
    threshold = int((67108864 - R._PACKET_MARGIN) / R._ESCAPE_FACTOR)
    target = int(threshold * 1.2)   # >阈值 → 触发压缩
    n_rec = 3000
    per = max(1, target // n_rec)
    records = [{"sql": "CREATE TABLE t%d (a INT); -- " % i + "x" * per,
                "passed": True, "violations": []} for i in range(n_rec)]
    results_json = json.dumps(records, ensure_ascii=False)
    assert len(results_json.encode()) > threshold, "载荷须超阈值才触发压缩"
    cols = list(_audit_cols(results_json))
    cols[7] = 97.5   # pass_rate 列
    rid = R.repository.publish("j", "tok", audit_columns_values=tuple(cols),
                               results_json=results_json)
    assert rid == 1
    p = fake.insert_params
    stored = json.loads(p[8])   # results_json 列（索引 8）= 压缩后 list
    assert isinstance(stored, list) and len(stored) < n_rec   # 压缩后条数减少
    assert p[7] == 97.5   # pass_rate 列（索引 7）未被 results_json 污染
    assert int(p[-1]) > 0   # omitted_results > 0


class _FakePublishCursor:
    def __init__(self, rows=None, lastrowid=1, rowcount=1):
        self._rows = rows or []
        self.lastrowid = lastrowid
        self.rowcount = rowcount
    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakePublishConn:
    """R3-N-01/02：模拟 publish 的连接——可控 max_packet、INSERT/rollback 行为，
    并捕获 INSERT 参数（用于断言 omitted_count / results_json 列）。不依赖真实库。"""
    def __init__(self, max_pkt=67108864, job=None,
                 insert_raises=None, rollback_raises=None):
        self.max_pkt = max_pkt
        self.job = job or {"id": "j", "attempt_token": "tok",
                           "state": R.STATE_PUBLISHING, "report_id": None}
        self.insert_raises = insert_raises
        self.rollback_raises = rollback_raises
        self.insert_params = None
        self.rolled_back = False
    def execute(self, sql, params=None):
        s = " ".join(sql.split()).upper()
        if "@@SESSION.MAX_ALLOWED_PACKET" in s:
            return _FakePublishCursor([{"p": self.max_pkt}])
        if "METADATA_AUDIT_SLOT" in s:
            return _FakePublishCursor([{"id": 1}])
        if "FROM METADATA_AUDIT_JOBS" in s:
            return _FakePublishCursor([self.job])
        if "INSERT INTO AUDIT_HISTORY" in s:
            self.insert_params = params
            if self.insert_raises is not None:
                raise self.insert_raises
            return _FakePublishCursor(lastrowid=1)
        if "UPDATE METADATA_AUDIT_JOBS" in s:
            return _FakePublishCursor(rowcount=1)
        return _FakePublishCursor()
    def rollback(self):
        self.rolled_back = True
        if self.rollback_raises is not None:
            raise self.rollback_raises
    def commit(self):
        pass
    def close(self):
        pass


def test_n9_rollback_failure_does_not_mask_metadata_error(monkeypatch):
    """R3-N-02：行为断言——INSERT 抛 1153 + rollback 抛 2006 时，publish 必须抛
    MetadataJobError（含真因），不裸穿 2006/1153。（不再是源码文本断言）"""
    import pymysql
    fake = _FakePublishConn(
        insert_raises=pymysql.err.OperationalError(1153, "packet bigger than max_allowed_packet"),
        rollback_raises=pymysql.err.OperationalError(2006, "MySQL server has gone away"))
    monkeypatch.setattr(R, "_get_connection", lambda: fake)
    with pytest.raises(R.MetadataJobError) as ei:
        R.repository.publish("j", "tok",
                             audit_columns_values=_audit_cols("[]"),
                             results_json="[]")
    assert ei.value.code == "REPORT_SAVE_FAILED"
    assert fake.rolled_back, "rollback 应被调用（其 2006 被 try/except 吞掉）"


def test_n2_precheck_escape_factor_rejects_in_band(monkeypatch):
    """R3-N-01：行为断言——预检乘转义系数(1.25)：构造 len<max_pkt 但 len×1.25>max_pkt
    的全违规载荷（压缩不缩减违规），预检必须抛 PERSIST_PAYLOAD_TOO_LARGE。
    P8（预检去掉 *系数、常量原地留着）：len<max_pkt → 不拦 → 本锁变红。"""
    fake = _FakePublishConn(max_pkt=67108864)   # 64MiB
    monkeypatch.setattr(R, "_get_connection", lambda: fake)
    max_pkt = 67108864
    # 全违规载荷：len 卡在 (max_pkt/1.25, max_pkt)——乘系数超限、不乘则不超
    target = int(max_pkt * 0.9)   # ≈60.4Mi：<64Mi 但 ×1.25≈75.5Mi>64Mi
    n_rec = 2000
    per = target // n_rec
    records = [{"sql": "CREATE TABLE t%d (a INT); -- " % i + "x" * per,
                "passed": False, "violations": [{"rule_id": "R001"}]} for i in range(n_rec)]
    results_json = json.dumps(records, ensure_ascii=False)
    raw = len(results_json.encode())
    assert raw < max_pkt < int(raw * R._ESCAPE_FACTOR), \
        "载荷须卡在'乘系数超限/不乘不超'的区分带"
    with pytest.raises(R.MetadataJobError) as ei:
        R.repository.publish("j", "tok",
                             audit_columns_values=_audit_cols(results_json),
                             results_json=results_json)
    assert ei.value.code == "PERSIST_PAYLOAD_TOO_LARGE"


# ══ R3-M-05：L6 缺参 TypeError + worker instance_type 接线锁 ══
def test_m05_l6_missing_arg_and_worker_wiring(monkeypatch):
    """R3-M-05：（1）classify 四参数全部必传，缺参抛 TypeError（L6）；
    （2）worker 接线锁——ctx 无 instance_type 时 worker 传给 extract_metadata 的必须是
    ""（不是 "distributed"）。P17（or "distributed"）会被本锁捕获。"""
    import pymysql
    err = pymysql.err.OperationalError(660, "x")
    # L6：缺 available_tables → TypeError（四参必传，无缺省）
    with pytest.raises(TypeError):
        P.classify_extract_failure("cus_tdsql_subp123456", err, "distributed")
    # worker 接线：monkeypatch extract_metadata 捕获 worker 传入的 instance_type
    from backend.workers import metadata_audit_worker as W
    from backend.services import metadata_audit_repository as RR
    import backend.services.connection_registry as CRG
    captured = {}

    class _Stop(BaseException):
        pass

    def _fake_extract(pool, db, scopes, instance_label="", instance_type="MISSING"):
        captured["it"] = instance_type
        raise _Stop()
    monkeypatch.setattr(P, "extract_metadata", _fake_extract)
    job = {"id": "j1", "attempt_token": "tok", "state": RR.STATE_RUNNING,
           "execution_context_json": json.dumps({}),   # ctx 无 instance_type
           "request_json": json.dumps({"scopes": ["TABLE"]}),
           "connection_id": "c1", "db_name": "db1", "created_by": "u"}
    monkeypatch.setattr(RR.repository, "get_job", lambda jid: dict(job))
    monkeypatch.setattr(RR.repository, "cas_state", lambda *a, **k: None)
    monkeypatch.setattr(RR.repository, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(CRG.registry, "get", lambda cid: object())
    try:
        W.run("j1", "tok")
    except BaseException:
        pass
    assert captured.get("it") == "", \
        f"worker 应传空串（非 distributed），实际 {captured.get('it')!r}"


# ══ R3-M-04：L11 历史 HTML 告警条 + L12 SQL 文件头 锁 ══
def test_m06_l11_l12_completeness_surfaces():
    """R3-M-04：造 skipped_abnormal>0 / omitted_results>0 的真实记录，调两个呈现端点：
    L11 历史报告 HTML 顶部告警条（异常→红、节选→橙，条件不串）；
    L12 SQL 下载文件头（[跳过]/[节选]）。删掉告警/文件头代码 → 本锁变红。"""
    import asyncio
    from backend.services.database import ensure_db, _get_connection
    from backend.api.sql_audit import (export_extracted_report_html,
                                       download_extracted_report_sql)
    ensure_db()
    conn = _get_connection()
    _results = json.dumps([{"sql": "CREATE TABLE t (id INT)", "sql_type": "CREATE",
                            "passed": True, "violations": []}], ensure_ascii=False)

    def _insert(sk_obj, sk_ben, sk_abn, omitted):
        cols = tuple(_audit_cols(_results, skipped_objects=sk_obj,
                                 skipped_benign=sk_ben,
                                 skipped_abnormal=sk_abn)) + (omitted,)
        cur = conn.execute(
            """INSERT INTO audit_history (audit_type, source, total_sql, passed, failed,
                error_count, warning_count, pass_rate, results_json, created_by, project_id,
                gate_passed, gate_detail, created_at, connection_id, db_name, rule_set_id,
                instance_type, instance_type_source, skipped_rules_count, report_context_json,
                skipped_objects, skipped_benign, skipped_abnormal, omitted_results)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", cols)
        conn.commit()
        return int(cur.lastrowid)

    rid_abn = _insert(7, 0, 7, 0)          # 仅异常跳过
    rid_om = _insert(2800, 2800, 0, 5894)  # 仅节选
    try:
        html_abn = asyncio.run(export_extracted_report_html(rid_abn)).body.decode("utf-8")
        html_om = asyncio.run(export_extracted_report_html(rid_om)).body.decode("utf-8")
        # L11：异常→红色告警（无节选提示）；节选→橙色提示（无异常告警）
        assert "未能读取 DDL" in html_abn and "已省略" not in html_abn
        assert "已省略" in html_om and "未能读取 DDL" not in html_om
        sql_abn = asyncio.run(download_extracted_report_sql(rid_abn)).body.decode("utf-8")
        sql_om = asyncio.run(download_extracted_report_sql(rid_om)).body.decode("utf-8")
        # L12：文件头 [跳过]/[节选] 条件不串
        assert "-- [跳过]" in sql_abn and "-- [节选]" not in sql_abn
        assert "-- [节选]" in sql_om and "已省略 5894" in sql_om and "-- [跳过]" not in sql_om
    finally:
        conn.execute("DELETE FROM audit_history WHERE id IN (?,?)", (rid_abn, rid_om))
        conn.commit()
        conn.close()


# ══ R3-B-01（BLOCK）锁：良性跳过写汇总不写逐个块 ══
def test_m07_benign_skip_summary_not_per_object_block():
    """R3-B-01（BLOCK 锁）：良性子表跳过**不写逐个 [SKIPPED] 块**（否则大库文件
    94% 是注释、审核超线性变慢），只在文件末尾写一行 [SKIPPED-SUMMARY] 汇总。
    复活“良性也写逐个块”→本锁变红。"""
    rows = [
        {"TABLE_NAME": "t_main", "TABLE_TYPE": "BASE TABLE"},
        {"TABLE_NAME": "t_main_tdsql_subp190001", "TABLE_TYPE": "BASE TABLE"},
        {"TABLE_NAME": "t_main_tdsql_subp190002", "TABLE_TYPE": "BASE TABLE"},
    ]
    pool = _FakePool(rows, fail_names=("t_main_tdsql_subp190001",
                                       "t_main_tdsql_subp190002"))
    lines, stats = P.extract_metadata(pool, "db1", ["TABLE"], instance_label="x",
                                      instance_type="distributed")
    sql = "\n".join(lines)
    assert stats["skipped_benign"] == 2 and stats["skipped_abnormal"] == 0
    # 良性：末尾一行汇总，且**没有**逐个 [SKIPPED] 块
    assert "[SKIPPED-SUMMARY]" in sql and "2 张已跳过" in sql
    assert "-- [SKIPPED] SQL Object" not in sql, "良性跳过不应写逐个 [SKIPPED] 块"
    # 良性子表名不逐个落盘（只在 skipped_list）
    assert "t_main_tdsql_subp190001" not in sql


def test_m07b_abnormal_skip_still_writes_per_object_block():
    """R3-B-01 对照：异常跳过（extract_failed）**保留**逐个 [SKIPPED] 块（需溯源）。

    t_denied 名字不匹配子表模式 → 无论错误码都判 extract_failed（异常）。"""
    rows = [
        {"TABLE_NAME": "t_ok", "TABLE_TYPE": "BASE TABLE"},
        {"TABLE_NAME": "t_denied", "TABLE_TYPE": "BASE TABLE"},   # 名字非子表→异常
    ]
    pool = _FakePool(rows, fail_names=("t_denied",))
    lines, stats = P.extract_metadata(pool, "db1", ["TABLE"], instance_label="x",
                                      instance_type="distributed")
    sql = "\n".join(lines)
    assert stats["skipped_abnormal"] == 1 and stats["skipped_benign"] == 0
    assert "-- [SKIPPED] SQL Object" in sql and "t_denied" in sql   # 异常写逐个块
    assert "[SKIPPED-SUMMARY]" not in sql                            # 无良性→无汇总行
