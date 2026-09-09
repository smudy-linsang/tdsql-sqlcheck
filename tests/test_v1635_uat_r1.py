# -*- coding: utf-8 -*-
"""v1.6.3.5 / UAT 第一轮整改回归锁（M1 runner缺失503 / M2 progress恒为dict / M3 错误码语义化）。"""
import pytest

from backend.services import metadata_job_process as jp


# ══ UAT-M3：PyMySQL 错误码语义化 ══
def test_humanize_2013_lost_connection():
    out = jp.humanize_db_error("(2013, 'Lost connection to MySQL server during query')")
    assert "连接中断" in out and "2013" in out   # 中文提示 + 保留原始错误


def test_humanize_1049_unknown_database():
    out = jp.humanize_db_error("(1049, \"Unknown database 's5t'\")")
    assert "不存在" in out and "1049" in out


def test_humanize_1045_access_denied():
    out = jp.humanize_db_error("(1045, \"Access denied for user\")")
    assert "鉴权失败" in out


def test_humanize_unknown_code_passthrough():
    """未知错误码/无错误码原样返回（不丢信息）。"""
    assert jp.humanize_db_error("some other error") == "some other error"
    assert jp.humanize_db_error("(9999, 'x')") == "(9999, 'x')"


# ══ UAT-M2：任务状态响应的 progress 恒为 dict（终态也不为 None）══
def test_job_summary_progress_always_dict():
    from backend.api.metadata_audit import _job_summary
    # progress_json 为 None（如早期失败）时，progress 仍应是 dict 而非 None
    for pj in (None, "", '{"extracted_objects": 5}'):
        job = {"id": "x" * 32, "state": "FAILED", "phase": "CLEANUP", "db_name": "d",
               "execution_context_json": "{}", "error_code": "X", "error_message": "y",
               "exit_code": 1, "progress_json": pj, "started_at": None,
               "finished_at": None, "created_at": None, "report_id": None,
               "snapshot_id": None, "cleanup_ok": None}
        s = _job_summary(job)
        assert isinstance(s["progress"], dict), f"progress 应为 dict，实得 {type(s['progress'])}"
        assert "enumerated_objects" in s["progress"]


# ══ UAT-M1：runner 未就绪时新受理返回 503（功能锁，M 已确认非产品缺陷）══
def test_runner_missing_returns_503():
    from backend.api.metadata_audit import _check_runner_ready
    from backend.services.metadata_audit_repository import MetadataJobError
    from backend.services.database import ensure_db, _get_connection
    ensure_db()
    conn = _get_connection()
    conn.execute("UPDATE metadata_audit_slot SET accepting=0 WHERE id=1")
    conn.commit(); conn.close()
    try:
        with pytest.raises(MetadataJobError) as ei:
            _check_runner_ready()
        assert ei.value.http_status == 503
        assert ei.value.code == "EXECUTOR_UNAVAILABLE"
    finally:
        conn = _get_connection()
        conn.execute("UPDATE metadata_audit_slot SET accepting=1 WHERE id=1")
        conn.commit(); conn.close()
