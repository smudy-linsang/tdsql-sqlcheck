# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：场景路由与主备策略（CP-W05，DETAIL §7.2）。

每 scene 配置主 provider ＋最多 1 个 fallback，同 data_zone 及相同/更严格
privacy_profile。未配置 route 直接 LOCAL_ONLY，不遍历全局所有凭据。
切换条件闭集：连接建立失败、HTTP429、HTTP502/503/504；每轮最多 2 次 HTTP
调用，合计 ≤60 秒且受整体 deadline 约束。401/403/404、TLS 失败、端点策略
失败、输出不合规：停止该路径并本地降级。超时不自动重试。

429 cooldown 取合法 Retry-After（夹 5—300 秒，无头默认 60 秒）；连续 3 次
连接/5xx 失败熔断 60 秒。健康状态落库以支持多进程共享。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.services.copilot.errors import CopilotError
from backend.services.copilot.policy import EndpointPolicy
from backend.services.copilot.repository import ProviderRepo, RouteRepo

logger = logging.getLogger("tdsql.copilot.routing")

_SWITCHABLE_ERRORS = frozenset({
    "PROVIDER_CONNECT_FAILED", "PROVIDER_RATE_LIMITED", "PROVIDER_UNAVAILABLE",
})
_STOP_ERRORS = frozenset({
    "PROVIDER_AUTH_FAILED", "PROVIDER_REQUEST_REJECTED", "PROVIDER_TLS_FAILED",
    "EGRESS_DENIED", "OUTPUT_INVALID", "OUTPUT_TRUNCATED", "OUTPUT_SENSITIVE",
})
MAX_ATTEMPTS_PER_TURN = 2


def _parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if hasattr(value, "timestamp"):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def provider_in_cooldown(provider: dict) -> bool:
    cu = _parse_dt(provider.get("cooldown_until"))
    if cu is None:
        return False
    now = datetime.now(timezone.utc)
    if cu.tzinfo is None:
        cu = cu.replace(tzinfo=timezone.utc)
    return cu > now


def resolve_route(conn, scene: str, policy: EndpointPolicy) -> Optional[dict]:
    """解析场景路由：返回 {route, primary, fallback}；未配置/本地模式返回 None。

    主备必须同批准数据域且隐私配置相同/更严格；fallback 不满足时置 None
    （不在故障转移时偷偷缩减/放宽投影，也不跨域）。
    """
    route = RouteRepo.get(conn, scene)
    if route is None or not int(route.get("enabled") or 0):
        return None
    primary_id = route.get("primary_provider_id")
    if not primary_id:
        return None  # 空主 provider 即本地模式
    primary = ProviderRepo.get(conn, primary_id)
    if primary is None or not int(primary.get("enabled") or 0):
        return None
    primary_ep = policy.get(primary["endpoint_id"])
    if primary_ep is None:
        return None
    fallback = None
    fb_id = route.get("fallback_provider_id")
    if fb_id and fb_id != primary_id:
        fb = ProviderRepo.get(conn, fb_id)
        if fb and int(fb.get("enabled") or 0):
            fb_ep = policy.get(fb["endpoint_id"])
            if fb_ep is not None and fb_ep["data_zone"] == primary_ep["data_zone"] \
                    and fb_ep["privacy_profile"] == primary_ep["privacy_profile"]:
                fallback = fb
            else:
                logger.warning("fallback provider 数据域/隐私配置不一致，已禁用: %s",
                               fb_id)
    return {"route": route, "primary": primary, "fallback": fallback}


def can_switch(error_code: str) -> bool:
    return error_code in _SWITCHABLE_ERRORS


def must_stop(error_code: str) -> bool:
    return error_code in _STOP_ERRORS


def record_failure(conn, provider: dict, error_code: str,
                   retry_after_seconds: Optional[int] = None) -> None:
    """健康落库：429 按 Retry-After 冷却；连续 3 次连接/5xx 失败熔断 60 秒。"""
    failures = int(provider.get("consecutive_failures") or 0) + 1
    cooldown_until = None
    now = datetime.now(timezone.utc)
    if error_code == "PROVIDER_RATE_LIMITED":
        seconds = retry_after_seconds if retry_after_seconds is not None else 60
        seconds = max(5, min(300, seconds))
        cooldown_until = (now + timedelta(seconds=seconds)).strftime(
            "%Y-%m-%d %H:%M:%S.%f")
    elif error_code in ("PROVIDER_CONNECT_FAILED", "PROVIDER_UNAVAILABLE") \
            and failures >= 3:
        cooldown_until = (now + timedelta(seconds=60)).strftime(
            "%Y-%m-%d %H:%M:%S.%f")
    ProviderRepo.record_health(conn, provider["id"], failures, cooldown_until,
                               error_code)
    conn.commit()


def record_success(conn, provider: dict) -> None:
    ProviderRepo.record_health(conn, provider["id"], 0, None, None)
    conn.commit()


def route_snapshot(route_info: Optional[dict]) -> dict:
    """受理时冻结的路由/批准策略快照（写入 turn.route_snapshot_envelope）。"""
    if route_info is None:
        return {"mode": "LOCAL_ONLY"}
    route = route_info["route"]
    return {
        "mode": "ROUTE",
        "scene_code": route["scene_code"],
        "route_revision": route["revision"],
        "primary": {"provider_id": route_info["primary"]["id"],
                    "provider_revision": route_info["primary"]["revision"],
                    "endpoint_id": route_info["primary"]["endpoint_id"],
                    "model_id": route_info["primary"]["model_id"]},
        "fallback": ({"provider_id": route_info["fallback"]["id"],
                      "provider_revision": route_info["fallback"]["revision"],
                      "endpoint_id": route_info["fallback"]["endpoint_id"],
                      "model_id": route_info["fallback"]["model_id"]}
                     if route_info.get("fallback") else None),
    }
