# -*- coding: utf-8 -*-
"""v1.6.3.5 / O 第二轮 UAT 整改回归锁（R2-01 心跳 / R2-02 终态计数与耗时 / R2-03 错误语义化）。

R2-01 根因：database.py 兼容层把 datetime 转 ISO（带 T），旧解析用空格格式失败被
`except: pass` 放行 → 过期心跳被误判新鲜。本文件覆盖该失效分支。
"""
from datetime import datetime, timezone, timedelta

import pytest

from backend.services import metadata_job_process as jp
from backend.services.metadata_audit_repository import MetadataJobError


# ══ R2-01：共享 UTC 时间解析 ══
def test_parse_utc_datetime_object():
    dt = datetime(2026, 9, 10, 12, 0, 0)
    out = jp.parse_utc(dt)
    assert out is not None and out.tzinfo is not None
    assert out.replace(tzinfo=None) == dt


def test_parse_utc_iso_with_T():
    # database.py 兼容层产物：v.isoformat() 带 T
    out = jp.parse_utc("2026-09-10T12:00:00.123456")
    assert out is not None and out.year == 2026 and out.microsecond == 123456


def test_parse_utc_space_format():
    out = jp.parse_utc("2026-09-10 12:00:00.123456")
    assert out is not None and out.microsecond == 123456


def test_parse_utc_invalid_and_empty():
    assert jp.parse_utc(None) is None
    assert jp.parse_utc("") is None
    assert jp.parse_utc("not-a-time") is None
    assert jp.parse_utc("2026-13-99 99:99:99") is None


def test_parse_utc_with_timezone():
    out = jp.parse_utc("2026-09-10T12:00:00+08:00")
    assert out is not None and out.tzinfo is not None


# ══ R2-01：心跳新鲜度 fail-closed ══
def test_is_fresh_heartbeat_cases():
    now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
    # 新鲜（5 秒前）
    assert jp.is_fresh_heartbeat(now - timedelta(seconds=5), window_seconds=10, now=now)
    # 边界（恰好 10 秒）
    assert jp.is_fresh_heartbeat(now - timedelta(seconds=10), window_seconds=10, now=now)
    # 过期（11 秒前）
    assert not jp.is_fresh_heartbeat(now - timedelta(seconds=11), window_seconds=10, now=now)
    # 过期 ISO 字符串（R2-01 失效分支：解析失败不得放行）
    stale_iso = (now - timedelta(seconds=20)).isoformat()
    assert not jp.is_fresh_heartbeat(stale_iso, window_seconds=10, now=now)
    # 缺失 / 非法 / 未来
    assert not jp.is_fresh_heartbeat(None, window_seconds=10, now=now)
    assert not jp.is_fresh_heartbeat("garbage", window_seconds=10, now=now)
    assert not jp.is_fresh_heartbeat(now + timedelta(seconds=60), window_seconds=10, now=now)


def test_check_runner_ready_stale_iso_heartbeat_503():
    """R2-01 复现：真实 ISO 心跳（带 T）且过期 → 必须 503，不得放行。"""
    from backend.api.metadata_audit import _check_runner_ready
    from backend.services.database import ensure_db, _get_connection
    ensure_db()
    conn = _get_connection()
    # 写入一个 20 秒前的 ISO 格式心跳（带 T，database.py 兼容层产物）
    stale = (datetime.now(timezone.utc) - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%S.%f")
    conn.execute("UPDATE metadata_audit_slot SET accepting=1, runner_heartbeat_at=? WHERE id=1",
                 (stale,))
    conn.commit(); conn.close()
    try:
        with pytest.raises(MetadataJobError) as ei:
            _check_runner_ready()
        assert ei.value.http_status == 503 and ei.value.code == "EXECUTOR_UNAVAILABLE"
    finally:
        conn = _get_connection()
        conn.execute("UPDATE metadata_audit_slot SET accepting=1, "
                     "runner_heartbeat_at=UTC_TIMESTAMP(6) WHERE id=1")
        conn.commit(); conn.close()


def test_check_runner_ready_missing_heartbeat_503():
    from backend.api.metadata_audit import _check_runner_ready
    from backend.services.database import ensure_db, _get_connection
    ensure_db()
    conn = _get_connection()
    conn.execute("UPDATE metadata_audit_slot SET accepting=1, runner_heartbeat_at=NULL WHERE id=1")
    conn.commit(); conn.close()
    try:
        with pytest.raises(MetadataJobError) as ei:
            _check_runner_ready()
        assert ei.value.http_status == 503
    finally:
        conn = _get_connection()
        conn.execute("UPDATE metadata_audit_slot SET accepting=1, "
                     "runner_heartbeat_at=UTC_TIMESTAMP(6) WHERE id=1")
        conn.commit(); conn.close()


# ══ R2-02：状态响应 elapsed 用真实 UTC 时间 ══
def test_job_summary_elapsed_with_iso_times():
    from backend.api.metadata_audit import _job_summary
    started = "2026-09-10T12:00:00.000000"
    finished = "2026-09-10T12:01:30.000000"
    job = {"id": "x" * 32, "state": "SUCCEEDED", "phase": "DONE", "db_name": "d",
           "execution_context_json": "{}", "error_code": None, "error_message": None,
           "exit_code": 0, "progress_json": '{"total_statements":63,"audited_statements":63}',
           "started_at": started, "finished_at": finished, "created_at": None,
           "report_id": 1, "snapshot_id": 2, "cleanup_ok": 1}
    s = _job_summary(job)
    assert s["elapsed_seconds"] == 90   # started→finished 实际秒数
    assert s["progress"]["total_statements"] == 63
    assert s["progress"]["audited_statements"] == 63


def test_job_summary_elapsed_none_when_not_started():
    from backend.api.metadata_audit import _job_summary
    job = {"id": "x" * 32, "state": "ACCEPTED", "phase": "WAITING", "db_name": "d",
           "execution_context_json": "{}", "error_code": None, "error_message": None,
           "exit_code": None, "progress_json": None, "started_at": None,
           "finished_at": None, "created_at": None, "report_id": None,
           "snapshot_id": None, "cleanup_ok": None}
    s = _job_summary(job)
    assert s["elapsed_seconds"] is None


# ══ R2-03：错误语义化（异常 args[0] 与文本两路）══
def test_humanize_db_error_from_exception_args():
    # 模拟 PyMySQL 异常：args[0] 是整数错误码
    class FakeErr(Exception):
        pass
    e = FakeErr(2013, "Lost connection to MySQL server during query")
    out = jp.humanize_db_error(e)
    assert "连接中断" in out and "2013" in out


def test_humanize_db_error_unknown_args_passthrough():
    class FakeErr(Exception):
        pass
    e = FakeErr(9999, "some unknown")
    out = jp.humanize_db_error(e)
    assert "9999" in out   # 未知码保留诊断线索


def test_humanize_all_mapped_codes():
    for code in ("2013", "2003", "2002", "2006", "1045", "1044", "1049", "1146", "1213", "1205"):
        out = jp.humanize_db_error(f"({code}, 'x')")
        assert "原始错误" in out, f"错误码 {code} 应有中文映射"
