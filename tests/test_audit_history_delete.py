"""
历史元数据审核记录批量删除 — 单元测试

覆盖：权限（仅 admin）、类型隔离（不误删文件审核记录）、参数校验、
批量上限、快照默认保留 / 显式级联、被对比留档引用的快照强制保留、
子表级联与门禁痕迹保留、审计日志。
"""
import asyncio
import json

import pytest

from backend.api.sql_audit import (MAX_DELETE_BATCH,
                                   batch_delete_extracted_reports)
from backend.services import scan_snapshot_service as snap
from backend.services.database import _get_connection, ensure_db
from backend.services.snapshot_extractors.schema_audit import extract_from_json
from fastapi import HTTPException


# ── 辅助 ──

class _FakeState:
    def __init__(self, role, username):
        self.role = role
        self.username = username


class _FakeClient:
    host = "127.0.0.1"


class _FakeRequest:
    """最小 Request 替身：处理函数只用到 state / client / headers"""

    def __init__(self, role="admin", username="pytest"):
        self.state = _FakeState(role, username)
        self.client = _FakeClient()
        self.headers = {"user-agent": "pytest-agent"}


def _call(payload, role="admin"):
    return asyncio.run(
        batch_delete_extracted_reports(payload, _FakeRequest(role=role)))


_RESULTS = json.dumps([
    {"sql": "CREATE TABLE `t_del` (id int)",
     "violations": [{"rule_id": "R003", "severity": "ERROR",
                     "message": "CREATE TABLE 未指定主键", "suggestion": "加主键"}]},
], ensure_ascii=False)


def _mk_history(audit_type="extracted_schema", source="ut.sql",
                created_at="2026-01-05 03:00:00", db_name="trade_core"):
    """插入一条 audit_history，返回 id"""
    ensure_db()
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO audit_history
              (audit_type, source, connection_id, db_name, total_sql, passed, failed,
               error_count, warning_count, pass_rate, results_json, created_by, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (audit_type, source, "c-del-ut", db_name, 1, 0, 1, 1, 0, 0.0,
              _RESULTS, "pytest", created_at))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _mk_snapshot_for(history_id, finished="2026-01-05 03:00:00"):
    items, obj_total = extract_from_json(_RESULTS, "trade_core")
    return snap.create_snapshot("schema_audit", {
        "biz_ref_id": str(history_id), "connection_id": "c-del-ut",
        "connection_name": "删除用例实例", "db_name": "trade_core", "node": "",
        "scan_label": f"ut-{history_id}", "scan_finished_at": finished,
        "created_by": "pytest",
    }, items, obj_total)


def _history_exists(history_id):
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM audit_history WHERE id = ?", (history_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def _snapshot_exists(snapshot_id):
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM scan_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


# ── 权限 ──

@pytest.mark.parametrize("role", ["dba", "developer", "auditor", "", "Admin"])
def test_non_admin_rejected(role):
    """非 admin 一律 403，且不得先执行删除再报错"""
    hid = _mk_history()
    with pytest.raises(HTTPException) as e:
        _call({"ids": [hid]}, role=role)
    assert e.value.status_code == 403
    assert _history_exists(hid), "鉴权失败时不得删除任何数据"


def test_admin_allowed():
    hid = _mk_history()
    out = _call({"ids": [hid]})
    assert out["status"] == "SUCCESS"
    assert out["deleted"] == 1
    assert out["deleted_ids"] == [hid]
    assert not _history_exists(hid)


# ── 参数校验 ──

def test_empty_ids_rejected():
    with pytest.raises(HTTPException) as e:
        _call({"ids": []})
    assert e.value.status_code == 400


def test_missing_payload_rejected():
    with pytest.raises(HTTPException) as e:
        _call({})
    assert e.value.status_code == 400


def test_non_integer_ids_rejected():
    with pytest.raises(HTTPException) as e:
        _call({"ids": ["abc"]})
    assert e.value.status_code == 400


def test_non_positive_ids_filtered_to_empty():
    """0 / 负数被过滤掉后等价于空集，返回 400 而非误删"""
    with pytest.raises(HTTPException) as e:
        _call({"ids": [0, -3]})
    assert e.value.status_code == 400


def test_over_batch_limit_rejected():
    ids = list(range(1, MAX_DELETE_BATCH + 2))
    with pytest.raises(HTTPException) as e:
        _call({"ids": ids})
    assert e.value.status_code == 400
    assert str(MAX_DELETE_BATCH) in str(e.value.detail)


def test_nonexistent_ids_return_404():
    with pytest.raises(HTTPException) as e:
        _call({"ids": [99999901, 99999902]})
    assert e.value.status_code == 404


def test_duplicate_ids_deduped():
    hid = _mk_history()
    out = _call({"ids": [hid, hid, hid]})
    assert out["deleted"] == 1
    assert out["deleted_ids"] == [hid]


# ── 类型隔离 ──

def test_file_audit_records_never_deleted():
    """audit_type='file' 的记录即使被显式指定也不能删"""
    file_id = _mk_history(audit_type="file", source="ut_file.sql")
    schema_id = _mk_history()
    out = _call({"ids": [file_id, schema_id]})
    assert out["deleted"] == 1
    assert out["deleted_ids"] == [schema_id]
    assert file_id in out["skipped_ids"]
    assert _history_exists(file_id), "文件审核记录被误删"
    assert not _history_exists(schema_id)


def test_only_file_ids_return_404():
    file_id = _mk_history(audit_type="file")
    with pytest.raises(HTTPException) as e:
        _call({"ids": [file_id]})
    assert e.value.status_code == 404
    assert _history_exists(file_id)


# ── 快照处置 ──

def test_snapshot_kept_by_default():
    """默认不删快照：对比基线是独立冻结产物，不应被静默摧毁"""
    hid = _mk_history()
    sid = _mk_snapshot_for(hid)
    out = _call({"ids": [hid]})
    assert out["deleted"] == 1
    assert out["snapshots_found"] == 1
    assert out["snapshots_deleted"] == 0
    assert out["snapshots_kept"] == 1
    assert _snapshot_exists(sid), "默认不应删除基线快照"


def test_snapshot_purged_when_requested():
    hid = _mk_history()
    sid = _mk_snapshot_for(hid)
    out = _call({"ids": [hid], "purge_snapshots": True})
    assert out["snapshots_found"] == 1
    assert out["snapshots_deleted"] == 1
    assert out["snapshots_kept_referenced"] == 0
    assert not _snapshot_exists(sid)


def test_referenced_snapshot_kept_even_when_purge_requested():
    """被 scan_compare_reports 留档引用的快照必须保留，否则留档指向空快照"""
    hid_a, hid_b = _mk_history(), _mk_history()
    sid_a = _mk_snapshot_for(hid_a, "2026-01-05 03:00:00")
    sid_b = _mk_snapshot_for(hid_b, "2026-01-20 03:00:00")

    conn = _get_connection()
    try:
        conn.execute("""
            INSERT INTO scan_compare_reports
              (module, connection_id, connection_name, db_name,
               base_snapshot_id, target_snapshot_id, title, created_by)
            VALUES (?,?,?,?,?,?,?,?)
        """, ("schema_audit", "c-del-ut", "删除用例实例", "trade_core",
              sid_a, sid_b, "留档-删除用例", "pytest"))
        conn.commit()
    finally:
        conn.close()

    out = _call({"ids": [hid_a, hid_b], "purge_snapshots": True})
    assert out["deleted"] == 2
    assert out["snapshots_found"] == 2
    assert out["snapshots_deleted"] == 0
    assert out["snapshots_kept_referenced"] == 2
    assert _snapshot_exists(sid_a) and _snapshot_exists(sid_b)


def test_purge_mixed_referenced_and_free():
    """一批中既有被引用又有游离快照：只删游离的"""
    hid_ref, hid_free = _mk_history(), _mk_history()
    sid_ref = _mk_snapshot_for(hid_ref, "2026-02-01 03:00:00")
    sid_free = _mk_snapshot_for(hid_free, "2026-02-02 03:00:00")

    conn = _get_connection()
    try:
        conn.execute("""
            INSERT INTO scan_compare_reports
              (module, connection_id, base_snapshot_id, target_snapshot_id, title, created_by)
            VALUES (?,?,?,?,?,?)
        """, ("schema_audit", "c-del-ut", sid_ref, sid_ref, "自引用留档", "pytest"))
        conn.commit()
    finally:
        conn.close()

    out = _call({"ids": [hid_ref, hid_free], "purge_snapshots": True})
    assert out["snapshots_found"] == 2
    assert out["snapshots_deleted"] == 1
    assert out["snapshots_kept_referenced"] == 1
    assert _snapshot_exists(sid_ref)
    assert not _snapshot_exists(sid_free)


def test_other_module_snapshots_untouched():
    """biz_ref_id 数值可能与其它模块撞号，删除必须限定 module='schema_audit'"""
    hid = _mk_history()
    items, obj_total = extract_from_json(_RESULTS, "trade_core")
    other = snap.create_snapshot("bigtable", {
        "biz_ref_id": str(hid), "connection_id": "c-del-ut",
        "connection_name": "删除用例实例", "db_name": "trade_core", "node": "",
        "scan_label": "bigtable-同号", "scan_finished_at": "2026-01-05 03:00:00",
        "created_by": "pytest",
    }, items, obj_total)

    out = _call({"ids": [hid], "purge_snapshots": True})
    assert out["snapshots_found"] == 0, "不应把其它模块的同号快照算进来"
    assert _snapshot_exists(other), "其它模块快照被误删"


# ── 子表级联 ──

def test_audit_results_cascade_deleted():
    """audit_results 外键 ON DELETE CASCADE，应随主记录清理"""
    hid = _mk_history()
    conn = _get_connection()
    try:
        conn.execute("""
            INSERT INTO audit_results
              (audit_history_id, sql_text, sql_type, passed, violations_json)
            VALUES (?,?,?,?,?)
        """, (hid, "CREATE TABLE `t_del` (id int)", "CREATE", 0, "[]"))
        conn.commit()
        cnt = dict(conn.execute(
            "SELECT COUNT(*) AS c FROM audit_results WHERE audit_history_id = ?",
            (hid,)).fetchone())["c"]
        assert cnt == 1
    finally:
        conn.close()

    _call({"ids": [hid]})

    conn = _get_connection()
    try:
        cnt = dict(conn.execute(
            "SELECT COUNT(*) AS c FROM audit_results WHERE audit_history_id = ?",
            (hid,)).fetchone())["c"]
        assert cnt == 0, "audit_results 未随主记录级联删除"
    finally:
        conn.close()


def test_gate_audit_log_preserved_with_null_ref():
    """gate_audit_logs 为 ON DELETE SET NULL：门禁合规痕迹必须保留"""
    hid = _mk_history()
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO gate_audit_logs
              (project_id, audit_history_id, source, passed, error_count)
            VALUES (?,?,?,?,?)
        """, ("p-del-ut", hid, "ut.sql", 0, 1))
        conn.commit()
        gate_id = cur.lastrowid
    finally:
        conn.close()

    _call({"ids": [hid]})

    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT audit_history_id FROM gate_audit_logs WHERE id = ?",
            (gate_id,)).fetchone()
        assert row is not None, "门禁审计痕迹被误删"
        assert dict(row)["audit_history_id"] is None
    finally:
        conn.close()


# ── 审计日志 ──

def test_module_logger_is_defined():
    """回归：sql_audit 曾用 logger 却从未定义，快照生成失败时 NameError
    会被外层 except 再次触发 NameError，把成功的审核放大成 HTTP 500。"""
    import logging as _logging

    import backend.api.sql_audit as m
    assert isinstance(getattr(m, "logger", None), _logging.Logger)


def test_operation_logged():
    hid = _mk_history()
    _call({"ids": [hid]})
    conn = _get_connection()
    try:
        row = conn.execute("""
            SELECT operator, detail FROM operation_logs
            WHERE operation_type = ? AND target_type = ? AND target_id = ?
            ORDER BY id DESC LIMIT 1
        """, ("delete_audit_history", "audit_history", str(hid))).fetchone()
        assert row is not None, "删除操作未写审计日志"
        d = dict(row)
        assert d["operator"] == "pytest"
        assert "deleted=1" in (d["detail"] or "")
    finally:
        conn.close()
