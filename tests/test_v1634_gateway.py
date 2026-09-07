# -*- coding: utf-8 -*-
"""v1.6.3.4 / D05+D06 网关上传防护 SIT 回归（DETAIL-v1.6.3.4 §8.4 GW-C1—C4、GW-T1—T2）。

含 **B-02 回归锁**：429/413 两条路径断言 响应头 X-Request-ID == 响应体 detail.request_id
（用户报障编号必须与访问日志/响应头一致，否则对不上账）。

锁语义用真实 GatewayUploadSlot（临时目录隔离）验证"未取槽绝不释放他人锁"。
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend import config
from backend import middleware as mw
from backend.services import gateway_upload_lock as gul

client = TestClient(app)

UPLOAD_PATH = "/api/v1/gateway-log/upload"
SAMPLE = (b"[2026-02-26 00:00:01 1] INFO topic=test&timecost=12.5&"
          b"sql=select 1&db=b&user=root&host=127.0.0.1\n")


def _upload():
    files = {"file": ("interf_test.log", SAMPLE, "text/plain")}
    data = {"connection_id": "test_conn", "log_type": "interf"}
    return client.post(UPLOAD_PATH, data=data, files=files)


# ════════════════════════════════════════════════════════════════════════════
# GW-C1：忙时第二个上传非阻塞 429；B-02 锁——头体请求编号一致
# ════════════════════════════════════════════════════════════════════════════
def test_gw_c1_busy_returns_429_and_request_id_consistent(monkeypatch):
    # 模拟另一 worker 持锁：try_acquire 恒 False，peek_retry_after 返回合法有界值
    monkeypatch.setattr(mw.GatewayUploadSlot, "try_acquire", lambda self, nonce: False)
    monkeypatch.setattr(mw.GatewayUploadSlot, "peek_retry_after", lambda self: 598)
    resp = _upload()
    assert resp.status_code == 429
    body = resp.json()["detail"]
    assert body["code"] == "GATEWAY_BUSY"
    assert body["stage"] == "admission"
    assert body["retryable"] is True
    assert "未进入分析" in body["message"]
    # Retry-After 头存在且为数字
    assert "retry-after" in resp.headers
    assert resp.headers["retry-after"].isdigit()
    # ★ B-02 回归锁：响应头与响应体的请求编号必须一致
    assert resp.headers["x-request-id"] == body["request_id"]


def test_gw_c1_busy_is_non_blocking(monkeypatch):
    # 429 应立即返回（不等待第一任务完成）——用一个很快的断言佐证非阻塞语义
    import time
    monkeypatch.setattr(mw.GatewayUploadSlot, "try_acquire", lambda self, nonce: False)
    monkeypatch.setattr(mw.GatewayUploadSlot, "peek_retry_after", lambda self: 300)
    t0 = time.monotonic()
    resp = _upload()
    assert resp.status_code == 429
    assert (time.monotonic() - t0) < 5.0, "429 必须非阻塞快速返回"


# ════════════════════════════════════════════════════════════════════════════
# GW-C2：取槽前 413（Content-Length 超 multipart 上限）；B-02 锁；锁归属语义
# ════════════════════════════════════════════════════════════════════════════
def test_gw_c2_oversize_413_request_id_consistent(monkeypatch):
    # 收紧 multipart 总上限到 64 字节，使本请求的 Content-Length 超限 → 413
    monkeypatch.setenv("GATEWAY_REQUEST_MAX_BYTES", "64")
    resp = _upload()
    assert resp.status_code == 413
    body = resp.json()["detail"]
    assert body["code"] == "GATEWAY_UPLOAD_TOO_LARGE"
    assert body["stage"] == "admission"
    # ★ B-02 回归锁：413 路径头体编号也必须一致
    assert resp.headers["x-request-id"] == body["request_id"]


def test_gw_c2_non_holder_never_releases_others_lock(tmp_path):
    """未取到槽的请求 release() 绝不释放他人锁（O 坚持的写法，A 评审纠正点）。"""
    a = gul.GatewayUploadSlot(str(tmp_path))
    b = gul.GatewayUploadSlot(str(tmp_path))
    c = gul.GatewayUploadSlot(str(tmp_path))
    assert a.try_acquire(gul.new_owner_nonce()) is True     # A 取槽
    assert b.try_acquire(gul.new_owner_nonce()) is False    # B 取不到
    b.release()                                             # B 未持槽，release 不得动 A 的锁
    assert c.try_acquire(gul.new_owner_nonce()) is False    # C 仍取不到（A 未被误释放）
    a.release()                                             # A 正常释放
    assert c.try_acquire(gul.new_owner_nonce()) is True     # C 现在能取到
    c.release()


# ════════════════════════════════════════════════════════════════════════════
# GW-C3：持槽阶段 Retry-After 有界（5—600 秒），无法估计回退 600
# ════════════════════════════════════════════════════════════════════════════
def test_gw_c3_retry_after_bounded(tmp_path):
    slot = gul.GatewayUploadSlot(str(tmp_path))
    assert slot.try_acquire(gul.new_owner_nonce()) is True
    slot.update_stage(gul.STAGE_PROCESSING, None)  # 无 deadline → 回退
    ra = slot.peek_retry_after()
    assert 5 <= ra <= 600, f"Retry-After 超出 5—600 有界区间: {ra}"
    slot.release()


# ════════════════════════════════════════════════════════════════════════════
# GW-C4：并发固定常量 1，0/2/5 等非法值一律拒绝；有效 1 行为不变
# ════════════════════════════════════════════════════════════════════════════
def test_gw_c4_concurrent_is_fixed_one():
    base = config.gateway_upload_config()
    for bad in (0, 2, 5):
        cfg = dict(base)
        cfg["GATEWAY_MAX_CONCURRENT"] = bad
        problems = config.validate_gateway_config(cfg)
        assert any("GATEWAY_MAX_CONCURRENT" in p for p in problems), \
            f"并发={bad} 未被拒绝"
        assert any("固定常量 1" in p for p in problems)
    # 有效 1：默认配置校验通过（行为不变）
    assert config.validate_gateway_config(base) == []


def test_gw_c4_capabilities_marks_concurrency_fixed():
    resp = client.get("/api/v1/gateway-log/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert data["concurrency_fixed"] is True
    assert data["max_concurrent"] == 1
    assert data["config_version"]


# ════════════════════════════════════════════════════════════════════════════
# GW-T1：超时链不等式逐项违例 → 启动校验拒绝并指名配置项；direct 只豁免 proxy 约束
# ════════════════════════════════════════════════════════════════════════════
def test_gw_t1_analysis_processing_inequality():
    base = config.gateway_upload_config()
    cfg = dict(base)
    cfg["GATEWAY_ANALYSIS_TIMEOUT_SECONDS"] = 540
    cfg["GATEWAY_PROCESSING_BUDGET_SECONDS"] = 545   # 540+10 >= 545 违反余量
    problems = config.validate_gateway_config(cfg)
    assert any("PROCESSING_BUDGET" in p for p in problems)


def test_gw_t1_receive_browser_inequality():
    base = config.gateway_upload_config()
    cfg = dict(base)
    cfg["GATEWAY_BROWSER_WAIT_SECONDS"] = 800        # 300+600+10=910 > 800
    problems = config.validate_gateway_config(cfg)
    assert any("BROWSER_WAIT" in p for p in problems)


def test_gw_t1_non_positive_rejected():
    base = config.gateway_upload_config()
    cfg = dict(base)
    cfg["GATEWAY_UPLOAD_RECEIVE_TIMEOUT_SECONDS"] = -1
    assert any("RECEIVE_TIMEOUT" in p for p in config.validate_gateway_config(cfg))
    # 值类型错误（字符串）也拒绝
    cfg2 = dict(base)
    cfg2["GATEWAY_ANALYSIS_TIMEOUT_SECONDS"] = "540"
    assert config.validate_gateway_config(cfg2) != []


def test_gw_t1_request_max_must_cover_upload_max():
    base = config.gateway_upload_config()
    cfg = dict(base)
    cfg["GATEWAY_REQUEST_MAX_BYTES"] = 100
    cfg["GATEWAY_UPLOAD_MAX_BYTES"] = 200
    assert any("REQUEST_MAX_BYTES" in p for p in config.validate_gateway_config(cfg))


def test_gw_t1_proxy_declared_constraints_and_direct_exemption():
    base = config.gateway_upload_config()
    # proxy 模式：declared < processing+10 → 拒绝
    cfg = dict(base); cfg["GATEWAY_DEPLOYMENT_MODE"] = "proxy"
    cfg["GATEWAY_DECLARED_PROXY_READ_TIMEOUT_SECONDS"] = 605
    assert any("DECLARED_PROXY" in p for p in config.validate_gateway_config(cfg))
    # proxy 模式：declared > browser_wait → 拒绝
    cfg = dict(base); cfg["GATEWAY_DEPLOYMENT_MODE"] = "proxy"
    cfg["GATEWAY_DECLARED_PROXY_READ_TIMEOUT_SECONDS"] = 1200
    assert any("DECLARED_PROXY" in p for p in config.validate_gateway_config(cfg))
    # direct 模式：只豁免 proxy 声明约束（不豁免自身预算），proxy 声明值离谱不报 DECLARED_PROXY
    cfg = dict(base); cfg["GATEWAY_DEPLOYMENT_MODE"] = "direct"
    cfg["GATEWAY_DECLARED_PROXY_READ_TIMEOUT_SECONDS"] = 1
    assert not any("DECLARED_PROXY" in p for p in config.validate_gateway_config(cfg))


# ════════════════════════════════════════════════════════════════════════════
# GW-T2：capabilities 与配置一致（漂移门禁的下发侧）；真实代理漂移留部署核验
# ════════════════════════════════════════════════════════════════════════════
def test_gw_t2_capabilities_match_config():
    resp = client.get("/api/v1/gateway-log/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    cfg = config.gateway_upload_config()
    # capabilities 下发值与当前配置一致（前端必须用返回值，不写死魔数）
    assert data["browser_wait_seconds"] == cfg["GATEWAY_BROWSER_WAIT_SECONDS"]
    assert data["upload_max_bytes"] == cfg["GATEWAY_UPLOAD_MAX_BYTES"]
    assert data["request_max_bytes"] == cfg["GATEWAY_REQUEST_MAX_BYTES"]
    # 默认 direct 模式
    assert data["deployment_mode"] in ("direct", "proxy")
