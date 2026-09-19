# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：Copilot 管理端 API（CP-W07，DETAIL §12.5）。

前缀 /api/v1/copilot-admin。全部要求 role=admin 且具 copilot-admin 菜单
（check_permission 前缀分支 + 本层显式断言双保险）。
配置变更与审计同事务提交；凭据只写不读（GET 只回 has_secret）。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

import ipaddress
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.models.copilot import (
    EndpointCreateRequest, EndpointUpdateRequest,
    GrantActionItem, GrantApproveRequest, GrantBatchActionRequest, GrantBatchApproveRequest,
    GrantPutRequest, ProviderCreateRequest, ProviderEnabledRequest, ProviderUpdateRequest,
    RoutePutRequest, SelfTestRequest, SettingsPutRequest,
)
from backend.services.copilot import schema as schema_mod
from backend.services.copilot.schema import guard_structural
from backend.services.copilot.authz import (
    has_copilot_admin, require_two_admin_gate, resolve_identity,
)
from backend.services.copilot.errors import CopilotError, error_payload
from backend.services.copilot.policy import load_policy, policy_available
from backend.services.copilot.repository import (
    AuditRepo, GrantRepo, ProviderRepo, RouteRepo, RuntimeRepo, SubjectRepo,
    new_id,
)
from backend.services.database import _get_connection, ensure_db

logger = logging.getLogger("tdsql.copilot.admin")

router = APIRouter(prefix="/api/v1/copilot-admin", tags=["CopilotAdmin"])

_SETTINGS_KEYS = ("enabled", "allow_schema_identifiers", "user_daily_tokens",
                  "global_daily_tokens", "session_retention_days",
                  "audit_retention_days")


def _rid() -> str:
    return uuid.uuid4().hex


def _err(code: str, message: Optional[str] = None, status_code: Optional[int] = None) -> JSONResponse:
    from backend.services.copilot.errors import http_status_of
    sc = status_code or (400 if code in ("ENDPOINT_IN_USE", "INVALID_REQUEST", "DUPLICATE_ENDPOINT") else http_status_of(code))
    return JSONResponse(status_code=sc,
                        content=error_payload(code, _rid(), message))


def _admin_identity(request: Request):
    identity = resolve_identity(request)
    if not has_copilot_admin(identity.role):
        raise CopilotError("FORBIDDEN", message="需要 copilot-admin 管理权限")
    return identity


def _require_ready(conn) -> None:
    ready, _r, _st = schema_mod.evaluate_ready(conn)
    if not ready:
        raise CopilotError("COPILOT_SCHEMA_UNAVAILABLE")




# ══════════════════════════════════════════════════════════════════
# 端点管理（管理员受控配置，支持内网直接配置 IP / 端口 / 模型服务）
# ══════════════════════════════════════════════════════════════════

@router.get("/endpoints")
@guard_structural
def list_endpoints(request: Request):
    try:
        _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    if not policy_available():
        return {"request_id": _rid(), "items": [],
                "policy_available": False}
    
    # 统计每个端点正在被哪些模型使用
    usage_map = {}
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        providers = ProviderRepo.list(conn)
        for p in providers:
            eid = p.get("endpoint_id")
            if eid:
                usage_map.setdefault(eid, []).append(p.get("name") or p.get("id"))
    except Exception:
        usage_map = {}
    finally:
        conn.close()

    items = load_policy().list_public_view()
    for item in items:
        item["used_by_providers"] = usage_map.get(item["endpoint_id"], [])

    return {"request_id": _rid(),
            "items": items,
            "policy_available": True}


@router.post("/endpoints", status_code=201)
@guard_structural
def create_endpoint(request: Request, body: EndpointCreateRequest):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)

    pol = load_policy()
    if pol.get(body.endpoint_id) is not None:
        return _err("INVALID_REQUEST", f"端点标识 {body.endpoint_id} 已存在，请换一个名称")

    cidrs = body.allowed_resolved_cidrs or []
    from backend.services.copilot.policy import _validate_cidr
    if not cidrs:
        try:
            ip = ipaddress.ip_address(body.canonical_host)
            err = _validate_cidr(f"{ip}/32")
            if err:
                return _err("INVALID_REQUEST", f"端点地址 [{body.canonical_host}] 属于系统禁止访问的网段（回环/保留地址），拒绝配置: {err}")
            cidrs = [f"{ip}/32"]
        except ValueError:
            # [INV-03 出网面豁免说明]
            # 此处使用 standard library `socket` 仅用于配置期对用户填写的 canonical_host 进行本地 DNS 解析 (socket.gethostbyname)
            # 并设置了严格的 3.0 秒超时 (socket.setdefaulttimeout(3.0))。
            # 目的为提取目标 IP 以收敛 allowed_resolved_cidrs 白名单及拦截回环/保留地址，不建立任何外部网络 Socket 连接或 I/O，
            # 符合行内安全合规要求。
            import socket
            resolved_ip = None
            orig_timeout = socket.getdefaulttimeout()
            try:
                socket.setdefaulttimeout(3.0)
                resolved_ip = socket.gethostbyname(body.canonical_host)
            except Exception:
                pass
            finally:
                try:
                    socket.setdefaulttimeout(orig_timeout)
                except Exception:
                    pass

            if resolved_ip:
                from backend.services.copilot.policy import _validate_cidr
                err = _validate_cidr(f"{resolved_ip}/32")
                if err:
                    return _err("INVALID_REQUEST", f"端点域名 [{body.canonical_host}] 解析到的 IP 地址 [{resolved_ip}] 属于禁止网段（回环/保留地址），拒绝配置: {err}")
                cidrs = [f"{resolved_ip}/32"]
            else:
                cidrs = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    else:
        from backend.services.copilot.policy import _validate_cidr
        for c in cidrs:
            err = _validate_cidr(str(c))
            if err:
                return _err("INVALID_REQUEST", f"端点批准 CIDR [{c}] 校验未通过: {err}")

    base_path = body.base_path or "/"
    scheme = body.scheme.lower()

    ep_data = {
        "endpoint_id": body.endpoint_id,
        "scheme": scheme,
        "canonical_host": body.canonical_host.strip(),
        "port": body.port,
        "base_path": base_path,
        "data_zone": body.data_zone,
        "privacy_profile": body.privacy_profile,
        "allows_schema_identifiers": body.allows_schema_identifiers,
        "allowed_resolved_cidrs": cidrs,
        "tls_ca_ref": body.tls_ca_ref or "internal",
        "description": body.description or "",
    }
    if scheme == "http":
        ep_data["allow_http"] = True
        logger.info("端点 %s 使用明文 HTTP 协议，已确认在内网受控环境中使用", body.endpoint_id)
    elif body.allow_http is not None:
        ep_data["allow_http"] = body.allow_http

    try:
        pol.upsert(ep_data)
    except CopilotError as e:
        return _err(e.code, e.message)
    except Exception as e:
        return _err("INVALID_REQUEST", f"端点配置校验失败: {e}")

    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        AuditRepo.record(conn, "CONFIG_CHANGE", identity.username,
                         identity.subject_id, "copilot_endpoints", body.endpoint_id,
                         "CREATE", detail=ep_data)
        conn.commit()
    except Exception as e:
        logger.warning("端点创建审计记录写入异常: %s", e)
    finally:
        conn.close()

    return JSONResponse(status_code=201, content={
        "request_id": _rid(),
        "endpoint": ep_data
    })


@router.put("/endpoints/{endpoint_id}")
@guard_structural
def update_endpoint(request: Request, endpoint_id: str, body: EndpointUpdateRequest):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)

    pol = load_policy()
    existing = pol.get(endpoint_id)
    if existing is None:
        return _err("NOT_FOUND", f"端点 {endpoint_id} 不存在")

    ep_data = dict(existing)
    if body.scheme is not None:
        ep_data["scheme"] = body.scheme.lower()
    if body.canonical_host is not None:
        ep_data["canonical_host"] = body.canonical_host.strip()
    if body.port is not None:
        ep_data["port"] = body.port
    if body.base_path is not None:
        ep_data["base_path"] = body.base_path or "/"
    if body.data_zone is not None:
        ep_data["data_zone"] = body.data_zone
    if body.privacy_profile is not None:
        ep_data["privacy_profile"] = body.privacy_profile
    if body.allows_schema_identifiers is not None:
        ep_data["allows_schema_identifiers"] = body.allows_schema_identifiers
    if body.allowed_resolved_cidrs is not None and len(body.allowed_resolved_cidrs) > 0:
        ep_data["allowed_resolved_cidrs"] = body.allowed_resolved_cidrs
    if body.tls_ca_ref is not None:
        ep_data["tls_ca_ref"] = body.tls_ca_ref
    if body.description is not None:
        ep_data["description"] = body.description

    if ep_data.get("scheme") == "http":
        ep_data["allow_http"] = True
    elif body.allow_http is not None:
        ep_data["allow_http"] = body.allow_http

    try:
        pol.upsert(ep_data)
    except CopilotError as e:
        return _err(e.code, e.message)
    except Exception as e:
        return _err("INVALID_REQUEST", f"端点配置校验失败: {e}")

    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        AuditRepo.record(conn, "CONFIG_CHANGE", identity.username,
                         identity.subject_id, "copilot_endpoints", endpoint_id,
                         "UPDATE", detail=ep_data)
        conn.commit()
    except Exception as e:
        logger.warning("端点更新审计记录写入异常: %s", e)
    finally:
        conn.close()

    return {
        "request_id": _rid(),
        "endpoint": ep_data
    }


@router.delete("/endpoints/{endpoint_id}")
@guard_structural
def delete_endpoint(request: Request, endpoint_id: str):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)

    pol = load_policy()
    existing = pol.get(endpoint_id)
    if existing is None:
        return _err("NOT_FOUND", f"端点 {endpoint_id} 不存在")

    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        providers = ProviderRepo.list(conn)
        used_by = [p["name"] for p in providers if p.get("endpoint_id") == endpoint_id]
        if used_by:
            return _err("ENDPOINT_IN_USE", f"端点已被模型【{', '.join(used_by)}】使用，请先解除绑定或修改对应模型后再删除")

        pol.remove(endpoint_id)
        AuditRepo.record(conn, "CONFIG_CHANGE", identity.username,
                         identity.subject_id, "copilot_endpoints", endpoint_id,
                         "DELETE", detail={"endpoint_id": endpoint_id})
        conn.commit()
    finally:
        conn.close()

    return {
        "request_id": _rid(),
        "deleted": endpoint_id
    }


# ══════════════════════════════════════════════════════════════════
# providers 管理
# ══════════════════════════════════════════════════════════════════

@router.get("/providers")
@guard_structural
def list_providers(request: Request):
    try:
        _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        items = []
        for p in ProviderRepo.list(conn):
            items.append({
                "id": p["id"], "name": p["name"], "endpoint_id": p["endpoint_id"],
                "protocol": p["protocol"], "model_id": p["model_id"],
                "auth_mode": p["auth_mode"],
                "capabilities": json.loads(p.get("capabilities_json") or "{}"),
                "enabled": bool(p.get("enabled")),
                "revision": int(p.get("revision") or 1),
                "tested_revision": p.get("tested_revision"),
                "has_secret": bool(p.get("has_secret")),
                "cooldown_until": str(p.get("cooldown_until") or "") or None,
                "consecutive_failures": int(p.get("consecutive_failures") or 0),
                "last_error_code": p.get("last_error_code"),
            })
        return {"request_id": _rid(), "items": items}
    finally:
        conn.close()


@router.post("/providers", status_code=201)
@guard_structural
def create_provider(request: Request, body: ProviderCreateRequest):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        policy = load_policy()
        if policy.get(body.endpoint_id) is None:
            raise CopilotError("PROVIDER_CONFIG_INVALID",
                               message="endpoint_id 不在部署批准清单中")
        caps = body.capabilities.model_dump()
        # context_tokens 必须 ≥ 输入上界＋输出上限＋1024
        from backend.services.copilot.policy import Limits
        need = Limits.get("COPILOT_INPUT_TOKEN_UPPER_BOUND") + \
            Limits.get("COPILOT_OUTPUT_MAX_TOKENS") + 1024
        if caps["context_tokens"] < need:
            raise CopilotError(
                "PROVIDER_CONFIG_INVALID",
                message=f"context_tokens 必须 ≥ {need}（输入上界＋输出上限＋1024）")
        if body.auth_mode == "NETWORK_IDENTITY":
            ep = policy.get(body.endpoint_id)
            if ep and ep.get("data_zone") != "INTERNAL":
                raise CopilotError(
                    "PROVIDER_CONFIG_INVALID",
                    message="NETWORK_IDENTITY 仅允许内网端点")
        secret_envelope = None
        if body.secret_action == "REPLACE" and body.secret:
            from backend.services.copilot import crypto as crypto_mod
            kr = crypto_mod.load_keyring()
            pid = new_id()
            secret_envelope = crypto_mod.encrypt(
                body.secret, "copilot_providers", pid, "secret_envelope",
                owner="SYSTEM", crypto_revision=1, keyring=kr)
        else:
            pid = new_id()
        # R3-N01：重名检查（先查再写，同时外层兜底 IntegrityError 防竞态）
        existing = conn.execute(
            "SELECT id FROM copilot_providers WHERE name = ?", (body.name,)).fetchone()
        if existing:
            raise CopilotError("INVALID_REQUEST",
                               message="模型名称已存在，请换一个名称")
        ProviderRepo.insert(conn, {
            "id": pid, "name": body.name, "endpoint_id": body.endpoint_id,
            "protocol": body.protocol, "model_id": body.model_id,
            "auth_mode": body.auth_mode,
            "capabilities_json": json.dumps(caps, ensure_ascii=False),
            "secret_envelope": secret_envelope})
        AuditRepo.record(conn, "CONFIG_CHANGE", identity.username,
                         identity.subject_id, "copilot_providers", pid,
                         "CREATE", detail={"endpoint_id": body.endpoint_id,
                                           "model_id": body.model_id,
                                           "auth_mode": body.auth_mode})
        conn.commit()
        return JSONResponse(status_code=201, content={
            "request_id": _rid(), "id": pid, "enabled": False, "revision": 1})
    finally:
        conn.close()


@router.put("/providers/{provider_id}")
@guard_structural
def update_provider(request: Request, provider_id: str,
                    body: ProviderUpdateRequest):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        existing = ProviderRepo.get(conn, provider_id, for_update=True)
        if existing is None:
            raise CopilotError("NOT_FOUND")
        if int(existing["revision"]) != body.expected_revision:
            raise CopilotError("CONFIG_CHANGED")
        merged = {
            "id": provider_id,
            "name": body.name or existing["name"],
            "endpoint_id": body.endpoint_id or existing["endpoint_id"],
            "model_id": body.model_id or existing["model_id"],
            "auth_mode": body.auth_mode or existing["auth_mode"],
            "capabilities_json": json.dumps(
                body.capabilities.model_dump(), ensure_ascii=False)
            if body.capabilities else existing["capabilities_json"],
        }
        secret_envelope = existing.get("secret_envelope")
        new_revision = int(existing["revision"]) + 1
        if body.secret_action == "CLEAR":
            secret_envelope = None
        elif body.secret_action == "REPLACE" and body.secret:
            from backend.services.copilot import crypto as crypto_mod
            kr = crypto_mod.load_keyring()
            secret_envelope = crypto_mod.encrypt(
                body.secret, "copilot_providers", provider_id,
                "secret_envelope", owner="SYSTEM", crypto_revision=new_revision,
                keyring=kr)
        merged["secret_envelope"] = secret_envelope
        if not ProviderRepo.update_config(conn, merged, body.expected_revision):
            raise CopilotError("CONFIG_CHANGED")
        AuditRepo.record(conn, "CONFIG_CHANGE", identity.username,
                         identity.subject_id, "copilot_providers", provider_id,
                         "UPDATE", detail={"revision": new_revision})
        conn.commit()
        return {"request_id": _rid(), "id": provider_id, "enabled": False,
                "revision": new_revision, "tested_revision": None}
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# Provider 自检（§12.5/§12.6）
# ══════════════════════════════════════════════════════════════

@router.post("/providers/{provider_id}/self-tests", status_code=202)
@guard_structural
def start_provider_self_test(request: Request, provider_id: str,
                             body: SelfTestRequest):
    """§12.5/§12.6：自检实现为 turn_kind=PROVIDER_SELFTEST 的内部 turn。

    不发业务资料；主备尝试上限 1，不借 fallback；同键幂等 202/200。
    """
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        provider = ProviderRepo.get(conn, provider_id, for_update=True)
        if provider is None:
            raise CopilotError("NOT_FOUND")
        if int(provider["revision"]) != body.expected_provider_revision:
            raise CopilotError("CONFIG_CHANGED")
        from backend.services.copilot.selftest import admit_self_test
        turn = admit_self_test(conn, identity, provider,
                              body.client_request_id)
        conn.commit()
        return JSONResponse(status_code=202, content={
            "request_id": _rid(), "turn_id": turn["id"], "state": "ACCEPTED",
            "status_url": f"/api/v1/copilot/turns/{turn['id']}",
            "result_url": f"/api/v1/copilot/turns/{turn['id']}/result",
            "poll_after_ms": 2000, "reused": bool(turn.get("reused")),
            "notice": "自检可能产生少量模型调用费用；不发送任何业务资料。"})
    finally:
        conn.close()


@router.put("/providers/{provider_id}/enabled")
@guard_structural
def set_provider_enabled(request: Request, provider_id: str,
                         body: ProviderEnabledRequest):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        existing = ProviderRepo.get(conn, provider_id, for_update=True)
        if existing is None:
            raise CopilotError("NOT_FOUND")
        if int(existing["revision"]) != body.expected_revision:
            raise CopilotError("CONFIG_CHANGED")
        if body.enabled:
            # 启用要求同 revision 自检通过
            if existing.get("tested_revision") is None or \
                    int(existing["tested_revision"]) != int(existing["revision"]):
                raise CopilotError(
                    "PROVIDER_CONFIG_INVALID",
                    message="当前配置版本尚未通过自检，请先执行自检")
        if not ProviderRepo.set_enabled(conn, provider_id, body.enabled,
                                        body.expected_revision):
            raise CopilotError("CONFIG_CHANGED")
        AuditRepo.record(conn, "CONFIG_CHANGE", identity.username,
                         identity.subject_id, "copilot_providers", provider_id,
                         "ENABLE" if body.enabled else "DISABLE")
        conn.commit()
        return {"request_id": _rid(), "id": provider_id,
                "enabled": body.enabled,
                "revision": body.expected_revision + 1}
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 场景路由
# ══════════════════════════════════════════════════════════════════

_SCENES = ("USAGE_HELP", "RULE_EXPLAIN", "SQL_ADVISE", "AUDIT_EXPLAIN",
           "JOB_TROUBLESHOOT", "SLOW_EXPLAIN", "COMPARE_EXPLAIN",
           "TABLETYPE_EXPLAIN", "GATEWAY_EXPLAIN", "DIAGNOSTIC_HELP")


@router.get("/routes")
@guard_structural
def list_routes(request: Request):
    try:
        _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        return {"request_id": _rid(),
                "items": [{k: r[k] for k in
                           ("scene_code", "primary_provider_id",
                            "fallback_provider_id", "privacy_profile",
                            "revision", "enabled", "updated_by")}
                          for r in RouteRepo.list(conn)]}
    finally:
        conn.close()


@router.put("/routes/{scene}")
@guard_structural
def put_route(request: Request, scene: str, body: RoutePutRequest):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    scene = scene.upper()
    if scene not in _SCENES:
        return _err("INVALID_REQUEST", "未知场景")
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        if body.primary_provider_id and body.fallback_provider_id and \
                body.primary_provider_id == body.fallback_provider_id:
            raise CopilotError("INVALID_REQUEST",
                               message="主备 provider 不能相同")
        # 同数据域校验
        policy = load_policy()
        zones = set()
        for pid in (body.primary_provider_id, body.fallback_provider_id):
            if not pid:
                continue
            p = ProviderRepo.get(conn, pid)
            if p is None:
                raise CopilotError("PROVIDER_CONFIG_INVALID",
                                   message="provider 不存在")
            ep = policy.get(p["endpoint_id"])
            if ep is None:
                raise CopilotError("PROVIDER_CONFIG_INVALID",
                                   message="provider 端点不在批准清单")
            zones.add(ep["data_zone"])
        if len(zones) > 1:
            raise CopilotError("PROVIDER_CONFIG_INVALID",
                               message="主备必须在同一批准数据域")
        ok = RouteRepo.upsert(conn, scene, body.primary_provider_id,
                              body.fallback_provider_id, body.privacy_profile,
                              True, identity.username, body.expected_revision)
        if not ok:
            raise CopilotError("CONFIG_CHANGED")
        AuditRepo.record(conn, "CONFIG_CHANGE", identity.username,
                         identity.subject_id, "copilot_scene_routes", scene,
                         "ROUTE_UPDATE")
        conn.commit()
        route = RouteRepo.get(conn, scene)
        return {"request_id": _rid(), "scene_code": scene,
                "revision": int(route["revision"])}
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 实例授权（申请/复核分离）
# ══════════════════════════════════════════════════════════════════

@router.get("/grants")
@guard_structural
def list_grants(request: Request, limit: int = 50, offset: int = 0,
                connection_id: str = "", state: str = ""):
    try:
        _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        rows = GrantRepo.list_page(conn, min(max(1, limit), 100),
                                   max(0, offset), connection_id, state=state)
        conn_rows = conn.execute("SELECT id, name FROM tdsql_connections").fetchall()
        conn_name_map = {r["id"]: r["name"] for r in conn_rows}
        return {"request_id": _rid(), "items": [{
            "subject_id": g["subject_id"], "connection_id": g["connection_id"],
            "connection_name": conn_name_map.get(g["connection_id"], g["connection_id"]),
            "username": g["username"], "enabled": bool(g["enabled"]),
            "approval_state": g["approval_state"],
            "approval_ref": g["approval_ref"],
            "allow_schema_identifiers": bool(g["allow_schema_identifiers"]),
            "revision": int(g["revision"]),
        } for g in rows]}
    finally:
        conn.close()


@router.put("/grants")
@guard_structural
def put_grant(request: Request, body: GrantPutRequest):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)

        # 整理目标用户与目标连接列表（兼顾单填与多对多批量）
        raw_users = list(body.usernames or [])
        if body.username and body.username.strip():
            raw_users.append(body.username.strip())
        target_users = list(dict.fromkeys(u.strip() for u in raw_users if u.strip()))

        raw_conns = list(body.connection_ids or [])
        if body.connection_id and body.connection_id.strip():
            raw_conns.append(body.connection_id.strip())
        target_conns = list(dict.fromkeys(c.strip() for c in raw_conns if c.strip()))

        if not target_users:
            raise CopilotError("INVALID_REQUEST", message="目标用户不能为空")
        if not target_conns:
            raise CopilotError("INVALID_REQUEST", message="实例连接不能为空")

        # 预校验所有实例连接必须存在
        for cid in target_conns:
            conn_row = conn.execute(
                "SELECT id FROM tdsql_connections WHERE id = ?", (cid,)).fetchone()
            if not conn_row:
                raise CopilotError("INVALID_REQUEST", message=f"连接 {cid} 不存在")

        # 预校验并自动就绪目标用户的 Copilot 身份
        user_subjects = {}
        for uname in target_users:
            user_row = conn.execute(
                "SELECT username, role, status, created_at FROM users "
                "WHERE username = ?", (uname,)).fetchone()
            if not user_row:
                raise CopilotError("INVALID_REQUEST", message=f"目标用户 {uname} 不存在")
            subj = SubjectRepo.get_active_by_username(conn, uname)
            if not subj:
                sid = SubjectRepo.ensure_for_user(conn, uname, str(user_row["created_at"]))
                subj = {"subject_id": sid, "username": uname}
            user_subjects[uname] = subj["subject_id"]

        if body.intent == "DELETE":
            deleted_count = 0
            for uname in target_users:
                sid = user_subjects[uname]
                for cid in target_conns:
                    if GrantRepo.delete(conn, sid, cid):
                        AuditRepo.record(conn, "GRANT_DELETE", identity.username,
                                         identity.subject_id, "copilot_instance_grants",
                                         cid, "DELETE",
                                         detail={"target": uname})
                        deleted_count += 1
            conn.commit()
            return {"request_id": _rid(), "deleted": True, "count": deleted_count}

        if body.intent == "RESTORE":
            restored_count = 0
            for uname in target_users:
                sid = user_subjects[uname]
                for cid in target_conns:
                    if GrantRepo.restore(conn, sid, cid, identity.username, identity.subject_id):
                        AuditRepo.record(conn, "GRANT_RESTORE", identity.username,
                                         identity.subject_id, "copilot_instance_grants",
                                         cid, "RESTORE",
                                         detail={"target": uname})
                        restored_count += 1
            conn.commit()
            return {"request_id": _rid(), "restored": True, "count": restored_count}

        if body.intent == "REVOKE":
            revoked_count = 0
            for uname in target_users:
                sid = user_subjects[uname]
                for cid in target_conns:
                    if GrantRepo.revoke(conn, sid, cid):
                        AuditRepo.record(conn, "GRANT_CHANGE", identity.username,
                                         identity.subject_id, "copilot_instance_grants",
                                         cid, "REVOKE",
                                         detail={"target": uname})
                        revoked_count += 1
            conn.commit()
            return {"request_id": _rid(), "revoked": True, "count": revoked_count}

        if body.intent == "REQUEST":
            requested_count = 0
            for uname in target_users:
                sid = user_subjects[uname]
                for cid in target_conns:
                    GrantRepo.upsert_request(
                        conn, sid, cid, uname, identity.subject_id,
                        body.approval_ref, body.allow_schema_identifiers,
                        body.identifier_approval_ref)
                    AuditRepo.record(conn, "GRANT_REQUEST", identity.username,
                                     identity.subject_id, "copilot_instance_grants",
                                     cid, "REQUEST",
                                     detail={"target": uname})
                    requested_count += 1
            conn.commit()
            return {"request_id": _rid(), "approval_state": "PENDING",
                    "enabled": False, "count": requested_count}

        if body.intent == "GRANT":
            if identity.role not in ("admin", "copilot_admin"):
                raise CopilotError("FORBIDDEN", message="仅管理员可执行直接授权分配")
            # 管理员直接分配授权（即时生效，APPROVED + enabled=1）
            # 禁止自授权（B-03）：管理员不能直接为自己授权，必须由另一位管理员分配或复核
            for uname in target_users:
                sid = user_subjects[uname]
                if sid == identity.subject_id:
                    raise CopilotError("FORBIDDEN", message="管理员不能为自己直接分配授权，请由另一名管理员为您分配")

            granted_count = 0
            for uname in target_users:
                sid = user_subjects[uname]
                for cid in target_conns:
                    GrantRepo.direct_grant(
                        conn, sid, cid, uname,
                        identity.username, identity.subject_id, body.approval_ref,
                        body.allow_schema_identifiers, body.identifier_approval_ref)
                    AuditRepo.record(conn, "GRANT_APPROVE", identity.username,
                                     identity.subject_id, "copilot_instance_grants",
                                     cid, "APPROVE",
                                     detail={"target": uname, "direct_grant": True})
                    granted_count += 1
            conn.commit()
            return {"request_id": _rid(), "approval_state": "APPROVED",
                    "enabled": True, "count": granted_count}

        raise CopilotError("INVALID_REQUEST", message=f"不支持的意图: {body.intent}")
    finally:
        conn.close()


@router.post("/grants/restore")
@guard_structural
def restore_grant(request: Request, body: GrantBatchActionRequest):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        restored_count = 0
        for item in body.grants:
            sid = item.subject_id
            if not sid and item.username:
                subj = SubjectRepo.get_active_by_username(conn, item.username)
                if subj:
                    sid = subj["subject_id"]
            if sid and item.connection_id:
                if GrantRepo.restore(conn, sid, item.connection_id,
                                     identity.username, identity.subject_id):
                    AuditRepo.record(conn, "GRANT_RESTORE", identity.username,
                                     identity.subject_id, "copilot_instance_grants",
                                     item.connection_id, "RESTORE",
                                     detail={"target_subject": sid, "target_user": item.username or ""})
                    restored_count += 1
        conn.commit()
        return {"request_id": _rid(), "restored_count": restored_count}
    finally:
        conn.close()


@router.delete("/grants")
@guard_structural
def delete_grants(request: Request,
                  subject_id: Optional[str] = None,
                  connection_id: Optional[str] = None,
                  username: Optional[str] = None,
                  clear_revoked: Optional[bool] = False):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        if clear_revoked:
            count = GrantRepo.clear_revoked(conn)
            AuditRepo.record(conn, "GRANT_CLEANUP", identity.username,
                             identity.subject_id, "copilot_instance_grants",
                             "ALL", "CLEAR_REVOKED",
                             detail={"deleted_count": count})
            conn.commit()
            return {"request_id": _rid(), "deleted_count": count, "cleared_revoked": True}

        sid = subject_id
        if not sid and username:
            subj = SubjectRepo.get_active_by_username(conn, username)
            if subj:
                sid = subj["subject_id"]

        if sid and connection_id:
            deleted = GrantRepo.delete(conn, sid, connection_id)
            if deleted:
                AuditRepo.record(conn, "GRANT_DELETE", identity.username,
                                 identity.subject_id, "copilot_instance_grants",
                                 connection_id, "DELETE",
                                 detail={"target_subject": sid, "target_user": username or ""})
            conn.commit()
            return {"request_id": _rid(), "deleted": deleted, "deleted_count": 1 if deleted else 0}

        raise CopilotError("INVALID_REQUEST", message="缺少删除参数")
    finally:
        conn.close()


@router.post("/grants/batch-delete")
@guard_structural
def batch_delete_grants(request: Request, body: GrantBatchActionRequest):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        if body.clear_revoked_only:
            count = GrantRepo.clear_revoked(conn)
            AuditRepo.record(conn, "GRANT_CLEANUP", identity.username,
                             identity.subject_id, "copilot_instance_grants",
                             "ALL", "CLEAR_REVOKED",
                             detail={"deleted_count": count})
            conn.commit()
            return {"request_id": _rid(), "deleted_count": count, "cleared_revoked": True}

        deleted_count = 0
        for item in body.grants:
            sid = item.subject_id
            if not sid and item.username:
                subj = SubjectRepo.get_active_by_username(conn, item.username)
                if subj:
                    sid = subj["subject_id"]
            if sid and item.connection_id:
                if GrantRepo.delete(conn, sid, item.connection_id):
                    AuditRepo.record(conn, "GRANT_DELETE", identity.username,
                                     identity.subject_id, "copilot_instance_grants",
                                     item.connection_id, "DELETE",
                                     detail={"target_subject": sid, "target_user": item.username or ""})
                    deleted_count += 1
        conn.commit()
        return {"request_id": _rid(), "deleted_count": deleted_count}
    finally:
        conn.close()


@router.post("/grants/approve")
@guard_structural
def approve_grant(request: Request, body: GrantApproveRequest):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        require_two_admin_gate()
        grant = GrantRepo.get(conn, body.subject_id, body.connection_id,
                              for_update=True)
        if grant is None:
            raise CopilotError("NOT_FOUND")
        if int(grant["revision"]) != body.expected_revision:
            raise CopilotError("CONFIG_CHANGED")
        if grant["approval_state"] != "PENDING":
            raise CopilotError("INVALID_REQUEST",
                               message="仅 PENDING 状态可批准")
        # 复核人分离：复核人 ≠ 申请人 ≠ 被授权主体
        if grant["requested_by_subject_id"] == identity.subject_id:
            raise CopilotError("FORBIDDEN",
                               message="复核人不能是申请人")
        if grant["subject_id"] == identity.subject_id:
            raise CopilotError("FORBIDDEN",
                               message="复核人不能是被授权主体")
        if not GrantRepo.approve(conn, body.subject_id, body.connection_id,
                                 identity.username, identity.subject_id,
                                 body.expected_revision):
            raise CopilotError("CONFIG_CHANGED")
        AuditRepo.record(conn, "GRANT_APPROVE", identity.username,
                         identity.subject_id, "copilot_instance_grants",
                         body.connection_id, "APPROVE",
                         detail={"target_subject": body.subject_id})
        conn.commit()
        return {"request_id": _rid(), "approval_state": "APPROVED",
                "enabled": True}
    finally:
        conn.close()


@router.post("/grants/batch-approve")
@guard_structural
def batch_approve_grants(request: Request, body: GrantBatchApproveRequest):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        require_two_admin_gate()
        approved_count = 0
        errors = []
        for item in body.grants:
            grant = GrantRepo.get(conn, item.subject_id, item.connection_id,
                                  for_update=True)
            if grant is None:
                errors.append(f"{item.connection_id}: 授权不存在")
                continue
            if int(grant["revision"]) != item.expected_revision:
                errors.append(f"{item.connection_id}: 状态已变更")
                continue
            if grant["approval_state"] != "PENDING":
                errors.append(f"{item.connection_id}: 非 PENDING 状态")
                continue
            if grant["requested_by_subject_id"] == identity.subject_id:
                errors.append(f"{item.connection_id}: 复核人不能是申请人")
                continue
            if grant["subject_id"] == identity.subject_id:
                errors.append(f"{item.connection_id}: 复核人不能是被授权主体")
                continue
            if not GrantRepo.approve(conn, item.subject_id, item.connection_id,
                                     identity.username, identity.subject_id,
                                     item.expected_revision):
                errors.append(f"{item.connection_id}: 批准失败")
                continue
            AuditRepo.record(conn, "GRANT_APPROVE", identity.username,
                             identity.subject_id, "copilot_instance_grants",
                             item.connection_id, "APPROVE",
                             detail={"target_subject": item.subject_id})
            approved_count += 1
        conn.commit()
        return {"request_id": _rid(), "approved_count": approved_count, "errors": errors}
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# settings / health / feedback-summary
# ══════════════════════════════════════════════════════════════════

@router.get("/settings")
@guard_structural
def get_settings(request: Request):
    try:
        _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        rt = RuntimeRepo.get(conn) or {}
        settings = {}
        try:
            settings = json.loads(rt.get("settings_json") or "{}")
        except Exception:
            pass
        return {"request_id": _rid(), "settings": settings,
                "config_revision": int(rt.get("config_revision") or 1),
                "deploy_hard_gate": {
                    "COPILOT_ENABLED": __import__(
                        "backend.services.copilot.policy",
                        fromlist=["copilot_enabled"]).copilot_enabled(),
                }}
    finally:
        conn.close()


@router.put("/settings")
@guard_structural
def put_settings(request: Request, body: SettingsPutRequest):
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        rt = RuntimeRepo.get(conn, for_update=True) or {}
        if int(rt.get("config_revision") or 1) != body.expected_revision:
            raise CopilotError("CONFIG_CHANGED")
        settings = {}
        try:
            settings = json.loads(rt.get("settings_json") or "{}")
        except Exception:
            pass
        for key in _SETTINGS_KEYS:
            val = getattr(body, key)
            if val is not None:
                settings[key] = val
        # DB 只能进一步关闭，不能突破部署硬上限
        from backend.services.copilot.policy import (
            allow_schema_identifiers_deploy, copilot_enabled,
        )
        if not copilot_enabled():
            settings["enabled"] = False
        if not allow_schema_identifiers_deploy():
            settings["allow_schema_identifiers"] = False
        if settings.get("allow_schema_identifiers"):
            require_two_admin_gate()
        if not RuntimeRepo.save_settings(conn, settings, body.expected_revision):
            raise CopilotError("CONFIG_CHANGED")
        AuditRepo.record(conn, "CONFIG_CHANGE", identity.username,
                         identity.subject_id, "copilot_runtime", "1",
                         "SETTINGS_UPDATE",
                         detail={"keys": [k for k in _SETTINGS_KEYS
                                          if getattr(body, k) is not None]})
        conn.commit()
        return {"request_id": _rid(), "settings": settings,
                "config_revision": body.expected_revision + 1}
    finally:
        conn.close()


@router.get("/feedback-summary")
@guard_structural
def feedback_summary(request: Request, days: int = 30):
    """N-07 只读聚合：按 scene+rule_id+rule_snapshot_hash 聚合当前 INCORRECT 反馈。"""
    try:
        identity = _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        _require_ready(conn)
        days = max(1, min(30, days))
        # 先用 feedback_at 索引窄查，单次 3 秒预算，至多 5001 行
        rows = conn.execute(
            "SELECT feedback_rule_ids_json, COALESCE(rule_snapshot_hash,'') AS rsh, "
            "scene, owner_subject_id "
            "FROM copilot_turns "
            "WHERE feedback_code = 'INCORRECT' "
            "AND feedback_at >= UTC_TIMESTAMP(6) - INTERVAL %s DAY "
            "ORDER BY feedback_at DESC LIMIT 5001",
            (days,)).fetchall()
        if len(rows) > 5000:
            return _err("CONTEXT_TOO_LARGE", "反馈数据过多，请缩短时间窗")
        # 一轮多规则分别计入：按 (scene, rule_id, rsh) 拆行聚合
        buckets: dict[tuple, dict] = {}
        for r in rows:
            rids = json.loads(r["feedback_rule_ids_json"] or "[]") or [None]
            for rid in rids:
                k = (r["scene"], rid or "UNASSIGNED", r["rsh"])
                b = buckets.setdefault(k, {"scene": r["scene"],
                                           "rule_id": rid or "UNASSIGNED",
                                           "rule_snapshot_hash": r["rsh"],
                                           "count": 0, "subjects": set()})
                b["count"] += 1
                b["subjects"].add(r["owner_subject_id"])
        if len(buckets) > 1000:
            return _err("CONTEXT_TOO_LARGE", "分组过多，请缩短时间窗")
        items = [{kk: vv for kk, vv in b.items() if kk != "subjects"} | 
                 {"subject_count": len(b["subjects"])}
                 for b in buckets.values() if len(b["subjects"]) >= 3]
        hidden = sum(1 for b in buckets.values() if len(b["subjects"]) < 3)
        return {"request_id": _rid(), "days": days, "items": items,
                "hidden_below_privacy_threshold": hidden,
                "notice": "关联质疑次数，不等于该规则已证实误报。"}
    finally:
        conn.close()


@router.get("/health")
def health(request: Request):
    try:
        _admin_identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _get_connection()
    try:
        ensure_db()
        ready, reason, st = schema_mod.evaluate_ready(conn)
        rt = RuntimeRepo.get(conn) or {}
        from backend.services.copilot.knowledge import store as kstore
        body = {"request_id": _rid(),
                "module_schema_state": st.get("module_schema_state"),
                "module_schema_epoch": st.get("module_schema_epoch"),
                "module_reconciled_epoch": st.get("module_reconciled_epoch"),
                "ready": ready, "reason_code": reason or None,
                "runner": {"accepting": bool(rt.get("accepting")),
                           "heartbeat_at": str(rt.get("heartbeat_at") or "")
                           or None},
                "storage": {"used_bytes": int(rt.get("content_used_bytes") or 0),
                            "reserved_bytes": int(
                                rt.get("content_reserved_bytes") or 0)},
                "knowledge": kstore.status_info()}
        # QC1-m01：结构不可用时返回 503 + 完整诊断 JSON
        if not ready:
            return JSONResponse(status_code=503, content=body)
        return body
    finally:
        conn.close()
