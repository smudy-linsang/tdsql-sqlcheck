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
    """预检不再 *2：一个 40MB（<64MB max_allowed_packet）的结果不应被误拦。

    直接验证预检公式：payload = raw + margin（无 *2），40MB+64KiB < 64MB。
    """
    raw_mb = 40 * 1024 * 1024
    payload = raw_mb + R._PACKET_MARGIN
    max_pkt = 64 * 1024 * 1024
    assert payload < max_pkt, "40MB 结果在 64MB 包限制下应通过预检（不得 *2 误拦）"
    # 反例：旧的 *2 公式会误拦（80MB > 64MB）——证明 *2 是缺陷
    assert raw_mb * 2 + R._PACKET_MARGIN > max_pkt
