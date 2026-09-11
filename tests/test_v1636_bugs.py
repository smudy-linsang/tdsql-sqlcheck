# -*- coding: utf-8 -*-
"""v1.6.3.6 内网大库问题回归锁（BUG-01 子表容错 / BUG-02 包预检与轻量压缩）。

BUG-01：TDSQL 物理分片子表（_tdsql_subp*）不可 SHOW CREATE，单表失败不得杀全库。
BUG-02：publish 预检不得 *2 虚假翻倍；超大结果启用向后兼容 list 压缩。
"""
import json

import pytest

from backend.services import metadata_audit_pipeline as P
from backend.services import metadata_audit_repository as R


# ══ BUG-01：TDSQL 内部物理子表识别 ══
@pytest.mark.parametrize("name,expect", [
    ("cus_bas_merge_log_tdsql_subp190001", True),
    ("t_order_tdsql_shard12", True),
    ("T_X_TDSQL_SUBP0001", True),
    ("t_order", False),
    ("cus_bas_merge_log", False),
    ("t_tdsql_config", False),   # 不以 _tdsql_subp/shard+数字 结尾
])
def test_is_tdsql_internal_table(name, expect):
    assert P.is_tdsql_internal_table(name) is expect


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
    lines, stats = P.extract_metadata(pool, "db1", ["TABLE"], instance_label="x")
    sql = "\n".join(lines)
    # 任务不因单表失败而抛异常；成功 2、跳过 1
    assert stats["extracted_objects"] == 2
    assert stats["skipped_objects"] == 1
    # SQL 中含 [SKIPPED] 存证注释与跳过对象名
    assert "[SKIPPED]" in sql and "t_bad" in sql
    # 正常表仍在
    assert "t_good1" in sql and "t_good2" in sql


def test_tdsql_subp_filtered_before_show_create():
    """_tdsql_subp 子表被前置过滤（不计入 extracted，也不触发 SHOW CREATE 失败）。"""
    rows = [
        {"TABLE_NAME": "t_main", "TABLE_TYPE": "BASE TABLE"},
        {"TABLE_NAME": "t_main_tdsql_subp190001", "TABLE_TYPE": "BASE TABLE"},
    ]
    # 注意：不给 fail_names——若子表未被前置过滤而去 SHOW CREATE，FakeCursor 会返回
    # 正常 DDL（不报错），但我们断言它被计入 skipped 而非 extracted。
    pool = _FakePool(rows)
    lines, stats = P.extract_metadata(pool, "db1", ["TABLE"], instance_label="x")
    assert stats["extracted_objects"] == 1        # 只有主表
    assert stats["skipped_objects"] == 1          # 子表被前置跳过
    assert "t_main_tdsql_subp190001" not in "\n".join(
        l for l in lines if l.startswith("-- Table:"))


def test_all_objects_fail_still_raises():
    """全部对象失败仍应报 NO_AUDITABLE_OBJECTS（不产空成功）。"""
    rows = [{"TABLE_NAME": "t_bad", "TABLE_TYPE": "BASE TABLE"}]
    pool = _FakePool(rows, fail_names=("t_bad",))
    with pytest.raises(P.MetadataExtractError) as ei:
        P.extract_metadata(pool, "db1", ["TABLE"], instance_label="x")
    assert ei.value.code == "NO_AUDITABLE_OBJECTS"


# ══ BUG-02：轻量压缩向后兼容（list 格式）══
def test_compact_not_triggered_under_threshold():
    small = json.dumps([{"sql": "SELECT 1", "passed": True, "violations": []}])
    out = R.compact_results_for_audit_history(small, "job1")
    assert out == small   # 未超阈值原样返回


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
    out = R.compact_results_for_audit_history(big, "job2")
    parsed = json.loads(out)
    assert isinstance(parsed, list), "压缩结果必须仍为 list（向后兼容消费方）"
    assert len(parsed) < len(records)
    # 违规条目必须保留
    assert any((not r.get("passed", True)) or r.get("violations") for r in parsed)
    # 压缩后远小于阈值
    assert len(out.encode("utf-8")) <= R.MAX_DB_PAYLOAD_THRESHOLD


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
    """B-01：64MiB 包限下自适应阈值≈53MiB，42MiB 结果**不触发**压缩（无损）。"""
    max_pkt = 64 * 1024 * 1024
    adaptive = int((max_pkt - R._PACKET_MARGIN) / R._ESCAPE_FACTOR)
    # 42 MiB 结果（内网 6097 表库的真实体量）不应被压缩
    raw_42mb = "x" * (42 * 1024 * 1024)
    js = json.dumps([{"sql": raw_42mb, "passed": True, "violations": []}])
    assert len(js.encode("utf-8")) < adaptive, "42MiB 应低于自适应阈值（不压缩）"
    out = R.compact_results_for_audit_history(js, "j", threshold_bytes=adaptive)
    assert out == js, "低于自适应阈值必须原样返回（不压缩）"


def test_m9_skipped_list_truncated_at_extract_return():
    """M9：skipped_list 在 extract_metadata 返回处截断为前 50，计数仍全量。"""
    rows = [{"TABLE_NAME": f"t_fail{i}", "TABLE_TYPE": "BASE TABLE"} for i in range(120)]
    pool = _FakePool(rows, fail_names={f"t_fail{i}" for i in range(120)})
    # 全失败会抛 NO_AUDITABLE_OBJECTS；改留 1 个成功
    rows.append({"TABLE_NAME": "t_ok", "TABLE_TYPE": "BASE TABLE"})
    pool = _FakePool(rows, fail_names={f"t_fail{i}" for i in range(120)})
    lines, stats = P.extract_metadata(pool, "db1", ["TABLE"], instance_label="x")
    assert stats["skipped_objects"] == 120       # 计数全量
    assert len(stats["skipped_list"]) == 50       # list 截断为 50
    assert stats["skipped_abnormal"] == 120       # 非良性跳过计数


def test_m10_subp_requires_digit_and_parent_and_distributed():
    """M10 + B-02：\\d+（无数字不匹配）+ 集中式不过滤 + 父表须在枚举中。"""
    # 无数字 → 不匹配（\\d* 笔误已修）
    assert P.is_tdsql_internal_table("biz_tdsql_subp", "distributed",
                                     {"biz"}) is False
    assert P.is_tdsql_internal_table("foo_tdsql_shard", "distributed", {"foo"}) is False
    # 集中式一律不过滤
    assert P.is_tdsql_internal_table("orders_tdsql_subp202601", "centralized",
                                     {"orders"}) is False
    # 分布式 + 父表不在枚举 → 不过滤（防误杀真业务表）
    assert P.is_tdsql_internal_table("orders_tdsql_subp202601", "distributed",
                                     {"other"}) is False
    # 分布式 + 父表在枚举 + 有数字 → 过滤（内网故障对象）
    assert P.is_tdsql_internal_table("cus_bas_merge_log_tdsql_subp190001",
                                     "distributed", {"cus_bas_merge_log"}) is True


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


def _audit_cols(results_json):
    from backend.services.metadata_audit_repository import _now
    return ("extracted_schema", "f.sql", 1, 1, 0, 0, 0, 100.0, results_json,
            "v1636", "", None, "", _now(), "c", "d", None, "centralized", "auto", 0, None)


def _cleanup_jobs(tag):
    from backend.services.database import _get_connection
    conn = _get_connection()
    conn.execute("DELETE FROM metadata_audit_jobs WHERE created_by=?", (f"v1636_{tag}",))
    conn.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
    conn.commit(); conn.close()
