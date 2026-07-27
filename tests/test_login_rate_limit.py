"""
登录 IP 级限流（S09）

关键点：生产以 --workers 2 运行（deploy/tdsql-sqlcheck.service），
若计数放在进程内字典，两个 worker 各算各的，配 15 实际约等于 30，
运维按配置值评估 NAT 出口误伤阈值会算错。故计数源必须是共享的
operation_logs，本用例直接验证"另一个进程写入的失败也会被算进来"。
"""
import time

import pytest

from backend import config
from backend.services import auth_service as A
from backend.services.database import _get_connection, ensure_db, log_operation

_IP = "203.0.113.77"          # TEST-NET-3，不会与真实来源冲突
_IP2 = "203.0.113.78"


def _clear(ip):
    ensure_db()
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM operation_logs WHERE ip_address = ?", (ip,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _isolate():
    for ip in (_IP, _IP2):
        _clear(ip)
    A._ip_fail.clear()
    yield
    for ip in (_IP, _IP2):
        _clear(ip)
    A._ip_fail.clear()


def _write_failures(ip, n):
    """模拟另一个 worker 写入的失败记录（不经过本进程的内存计数）"""
    for _ in range(n):
        log_operation(f"probe_{ip}", "login_failed", "user", "probe",
                      "口令错误", ip)


def test_counts_failures_written_by_another_process():
    """核心：本进程内存计数为空，仅凭共享表中的失败即应触发限流"""
    limit = config.login_ip_fail_limit()
    assert limit > 0, "该用例要求限流处于开启状态"

    _write_failures(_IP, limit - 1)
    assert A._ip_fail == {}, "前置条件：本进程内存计数应为空"
    assert not A.ip_rate_limited(_IP), "未达阈值不应限流"

    _write_failures(_IP, 1)
    assert A.ip_rate_limited(_IP), (
        "共享计数未生效：其它 worker 写入的失败没有被算进来，"
        "多 worker 下实际阈值会翻倍")


def test_success_resets_counter():
    """成功登录即清零：NAT 出口后有人正常登录成功就不该持续累积"""
    limit = config.login_ip_fail_limit()
    _write_failures(_IP, limit)
    assert A.ip_rate_limited(_IP)

    log_operation("probe", "login_success", "user", "probe", "", _IP)
    time.sleep(1.1)  # created_at 精度为秒，确保成功记录晚于失败记录
    assert not A.ip_rate_limited(_IP), "成功登录后应清零"


def test_limit_is_per_ip():
    """限流按来源 IP 隔离，不得殃及其它来源"""
    _write_failures(_IP, config.login_ip_fail_limit())
    assert A.ip_rate_limited(_IP)
    assert not A.ip_rate_limited(_IP2)


def test_disabled_when_limit_zero(monkeypatch):
    monkeypatch.setattr(config, "login_ip_fail_limit", lambda: 0)
    _write_failures(_IP, 50)
    assert not A.ip_rate_limited(_IP), "阈值置 0 应完全关闭限流"


def test_falls_back_to_memory_when_db_unavailable(monkeypatch):
    """元数据库不可用时降级为进程内计数，而不是放弃限流或阻断登录"""
    monkeypatch.setattr(A, "_count_recent_failures", lambda ip, w: None)
    limit = config.login_ip_fail_limit()
    for _ in range(limit):
        A.record_ip_failure(_IP)
    assert A.ip_rate_limited(_IP), "降级路径未生效"


def test_empty_ip_never_limited():
    """取不到来源 IP 时不限流，避免误伤全部请求"""
    assert not A.ip_rate_limited("")
