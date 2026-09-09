# -*- coding: utf-8 -*-
"""v1.6.3.5 第一轮 SIT 整改回归锁（B-02 资源护栏 / B-03 错误分类 / M-01 stderr / N-01 错误暴露）。"""
import os
import subprocess
import sys

import pytest

from backend.services import metadata_job_process as jp
from backend.services.metadata_audit_repository import MetadataJobError


# ══ B-02：资源护栏参数与预检 ══
def test_limits_defaults_and_validation():
    assert jp.MetadataLimits.MAX_CONCURRENT == 1
    assert jp.MetadataLimits.CHILD_RSS_LIMIT_MIB == 1024
    assert jp.MetadataLimits.JOB_TIMEOUT == 1800
    assert jp.MetadataLimits.START_TIMEOUT == 30
    assert jp.validate_limits() == []


def test_limits_out_of_range_rejected():
    """参数越界 → validate_limits 报错（fail-closed）。"""
    orig = jp.MetadataLimits.CHILD_RSS_LIMIT_MIB
    try:
        jp.MetadataLimits.CHILD_RSS_LIMIT_MIB = 511   # 低于下界 512
        errs = jp.validate_limits()
        assert any("CHILD_RSS_LIMIT_MIB" in e for e in errs)
    finally:
        jp.MetadataLimits.CHILD_RSS_LIMIT_MIB = orig


def test_precheck_runner_start_present():
    """25% MemTotal 预检函数存在并返回 (ok, reasons)；本机内存足够应通过。"""
    ok, reasons = jp.precheck_runner_start()
    assert isinstance(ok, bool) and isinstance(reasons, list)
    # 本机（开发机内存足够）应通过；且 1024MiB*4=4GiB < 主机总量
    assert ok, f"本机预检应通过: {reasons}"


def test_precheck_rejects_when_rss_over_25pct(monkeypatch):
    """RSS 上限 * 4 > 有效容量 → 预检拒绝（25% 规则）。"""
    monkeypatch.setattr(jp.MetadataLimits, "CHILD_RSS_LIMIT_MIB", 4096)
    # 注入一个很小的有效容量，触发 25% 拒绝
    monkeypatch.setattr(jp, "effective_capacity_bytes", lambda: 1024 * 1024 * 1024)  # 1 GiB
    ok, reasons = jp.precheck_runner_start()
    assert ok is False
    assert any("25%" in r for r in reasons)


def test_disk_check_before_accept():
    """磁盘受理前置检查存在；本机可用空间充足应通过。"""
    from backend.services import metadata_artifacts as art
    ok, code, msg = jp.check_disk_before_accept(str(art.artifact_root()))
    assert ok is True and code is None


def test_disk_check_insufficient(monkeypatch):
    """可用空间不足 → 507 INSUFFICIENT_STORAGE。"""
    monkeypatch.setattr(jp, "disk_free_bytes", lambda p: 1024)  # 1 KB 必然不足
    ok, code, msg = jp.check_disk_before_accept("x")
    assert ok is False and code == "INSUFFICIENT_STORAGE"


def test_disk_check_unavailable(monkeypatch):
    """读不到可用空间 → STORAGE_CHECK_UNAVAILABLE。"""
    monkeypatch.setattr(jp, "disk_free_bytes", lambda p: None)
    ok, code, msg = jp.check_disk_before_accept("x")
    assert ok is False and code == "STORAGE_CHECK_UNAVAILABLE"


# ══ M-01：run_metadata_worker 捕获 stderr 尾部 ══
def test_run_metadata_worker_captures_stderr_on_failure():
    """子进程失败时，stderr 尾部被捕获（诊断线索，不再静默）。"""
    res = jp.run_metadata_worker(
        [sys.executable, "-c",
         "import sys; sys.stderr.write('BOOM_ROOT_CAUSE_XYZ\\n'); sys.exit(3)"],
        timeout=30)
    assert res.returncode == 3
    assert "BOOM_ROOT_CAUSE_XYZ" in res.stderr_tail
    assert res.cleanup_ok is True


def test_run_metadata_worker_timeout_kills():
    """超时子进程被终止回收（TERM/KILL），cleanup_ok 确认退出。"""
    res = jp.run_metadata_worker(
        [sys.executable, "-c", "import time; time.sleep(60)"], timeout=2)
    assert res.timed_out is True
    assert res.cleanup_ok is True


def test_run_metadata_worker_success():
    res = jp.run_metadata_worker([sys.executable, "-c", "print('ok')"], timeout=30)
    assert res.returncode == 0 and res.cleanup_ok is True


# ══ B-03：MetadataJobError → 稳定 HTTP 错误（非裸 500）══
def test_runner_not_ready_raises_503():
    """runner 未就绪时受理抛 MetadataJobError(503)，异常处理器转 503 而非 500。"""
    from backend.services.metadata_audit_repository import repository as repo
    from backend.api.metadata_audit import _check_runner_ready
    from backend.services.database import ensure_db, _get_connection
    ensure_db()
    # 把 slot 置为不接受（模拟 runner 未启动）
    conn = _get_connection()
    conn.execute("UPDATE metadata_audit_slot SET accepting=0 WHERE id=1")
    conn.commit(); conn.close()
    try:
        with pytest.raises(MetadataJobError) as ei:
            _check_runner_ready()
        assert ei.value.code == "EXECUTOR_UNAVAILABLE"
        assert ei.value.http_status == 503   # 是 503 而非 500
    finally:
        conn = _get_connection()
        conn.execute("UPDATE metadata_audit_slot SET accepting=1 WHERE id=1")
        conn.commit(); conn.close()


def test_main_has_metadata_job_error_handler():
    """main.py 注册了 MetadataJobError 异常处理器（B-03）。"""
    from backend.main import app
    handlers = app.exception_handlers
    assert MetadataJobError in handlers, "MetadataJobError 未注册异常处理器"


# ══ N-01：任务详情顶层暴露 error_code/error_message/exit_code ══
def test_job_summary_exposes_error_fields():
    from backend.api.metadata_audit import _job_summary
    job = {
        "id": "x" * 32, "state": "FAILED", "phase": "CLEANUP", "db_name": "db1",
        "execution_context_json": '{"connection_name":"n"}',
        "error_code": "CHILD_EXITED", "error_message": "子进程退出码 3", "exit_code": 3,
        "progress_json": None, "started_at": None, "finished_at": None,
        "created_at": None, "report_id": None, "snapshot_id": None, "cleanup_ok": 1,
    }
    s = _job_summary(job)
    assert s["error_code"] == "CHILD_EXITED"
    assert s["error_message"] == "子进程退出码 3"
    assert s["exit_code"] == 3
