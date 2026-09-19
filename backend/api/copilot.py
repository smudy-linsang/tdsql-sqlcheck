# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：Copilot 用户侧 API（CP-W07，DETAIL §12.1—§12.4）。

前缀 /api/v1/copilot。全部端点先做 §9 四道闸；B组 UNAVAILABLE 时仅
capabilities/help 可读（200），其余 503 COPILOT_SCHEMA_UNAVAILABLE。

幂等：同一 (owner_subject_id, client_request_id) 重放返回原 turn；
新意图必须新 preview。预览 120 秒有效，最多每用户 3 条未消费。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.models.copilot import (
    ActionResolveRequest, FeedbackRequest, PreviewRequest, SessionCreateRequest,
    TurnSubmitRequest,
)
from backend.services.copilot import schema as schema_mod
from backend.services.copilot.schema import guard_structural
from backend.services.copilot.authz import (
    CopilotIdentity, check_instance_grant, has_copilot_menu, permission_version,
    require_session_owner, require_turn_owner, resolve_identity,
)
from backend.services.copilot.crypto import hmac_digest, load_keyring
from backend.services.copilot.errors import CopilotError, error_payload
from backend.services.copilot.policy import (
    Limits, allow_schema_identifiers_deploy, copilot_enabled,
)
from backend.services.copilot.redaction import contains_sensitive, utf8_len
from backend.services.copilot.repository import (
    BudgetRepo, PreviewRepo, RuntimeRepo, SessionRepo, TurnRepo, new_id,
    today_utc, utcnow6,
)
from backend.services.database import _get_connection, ensure_db

logger = logging.getLogger("tdsql.copilot.api")

router = APIRouter(prefix="/api/v1/copilot", tags=["Copilot"])

PREVIEW_TTL_SECONDS = 120
MAX_PREVIEWS_PER_USER = 3
RATE_LIMIT_PREVIEW_PER_MIN = 10
RATE_LIMIT_SESSION_PER_MIN = 30
RATE_LIMIT_TURN_PER_MIN = 10


# ══════════════════════════════════════════════════════════════════
# 基础设施
# ══════════════════════════════════════════════════════════════════

def _request_id() -> str:
    return uuid.uuid4().hex


def _err(code: str, message: Optional[str] = None) -> JSONResponse:
    rid = _request_id()
    return JSONResponse(status_code=schema_mod_http(code),
                        content=error_payload(code, rid, message))


def schema_mod_http(code: str) -> int:
    from backend.services.copilot.errors import http_status_of
    return http_status_of(code)


def _identity(request: Request) -> CopilotIdentity:
    return resolve_identity(request)


def _require_business_ready(conn) -> None:
    """B组 schema 门禁：先于任何 B组对象/幂等查询；失败 503。"""
    ready, _reason, _st = schema_mod.evaluate_ready(conn)
    if not ready:
        raise CopilotError("COPILOT_SCHEMA_UNAVAILABLE")


def _conn_or_503():
    ensure_db()
    return _get_connection()




def authorize_turn_material(conn, identity: CopilotIdentity, turn: dict) -> None:
    """QC1-B02：撤销即时——历史结果/动作/导出重新鉴权。

    实例会话每次读取结果/动作/导出时，按当前 grant revision 重新校验。
    任何一项撤销后不得返回结果正文和来源 excerpt。
    """
    session = SessionRepo.get(conn, turn["session_id"]) or {}
    if session.get("scope_kind") != "INSTANCE":
        return
    conn_id = session.get("connection_id")
    if not conn_id:
        return
    check_instance_grant(conn, identity, conn_id)




# ══════════════════════════════════════════════════════════════════
# GET /capabilities（B组故障仍 200，不读 B组）
# ══════════════════════════════════════════════════════════════════

@router.get("/capabilities")
def capabilities(request: Request):
    rid = _request_id()
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        from backend.services.copilot.crypto import crypto_available
        from backend.services.copilot.knowledge import store as kstore
        from backend.services.copilot.policy import policy_available
        ready, reason, st = schema_mod.evaluate_ready(conn)
        runtime = RuntimeRepo.get(conn) or {}
        settings = {}
        try:
            settings = json.loads(runtime.get("settings_json") or "{}")
        except Exception:
            pass
        from backend.services.copilot import local_ready
        _lr, _lr_reason = local_ready()
        enabled = bool(copilot_enabled()) and bool(settings.get("enabled", False)) \
            and crypto_available() and policy_available() \
            and not (_lr_reason == "COPILOT_DISABLED")
        if not ready:
            mode = "UNAVAILABLE"
        elif not enabled:
            mode = "DISABLED"
        elif not _has_route(conn):
            mode = "LOCAL_ONLY"
        else:
            mode = "READY"
        return {
            "request_id": rid,
            "subject_id": identity.subject_id,
            "enabled": bool(copilot_enabled()),
            "mode": mode,
            "runner_ready": bool(runtime.get("accepting")),
            "allowed_scenes": [
                "USAGE_HELP", "RULE_EXPLAIN", "SQL_ADVISE", "AUDIT_EXPLAIN",
                "JOB_TROUBLESHOOT", "SLOW_EXPLAIN", "COMPARE_EXPLAIN",
                "TABLETYPE_EXPLAIN", "GATEWAY_EXPLAIN", "DIAGNOSTIC_HELP"],
            "limits": Limits.snapshot(),
            "knowledge": kstore.status_info(),
            "module_schema_state": st.get("module_schema_state"),
            "reason_code": reason or None,
        }
    finally:
        conn.close()


def _has_route(conn) -> bool:
    from backend.services.copilot.repository import RouteRepo
    for r in RouteRepo.list(conn):
        if int(r.get("enabled") or 0) and r.get("primary_provider_id"):
            return True
    return False


# ══════════════════════════════════════════════════════════════════
# GET /help（本地批准知识摘要；不读 B组/模型配置/实例 grant）
# ══════════════════════════════════════════════════════════════════

@router.get("/help")
def help_endpoint(request: Request, query: str = "", page_key: str = ""):
    try:
        _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    from backend.services.copilot.knowledge import store as kstore
    q = (query or "")[:1024]
    items = []
    if kstore.bundle is not None and q:
        for r in kstore.bundle.search(q, product_family="TDSQL-MySQL")[:5]:
            items.append({"title": r["title"], "section": r["section"],
                          "excerpt": r["content"][:300], "source_id": r["source_id"]})
    return {"request_id": _request_id(), "knowledge_status": kstore.status,
            "items": items, "page_key": page_key}


# ══════════════════════════════════════════════════════════════════
# GET /connections（仅已授权连接；不返回 host/账号/端口）
# ══════════════════════════════════════════════════════════════════

@router.get("/connections")
@guard_structural
def connections(request: Request, keyword: str = ""):
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        grants = [g for g in _list_grants(conn, identity.subject_id)
                  if int(g.get("enabled") or 0) and
                  g.get("approval_state") == "APPROVED"]
        conn_ids = [g["connection_id"] for g in grants]
        out = []
        if conn_ids:
            placeholders = ",".join(["?"] * len(conn_ids))
            rows = conn.execute(
                f"SELECT id, name, is_distributed FROM tdsql_connections "
                f"WHERE id IN ({placeholders}) ORDER BY name LIMIT 50",
                tuple(conn_ids)).fetchall()
            for r in rows:
                r = dict(r)
                name = str(r.get("name") or "")
                if keyword and keyword.lower() not in name.lower():
                    continue
                out.append({
                    "connection_id": r["id"], "name": name,
                    "instance_type": "distributed" if int(
                        r.get("is_distributed") or 0) else "centralized"})
        return {"request_id": _request_id(), "items": out}
    finally:
        conn.close()


def _list_grants(conn, subject_id: str):
    from backend.services.copilot.repository import GrantRepo
    return GrantRepo.list_for_subject(conn, subject_id)


# ══════════════════════════════════════════════════════════════════
# POST /sessions
# ══════════════════════════════════════════════════════════════════

@router.post("/sessions", status_code=201)
@guard_structural
def create_session(request: Request, body: SessionCreateRequest):
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        _check_rate_limit(conn, identity, "session")
        if body.scope_kind.value == "INSTANCE":
            if not body.connection_id:
                raise CopilotError("INVALID_REQUEST",
                                   message="INSTANCE 会话必须携带 connection_id")
            check_instance_grant(conn, identity, body.connection_id)
        else:
            if body.connection_id:
                raise CopilotError("INVALID_REQUEST",
                                   message="GLOBAL_HELP 会话不得携带 connection_id")
        name_snapshot = None
        name_source = "missing"
        if body.connection_id:
            row = conn.execute(
                "SELECT name FROM tdsql_connections WHERE id = ?",
                (body.connection_id,)).fetchone()
            if row:
                name_snapshot = str(dict(row).get("name") or "")
                name_source = "snapshot"
        retention = _session_retention_days(conn)
        expires = (datetime.now(timezone.utc) + timedelta(days=retention)).strftime(
            "%Y-%m-%d %H:%M:%S.%f")
        sid = new_id()
        title = f"{'实例' if body.scope_kind.value == 'INSTANCE' else '通用'}咨询 " \
                f"{datetime.now(timezone.utc).strftime('%m-%d %H:%M')}"
        SessionRepo.insert(conn, {
            "id": sid, "owner_subject_id": identity.subject_id,
            "owner": identity.username, "scope_kind": body.scope_kind.value,
            "connection_id": body.connection_id, "database_name": body.database,
            "instance_type": body.instance_type, "initial_page_key": body.page_key,
            "name_snapshot": name_snapshot, "name_source": name_source,
            "title": title, "expires_at": expires})
        conn.commit()
        return JSONResponse(status_code=201, content={
            "request_id": _request_id(), "session_id": sid, "title": title,
            "scope_kind": body.scope_kind.value, "state": "OPEN",
            "expires_at": expires})
    finally:
        conn.close()


def _session_retention_days(conn) -> int:
    settings = RuntimeRepo.settings(conn)
    try:
        return int(settings.get("session_retention_days", 30))
    except Exception:
        return 30


def _check_rate_limit(conn, identity: CopilotIdentity, kind: str) -> None:
    """有索引时间窗口计数限流（preview 10/min、session 30/min、turn 10/min）。"""
    limits = {"preview": RATE_LIMIT_PREVIEW_PER_MIN,
              "session": RATE_LIMIT_SESSION_PER_MIN,
              "turn": RATE_LIMIT_TURN_PER_MIN}
    if kind == "session":
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM copilot_sessions WHERE owner_subject_id = ? "
            "AND created_at > UTC_TIMESTAMP(6) - INTERVAL 1 MINUTE",
            (identity.subject_id,)).fetchone()
    elif kind == "preview":
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM copilot_previews WHERE owner_subject_id = ? "
            "AND created_at > UTC_TIMESTAMP(6) - INTERVAL 1 MINUTE",
            (identity.subject_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM copilot_turns WHERE owner_subject_id = ? "
            "AND created_at > UTC_TIMESTAMP(6) - INTERVAL 1 MINUTE",
            (identity.subject_id,)).fetchone()
    if row and int(dict(row).get("c", 0)) >= limits[kind]:
        raise CopilotError("RATE_LIMITED")


# ══════════════════════════════════════════════════════════════════
# GET /sessions 列表 / GET /sessions/{id} / POST archive / GET turns
# ══════════════════════════════════════════════════════════════════

@router.get("/sessions")
@guard_structural
def list_sessions(request: Request, state: str = "", limit: int = 20,
                  cursor: str = ""):
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        limit = max(1, min(50, limit))
        cursor_updated, cursor_id = _decode_cursor(cursor)
        rows = SessionRepo.list_for_owner(
            conn, identity.subject_id, state or None, limit,
            cursor_updated, cursor_id)
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = ""
        if has_more and items:
            last = items[-1]
            next_cursor = _encode_cursor(str(last["updated_at"]), last["id"])
        return {"request_id": _request_id(),
                "items": [_session_summary(s) for s in items],
                "next_cursor": next_cursor, "has_more": has_more}
    finally:
        conn.close()


def _session_summary(s: dict) -> dict:
    return {"session_id": s["id"], "title": s["title"],
            "scope_kind": s["scope_kind"], "state": s["state"],
            "connection_name": s.get("name_snapshot"),
            "database": s.get("database_name"),
            "instance_type": s.get("instance_type"),
            "revision": s["revision"], "active_turn_id": s.get("active_turn_id"),
            "created_at": str(s.get("created_at") or ""),
            "updated_at": str(s.get("updated_at") or "")}


def _encode_cursor(updated_at: str, rid: str) -> str:
    import base64
    payload = json.dumps({"u": updated_at, "i": rid}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[Optional[str], Optional[str]]:
    if not cursor:
        return None, None
    try:
        import base64
        pad = "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(cursor + pad).decode())
        return str(data.get("u")), str(data.get("i"))
    except Exception:
        return None, None


@router.get("/sessions/{session_id}")
@guard_structural
def get_session(request: Request, session_id: str):
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        s = require_session_owner(SessionRepo.get(conn, session_id), identity)
        return {"request_id": _request_id(), **_session_summary(s)}
    finally:
        conn.close()


@router.post("/sessions/{session_id}/archive")
@guard_structural
def archive_session(request: Request, session_id: str,
                    expected_revision: int = 0):
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        s = require_session_owner(SessionRepo.get(conn, session_id), identity)
        result = SessionRepo.archive(conn, session_id, expected_revision)
        conn.commit()
        if result == "ok":
            return {"request_id": _request_id(), "session_id": session_id,
                    "state": "ARCHIVED"}
        if result == "busy":
            raise CopilotError("SESSION_BUSY")
        if result == "archived":
            return {"request_id": _request_id(), "session_id": session_id,
                    "state": "ARCHIVED"}
        raise CopilotError("SESSION_REVISION_CHANGED")
    finally:
        conn.close()


@router.get("/sessions/{session_id}/turns")
@guard_structural
def list_turns(request: Request, session_id: str, limit: int = 20,
               cursor: str = ""):
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        require_session_owner(SessionRepo.get(conn, session_id), identity)
        limit = max(1, min(50, limit))
        cursor_seq = None
        if cursor:
            try:
                cursor_seq = int(cursor)
            except ValueError:
                cursor_seq = None
        rows = TurnRepo.list_for_session(conn, session_id, limit, cursor_seq)
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = str(items[-1]["sequence_no"]) if has_more and items else ""
        return {"request_id": _request_id(),
                "items": [_turn_summary(t) for t in items],
                "next_cursor": next_cursor, "has_more": has_more}
    finally:
        conn.close()


def _turn_summary(t: dict) -> dict:
    return {"turn_id": t["id"], "sequence_no": t["sequence_no"],
            "state": t["state"], "phase": t["phase"], "scene": t["scene"],
            "created_at": str(t.get("created_at") or ""),
            "finished_at": str(t.get("finished_at") or "") or None,
            "error_code": t.get("error_code"),
            "feedback_rating": t.get("feedback_rating"),
            "feedback_code": t.get("feedback_code")}


# ══════════════════════════════════════════════════════════════════
# POST /sessions/{id}/previews（§12.3）
# ══════════════════════════════════════════════════════════════════

@router.post("/sessions/{session_id}/previews", status_code=201)
@guard_structural
def create_preview(request: Request, session_id: str, body: PreviewRequest):
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        _check_rate_limit(conn, identity, "preview")
        session = require_session_owner(
            SessionRepo.get(conn, session_id, for_update=True), identity)
        if session["state"] != "OPEN":
            raise CopilotError("SESSION_ARCHIVED")
        if session.get("active_turn_id"):
            raise CopilotError("SESSION_BUSY")
        if int(session["revision"]) != body.expected_session_revision:
            raise CopilotError("SESSION_REVISION_CHANGED")
        if PreviewRepo.count_unconsumed(conn, identity.subject_id) >= \
                MAX_PREVIEWS_PER_USER:
            raise CopilotError("RATE_LIMITED",
                               message="未消费的预览已达上限，请先提交或等待过期")

        from backend.services.copilot.preview_service import build_preview
        record = build_preview(conn, identity, session, body)

        # 存储预留（runtime 锁内）
        from backend.services.copilot import crypto as crypto_mod
        keyring = crypto_mod.load_keyring()
        payload_env = crypto_mod.encrypt(
            json.dumps(record["payload"], ensure_ascii=False),
            "copilot_previews", record["id"], "payload_envelope",
            owner=identity.subject_id, keyring=keyring)
        evidence_env = crypto_mod.encrypt(
            json.dumps({"evidence": record["evidence"],
                        "knowledge": record["knowledge"]}, ensure_ascii=False),
            "copilot_previews", record["id"], "evidence_envelope",
            owner=identity.subject_id, keyring=keyring)
        proj_env = crypto_mod.encrypt(
            json.dumps(record["model_projection"], ensure_ascii=False),
            "copilot_previews", record["id"], "model_projection_envelope",
            owner=identity.subject_id, keyring=keyring)
        reserved = crypto_mod.envelope_bytes(payload_env, evidence_env, proj_env) \
            + 1024 * 1024  # 预留模型响应空间
        quota = Limits.COPILOT_STORAGE_MAX_MIB * 1024 * 1024
        RuntimeRepo.get(conn, for_update=True)
        if not RuntimeRepo.reserve_storage(conn, reserved, quota):
            raise CopilotError("STORAGE_QUOTA_EXHAUSTED")

        expires = (datetime.now(timezone.utc) + timedelta(
            seconds=PREVIEW_TTL_SECONDS)).strftime("%Y-%m-%d %H:%M:%S.%f")
        PreviewRepo.insert(conn, {
            "id": record["id"], "session_id": session["id"],
            "owner_subject_id": identity.subject_id, "owner": identity.username,
            "scene": record["scene"], "input_hash": record["input_hash"],
            "payload_envelope": payload_env, "evidence_envelope": evidence_env,
            "model_projection_envelope": proj_env,
            "snapshot_hash": record["snapshot_hash"],
            "permission_version": record["permission_version"],
            "grant_revision": record["grant_revision"],
            "route_revision": record["route_revision"],
            "provider_revisions_json": record["provider_revisions_json"],
            "data_class": record["data_class"],
            "storage_reserved_bytes": reserved, "expires_at": expires,
            "projection_mode": record["projection_mode"],
            "identifier_policy_revision": record["identifier_policy_revision"],
            "module_schema_epoch": record["module_schema_epoch"]})
        from backend.services.copilot.repository import AuditRepo
        AuditRepo.record(conn, "PREVIEW", identity.username, identity.subject_id,
                         "copilot_sessions", session["id"], "OK",
                         detail={"scene": record["scene"],
                                 "sources": len(record["evidence"]),
                                 "data_class": record["data_class"]},
                         session_id=session["id"])
        conn.commit()
        return JSONResponse(status_code=201, content={
            "request_id": _request_id(),
            "preview_id": record["id"],
            "expires_at": expires,
            "expected_session_revision": int(session["revision"]),
            "scene": record["scene"],
            "snapshot_hash": record["snapshot_hash"],
            "input_hash": record["input_hash"],
            "context_display": _context_display(session, record),
            "evidence_cards": [_evidence_card(e) for e in record["evidence"]],
            "redacted_question": record["payload"].get("question"),
            "model_projection_preview": {
                "projection_mode": record["projection_mode"],
                "question": record["model_projection"].get("question"),
                "evidence_summary": [
                    {"evidence_id": e["evidence_id"],
                     "source_kind": e["source_kind"],
                     "availability": e["availability"],
                     "completeness": e["completeness"]}
                    for e in record["model_projection"].get("evidence", [])],
                "knowledge_count": len(record["model_projection"].get(
                    "knowledge", [])),
            },
            "data_class": record["data_class"],
            "mode": "LOCAL_ONLY" if record["route_snapshot"].get(
                "mode") == "LOCAL_ONLY" else "ROUTE",
            "provider_display": _provider_display(record["route_snapshot"]),
            "route_revision": record["route_revision"],
            "warnings": [],
            "reserved_token_upper": 38912,
            "requires_confirmation": True,
        })
    finally:
        conn.close()


def _context_display(session: dict, record: dict) -> dict:
    return {
        "connection_name": session.get("name_snapshot"),
        "name_source": session.get("name_source"),
        "database": session.get("database_name"),
        "instance_type": session.get("instance_type"),
        "observed_at": None,
        "completeness": "COMPLETE" if record["evidence"] else "UNKNOWN",
    }


def _evidence_card(e: dict) -> dict:
    return {"evidence_id": e["evidence_id"], "source_kind": e["source_kind"],
            "availability": e["availability"], "completeness": e["completeness"],
            "observed_at": e.get("observed_at"),
            "connection_name": e.get("connection_name")}


def _provider_display(route_snapshot: dict) -> dict:
    if route_snapshot.get("mode") != "ROUTE":
        return {"name": None, "model_id": None, "data_zone": None,
                "has_fallback": False}
    p = route_snapshot.get("primary") or {}
    return {"name": p.get("provider_id"), "model_id": p.get("model_id"),
            "data_zone": None, "has_fallback": bool(route_snapshot.get("fallback"))}


# ══════════════════════════════════════════════════════════════════
# POST /sessions/{id}/turns（§12.4 幂等受理）
# ══════════════════════════════════════════════════════════════════

@router.post("/sessions/{session_id}/turns", status_code=202)
@guard_structural
def submit_turn(request: Request, session_id: str, body: TurnSubmitRequest):
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        # 受理事务（锁序：runtime → session → budget(user<global 字典序) → turn）
        request_hash = _request_hash(body, session_id, identity.subject_id)
        # 幂等：先查原键（同所有者），再做新任务容量检查
        existing = TurnRepo.find_idempotent(conn, identity.subject_id,
                                            body.client_request_id)
        if existing is not None:
            if existing["request_hash"] == request_hash:
                conn.commit()
                return JSONResponse(status_code=200, content={
                    "request_id": _request_id(), "turn_id": existing["id"],
                    "session_id": existing["session_id"],
                    "state": existing["state"], "deadline_at": str(
                        existing.get("deadline_at") or ""),
                    "status_url": f"/api/v1/copilot/turns/{existing['id']}",
                    "result_url": f"/api/v1/copilot/turns/{existing['id']}/result",
                    "poll_after_ms": 2000, "reused": True})
            raise CopilotError("IDEMPOTENCY_CONFLICT")

        RuntimeRepo.get(conn, for_update=True)
        session = require_session_owner(
            SessionRepo.get(conn, session_id, for_update=True), identity)
        if session["state"] != "OPEN":
            raise CopilotError("SESSION_ARCHIVED")
        if session.get("active_turn_id"):
            raise CopilotError("SESSION_BUSY")
        if int(session["revision"]) != body.expected_session_revision:
            raise CopilotError("SESSION_REVISION_CHANGED")

        preview = PreviewRepo.get(conn, body.preview_id, for_update=True)
        if not preview or preview["owner_subject_id"] != identity.subject_id:
            raise CopilotError("NOT_FOUND")
        if preview["session_id"] != session_id:
            raise CopilotError("INVALID_REQUEST",
                               message="预览不属于该会话")
        if preview.get("consumed_turn_id"):
            raise CopilotError("PREVIEW_CONSUMED")
        if preview["snapshot_hash"] != body.snapshot_hash:
            raise CopilotError("CONTEXT_CHANGED")
        # 预览有效期 / 版本冻结核对
        if _is_expired(preview.get("expires_at")):
            raise CopilotError("PREVIEW_EXPIRED")
        if preview["permission_version"] != permission_version():
            raise CopilotError("CONTEXT_CHANGED")
        if preview["module_schema_epoch"] != _current_epoch(conn):
            raise CopilotError("CONTEXT_CHANGED")

        # 开关 / runner / 容量（新请求才检查；幂等恢复先行已返回）
        _check_enabled(conn)
        _check_runner(conn)
        _check_rate_limit(conn, identity, "turn")
        if TurnRepo.count_active(conn) >= Limits.get("COPILOT_MAX_ACTIVE_TURNS"):
            raise CopilotError("CAPACITY_EXHAUSTED")
        if TurnRepo.count_active_for_subject(conn, identity.subject_id) >= \
                Limits.COPILOT_MAX_ACTIVE_PER_USER:
            raise CopilotError("CAPACITY_EXHAUSTED",
                               message="您有正在处理的提问")

        # 预算预留（user/global 字典序 principal）
        settings = RuntimeRepo.settings(conn)
        reserve = _reserve_tokens(conn, identity.subject_id, settings)

        turn_id = new_id()
        seq = TurnRepo.next_sequence(conn, session_id)
        deadline = (datetime.now(timezone.utc) + timedelta(
            seconds=Limits.get("COPILOT_TURN_DEADLINE_SECONDS"))).strftime(
            "%Y-%m-%d %H:%M:%S.%f")
        route_snap = json.loads(preview["provider_revisions_json"] or "{}")
        route_envelope = _route_envelope_from_preview(conn, preview)
        TurnRepo.insert(conn, {
            "id": turn_id, "session_id": session_id, "preview_id": preview["id"],
            "owner": identity.username, "owner_subject_id": identity.subject_id,
            "turn_kind": "USER_QUESTION", "scene": preview["scene"],
            "rule_snapshot_hash": None,
            "module_schema_epoch": preview["module_schema_epoch"],
            "client_request_id": body.client_request_id,
            "request_hash": request_hash, "sequence_no": seq,
            "deadline_at": deadline,
            "route_snapshot_envelope": route_envelope,
            "request_envelope": preview["payload_envelope"],
            "evidence_envelope": preview["evidence_envelope"],
            "source_hash": preview["snapshot_hash"],
            "reserved_tokens": reserve})
        if not PreviewRepo.consume(conn, preview["id"], turn_id):
            raise CopilotError("PREVIEW_CONSUMED")
        SessionRepo.touch_activity(conn, session_id, turn_id)
        from backend.services.copilot.repository import AuditRepo
        AuditRepo.record(conn, "ACCEPT", identity.username, identity.subject_id,
                         "copilot_turns", turn_id, "ACCEPTED",
                         detail={"scene": preview["scene"]},
                         session_id=session_id, turn_id=turn_id)
        conn.commit()
        return JSONResponse(status_code=202, content={
            "request_id": _request_id(), "turn_id": turn_id,
            "session_id": session_id, "state": "ACCEPTED",
            "deadline_at": deadline,
            "status_url": f"/api/v1/copilot/turns/{turn_id}",
            "result_url": f"/api/v1/copilot/turns/{turn_id}/result",
            "poll_after_ms": 2000, "reused": False})
    finally:
        conn.close()


def _request_hash(body: TurnSubmitRequest, session_id: str,
                  owner_subject_id: str) -> str:
    material = json.dumps({
        "client_request_id": body.client_request_id,
        "preview_id": body.preview_id,
        "snapshot_hash": body.snapshot_hash,
        "session_id": session_id,
        "owner_subject_id": owner_subject_id,
        "confirm_data_use": bool(body.confirm_data_use),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _is_expired(expires_at) -> bool:
    if not expires_at:
        return True
    try:
        exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp <= datetime.now(timezone.utc)
    except Exception:
        return True


def _check_enabled(conn) -> None:
    settings = RuntimeRepo.settings(conn)
    if not copilot_enabled() or not bool(settings.get("enabled", False)):
        raise CopilotError("COPILOT_DISABLED")
    # R3-M02：持久停用标记存在时不得新受理（不只是页面显示）
    from backend.services.copilot.policy import deployed_disabled_reason
    if deployed_disabled_reason():
        raise CopilotError("COPILOT_DISABLED")
    # M-02：部署参数越界拒绝受理（不静默夹值）
    problems = Limits.validate()
    if problems:
        raise CopilotError("COPILOT_DISABLED",
                           message="助手部署参数越界未启用，请联系管理员核查配置")


def _check_runner(conn) -> None:
    rt = RuntimeRepo.get(conn) or {}
    if not int(rt.get("accepting") or 0):
        raise CopilotError("RUNNER_UNAVAILABLE")
    hb = rt.get("heartbeat_at")
    if not hb:
        raise CopilotError("RUNNER_UNAVAILABLE")
    try:
        hb_dt = datetime.fromisoformat(str(hb).replace("Z", "+00:00"))
        if hb_dt.tzinfo is None:
            hb_dt = hb_dt.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - hb_dt).total_seconds() > 10:
            raise CopilotError("RUNNER_UNAVAILABLE")
    except ValueError:
        raise CopilotError("RUNNER_UNAVAILABLE")


def _reserve_tokens(conn, subject_id: str, settings: dict) -> int:
    """预留 user/global 日额度（固定 38912 上界）；超限拒绝。"""
    reserve = 38912
    day = today_utc()
    user_limit = int(settings.get("user_daily_tokens", 1000000))
    global_limit = int(settings.get("global_daily_tokens", 10000000))
    principals = sorted([f"user:{subject_id}", "global"])
    reserved_done = []
    try:
        for p in principals:
            limit = user_limit if p.startswith("user:") else global_limit
            if not BudgetRepo.reserve(conn, p, day, reserve, limit):
                raise CopilotError("QUOTA_EXHAUSTED")
            reserved_done.append(p)
    except Exception:
        for p in reserved_done:
            BudgetRepo.settle(conn, p, day, reserve, 0)
        raise
    return reserve


def _current_epoch(conn) -> int:
    rt = RuntimeRepo.get(conn) or {}
    return int(rt.get("module_schema_epoch") or 1)


def _route_envelope_from_preview(conn, preview: dict) -> str:
    from backend.services.copilot.routing import resolve_route, route_snapshot
    try:
        from backend.services.copilot.policy import load_policy, policy_available
        if not policy_available():
            return json.dumps({"mode": "LOCAL_ONLY"})
        info = resolve_route(conn, preview["scene"], load_policy())
        return json.dumps(route_snapshot(info), ensure_ascii=False)
    except Exception:
        return json.dumps({"mode": "LOCAL_ONLY"})


# ══════════════════════════════════════════════════════════════════
# GET /turns/{id} / result / cancel / feedback / actions / export
# ══════════════════════════════════════════════════════════════════

@router.get("/turns/{turn_id}")
@guard_structural
def get_turn(request: Request, turn_id: str):
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        t = require_turn_owner(TurnRepo.get(conn, turn_id), identity)
        terminal = t["state"] in (
            "SUCCEEDED", "LOCAL_ONLY", "DEGRADED", "FAILED", "CANCELLED",
            "INTERRUPTED")
        return {"request_id": _request_id(), "turn_id": t["id"],
                "session_id": t["session_id"], "state": t["state"],
                "phase": t["phase"], "scene": t["scene"],
                "created_at": str(t.get("created_at") or ""),
                "deadline_at": str(t.get("deadline_at") or ""),
                "finished_at": str(t.get("finished_at") or "") or None,
                "terminal": terminal,
                "result_available": terminal and t.get("response_envelope")
                is not None,
                "error_code": t.get("error_code"),
                "retry_after_ms": 2000 if not terminal else 0}
    finally:
        conn.close()


@router.get("/turns/{turn_id}/result")
@guard_structural
def get_turn_result(request: Request, turn_id: str):
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        t = require_turn_owner(TurnRepo.get(conn, turn_id), identity)
        authorize_turn_material(conn, identity, t)
        terminal = t["state"] in (
            "SUCCEEDED", "LOCAL_ONLY", "DEGRADED", "FAILED", "CANCELLED",
            "INTERRUPTED")
        if not terminal:
            raise CopilotError("RESULT_NOT_READY")
        if not t.get("response_envelope"):
            return {"request_id": _request_id(), "turn_id": t["id"],
                    "state": t["state"], "error_code": t.get("error_code"),
                    "error_message": t.get("error_message")}
        resp = json.loads(t["response_envelope"])
        session = SessionRepo.get(conn, t["session_id"]) or {}
        return {
            "request_id": _request_id(), "turn_id": t["id"],
            "state": t["state"],
            "answer_source": "MODEL" if t["state"] == "SUCCEEDED"
            else "LOCAL_TEMPLATE",
            "context_display": {
                "connection_name": session.get("name_snapshot"),
                "name_source": session.get("name_source"),
                "database": session.get("database_name"),
                "instance_type": session.get("instance_type"),
            },
            "answer": resp.get("answer"),
            "sources": resp.get("sources", []),
            "actions": resp.get("actions", []),
            "model": resp.get("model"),
            "usage": resp.get("usage"),
            "disclaimer": "AI或本地助手建议不是正式审核、数据库执行或验收结论。",
        }
    finally:
        conn.close()


@router.post("/turns/{turn_id}/cancel", status_code=202)
@guard_structural
def cancel_turn(request: Request, turn_id: str):
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        t = require_turn_owner(TurnRepo.get(conn, turn_id), identity)
        result = TurnRepo.request_cancel(conn, turn_id)
        if result == "cancel_requested":
            from backend.services.copilot.repository import AuditRepo
            AuditRepo.record(conn, "CANCEL", identity.username,
                             identity.subject_id, "copilot_turns", turn_id,
                             "CANCEL_REQUESTED", turn_id=turn_id,
                             session_id=t["session_id"])
            conn.commit()
            return JSONResponse(status_code=202, content={
                "request_id": _request_id(), "turn_id": turn_id,
                "state": "CANCEL_REQUESTED"})
        if result == "already_terminal":
            return JSONResponse(status_code=200, content={
                "request_id": _request_id(), "turn_id": turn_id,
                "state": t["state"], "already_terminal": True})
        raise CopilotError("NOT_FOUND")
    finally:
        conn.close()


@router.post("/turns/{turn_id}/feedback")
@guard_structural
def feedback(request: Request, turn_id: str, body: FeedbackRequest):
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        t = require_turn_owner(TurnRepo.get(conn, turn_id), identity)
        rule_ids = None
        if t.get("response_envelope"):
            try:
                resp = json.loads(t["response_envelope"])
                rules = set()
                for s in resp.get("sources", []):
                    if s.get("source_kind") == "RULE_RUNTIME":
                        pass
                rule_ids = json.dumps(sorted(rules)) if rules else None
            except Exception:
                rule_ids = None
        ok = TurnRepo.set_feedback(conn, turn_id, body.rating,
                                   body.code.value, rule_ids)
        if not ok:
            raise CopilotError("INVALID_REQUEST",
                               message="仅终态轮次可反馈")
        from backend.services.copilot.repository import AuditRepo
        AuditRepo.record(conn, "FEEDBACK", identity.username,
                         identity.subject_id, "copilot_turns", turn_id,
                         body.code.value, turn_id=turn_id,
                         session_id=t["session_id"])
        conn.commit()
        return {"request_id": _request_id(), "turn_id": turn_id,
                "feedback_rating": body.rating, "feedback_code": body.code.value}
    finally:
        conn.close()


@router.post("/turns/{turn_id}/actions/resolve")
@guard_structural
def resolve_action(request: Request, turn_id: str, body: ActionResolveRequest):
    """动作卡再校验：不执行 SQL/审核/扫描，仅返回安全动作数据。"""
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        t = require_turn_owner(TurnRepo.get(conn, turn_id), identity)
        authorize_turn_material(conn, identity, t)
        if not t.get("response_envelope"):
            raise CopilotError("NOT_FOUND")
        resp = json.loads(t["response_envelope"])
        action = None
        for a in resp.get("actions", []):
            if a.get("action_id") == body.action_id:
                action = a
                break
        if action is None:
            raise CopilotError("NOT_FOUND")
        # OPEN_SOURCE：重新授权（源 ACL+存在性）
        if action["type"] == "OPEN_SOURCE":
            ev_id = action.get("evidence_id")
            return {"request_id": _request_id(), "type": "OPEN_SOURCE",
                    "evidence_id": ev_id,
                    "url_hint": f"source:{ev_id}"}
        if action["type"] == "NAVIGATE":
            route_key = action.get("route_key")
            if route_key not in ("audit-sql", "file-audit",
                                 "schema-extractor-audit", "slow-tasks",
                                 "slow-records", "explain", "schema-check",
                                 "bigtable", "deep-diag-tabletype",
                                 "deep-diag-gateway", "rules", "sys-info"):
                raise CopilotError("INVALID_REQUEST")
            return {"request_id": _request_id(), "type": "NAVIGATE",
                    "route_key": route_key}
        if action["type"] == "COPY_SUGGESTION":
            cand = _find_candidate(resp, action.get("candidate_id"))
            if cand is None:
                raise CopilotError("NOT_FOUND")
            return {"request_id": _request_id(), "type": "COPY_SUGGESTION",
                    "candidate_id": action.get("candidate_id"),
                    "sql": cand.get("sql"), "reason": cand.get("reason"),
                    "warning": "候选 SQL 仅为建议模板，请人工复核后再使用"}
        if action["type"] == "OPEN_AUDIT_EDITOR":
            cand = _find_candidate(resp, action.get("candidate_id"))
            if cand is None:
                raise CopilotError("NOT_FOUND")
            return {"request_id": _request_id(),
                    "type": "OPEN_AUDIT_EDITOR",
                    "candidate_id": action.get("candidate_id"),
                    "sql": cand.get("sql"),
                    "note": "请送入原审核编辑器进行正式审核"}
        raise CopilotError("INVALID_REQUEST")
    finally:
        conn.close()


def _find_candidate(resp: dict, candidate_id: Optional[str]) -> Optional[dict]:
    if not candidate_id:
        return None
    try:
        idx = int(candidate_id.lstrip("C")) - 1
    except ValueError:
        return None
    cands = (resp.get("answer") or {}).get("sql_candidates") or []
    if 0 <= idx < len(cands):
        return cands[idx]
    return None


@router.get("/turns/{turn_id}/export.html")
@guard_structural
def export_turn_html(request: Request, turn_id: str):
    """单轮独立脱敏 HTML 建议报告（先复核所有者与来源）。"""
    try:
        identity = _identity(request)
    except CopilotError as e:
        return _err(e.code, e.message)
    conn = _conn_or_503()
    try:
        _require_business_ready(conn)
        t = require_turn_owner(TurnRepo.get(conn, turn_id), identity)
        authorize_turn_material(conn, identity, t)
        if not t.get("response_envelope"):
            raise CopilotError("RESULT_NOT_READY")
        from backend.services.copilot.report import render_turn_html
        session = SessionRepo.get(conn, t["session_id"]) or {}
        html = render_turn_html(t, session)
        from backend.services.copilot.repository import AuditRepo
        AuditRepo.record(conn, "EXPORT", identity.username, identity.subject_id,
                         "copilot_turns", turn_id, "OK", turn_id=turn_id,
                         session_id=t["session_id"])
        conn.commit()
        from fastapi.responses import Response
        filename = f"copilot-advice-{turn_id[:8]}-{datetime.now(timezone.utc).strftime('%Y%m%d')}.html"
        return Response(
            content=html.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                # N-05（UAT D40）：报告专用限制性 CSP，不用全站 CSP
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
            })
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# POST /chat（类 DB_Monitor 流畅对话问答模式 + 银行只读安全底线）
# ══════════════════════════════════════════════════════════════════

from pydantic import BaseModel, ConfigDict, Field
import re
import httpx

class CopilotChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=8000)
    connection_id: Optional[str] = Field(None, max_length=128)
    history: Optional[list[dict]] = None


COPILOT_CHAT_SYSTEM_PROMPT = """你是 TDSQL SQL审核与智能运维平台的专属 Copilot 专家助手。
精通 TDSQL 分布式数据库架构（Proxy、DB节点、赤兔管理台、Zookeeper）、MySQL 语法内核、SQL 审核规则、慢查询优化与运维诊断。

【服务准则与沟通风格】
1. 热情、直接、专业地回答用户的问题，提供“你一言我一语”的流畅对话交互体验；
2. 不要设置刻板繁复的审查门槛或生硬拒答；对于用户的各种技术咨询、原理解析、配置疑问，能回答的请直接清晰解答；
3. 若用户提问涉及系统纳管实例数量、内置审核规则定义（如 R043 等规则）、在线元数据审核历史记录数或拓扑状态，请严格结合下方提供的【系统实时上下文数据与拓扑】如实、准确、权威地解答；
4. 输出请使用美观易读的 Markdown 排版（小标题、加粗重点、列表、对比表格、代码块）。

【绝对安全底线（银行系统只读约束 - 最高优先级）】
本平台运行于银行数据库管理环境中，但不是交易业务系统，严禁输出任何可直接修改数据库数据或结构的 SQL 语句（包括但不限于 DROP, TRUNCATE, DELETE, UPDATE, ALTER, INSERT, REPLACE, GRANT, REVOKE 等写操作）。
你仅被允许提供只读查询与性能诊断语句（如 SELECT, EXPLAIN, SHOW, DESCRIBE）。
若用户提问要求直接修改数据，必须明确拒绝并提示“根据银行只读安全规范，Copilot 仅提供只读查询与优化建议，严禁生成修改数据的 SQL”，并仅提供只读查询或诊断方案。
"""


@router.post("/chat", summary="Copilot 实时对话接口 (类似 DB_Monitor)")
@guard_structural
async def copilot_chat(request: Request, body: Optional[CopilotChatRequest] = None):
    """类 DB_Monitor 模式的自然流利对话接口：
    - 多轮历史记录衔接
    - 实时资产与拓扑上下文注入
    - 银行只读安全硬红线拦截
    - 智能动作卡片 (Action Cards) 生成
    - 身份解析与全链路审计留痕
    """
    if body is None:
        try:
            raw = await request.json()
            if isinstance(raw, dict):
                body = CopilotChatRequest(**raw)
            else:
                body = CopilotChatRequest(query=str(raw))
        except Exception as e:
            return JSONResponse(status_code=400, content=error_payload("INVALID_REQUEST", uuid.uuid4().hex, f"请求参数非法: {e}"))

    query = (body.query or "").strip()
    if not query:
        return {"ok": False, "answer": "请输入您的问题。", "model": "none"}

    # 1. 身份解析（B-01 治理门禁）
    identity = None
    if config.auth_enabled():
        try:
            identity = resolve_identity(request)
        except CopilotError as e:
            from backend.services.copilot.errors import http_status_of
            return JSONResponse(status_code=http_status_of(e.code),
                                content=error_payload(e.code, uuid.uuid4().hex, e.message))
        except Exception:
            return JSONResponse(status_code=401,
                                content=error_payload("AUTH_REQUIRED", uuid.uuid4().hex, "身份验证失败，请重新登录"))

    from backend.services.copilot import crypto as crypto_mod
    from backend.services.copilot.policy import load_policy
    from backend.services.copilot.repository import ProviderRepo, AuditRepo, GrantRepo
    from backend.services.copilot.perception import CopilotPerceptionEngine

    answer = None
    model_name = "local-expert"
    provider_name = "DBA专家引擎"
    latency_ms = 0

    conn = _get_connection()
    try:
        ensure_db()
        # 2. 实例授权检查（若指定了 connection_id）
        if body.connection_id and identity:
            grant = GrantRepo.get_enabled(conn, identity.subject_id, body.connection_id)
            if not grant:
                return JSONResponse(
                    status_code=403,
                    content=error_payload("INSTANCE_NOT_GRANTED", uuid.uuid4().hex, f"当前用户未获准访问该实例（{body.connection_id}）的 Copilot 资料")
                )

        ctx_parts = CopilotPerceptionEngine.gather_context(conn, query, body.connection_id)

        # 3. 尝试调用真实大模型（优先按“AI配置 -> 场景路由”精准匹配主模型/备模型）
        from backend.services.copilot.repository import RouteRepo
        detected_scene = CopilotPerceptionEngine.detect_scene(query)
        route = RouteRepo.get(conn, detected_scene)

        active_p = None
        if route and route.get("enabled"):
            p_id = route.get("primary_provider_id")
            if p_id:
                cand = ProviderRepo.get(conn, p_id)
                if cand and cand.get("enabled"):
                    active_p = cand
            if not active_p and route.get("fallback_provider_id"):
                fb_cand = ProviderRepo.get(conn, route["fallback_provider_id"])
                if fb_cand and fb_cand.get("enabled"):
                    active_p = fb_cand

        # 若未命中场景或场景主备模型未就绪，使用全局已启用的首选模型兜底
        if not active_p:
            ps = ProviderRepo.list(conn)
            active_p = next((p for p in ps if p.get('enabled')), None)

        if active_p:
            full_p = ProviderRepo.get(conn, active_p['id'])
            keyring = load_keyring()
            secret = crypto_mod.decrypt(
                full_p['secret_envelope'], 'copilot_providers', full_p['id'],
                'secret_envelope', owner='SYSTEM',
                crypto_revision=int(full_p.get('revision') or 1), keyring=keyring
            )
            policy = load_policy()
            url = policy.build_url(full_p['endpoint_id'])

            messages = [{"role": "system", "content": COPILOT_CHAT_SYSTEM_PROMPT}]
            if body.history:
                for h in body.history[-8:]:
                    if isinstance(h, dict) and h.get('role') in ('user', 'assistant') and h.get('content'):
                        messages.append({'role': h['role'], 'content': str(h['content'])[:4000]})

            user_msg = query
            if ctx_parts:
                user_msg += "\n\n[系统实时上下文数据与拓扑]\n" + "\n".join(ctx_parts)
            messages.append({"role": "user", "content": user_msg})

            req_body = {
                "model": full_p["model_id"],
                "messages": messages,
                "max_tokens": 4096,
                "temperature": 0.3,
                "stream": False
            }

            copilot_timeout = float(os.getenv("COPILOT_CHAT_TIMEOUT", "3600"))
            t0 = time.time()
            try:
                # 异步非阻塞调用，释放 asyncio 事件循环（B-02），trust_env=False 严禁代理逃逸
                async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                             timeout=httpx.Timeout(copilot_timeout)) as client:
                    resp = await client.post(
                        url,
                        json=req_body,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {secret}"
                        }
                    )
                    latency_ms = int((time.time() - t0) * 1000)
                    if resp.status_code == 200:
                        resp_data = resp.json()
                        answer = resp_data['choices'][0]['message']['content']
                        model_name = full_p["model_id"]
                        provider_name = full_p.get("name")
                    else:
                        logger.warning("Copilot 模型响应非 200: %s %s", resp.status_code, resp.text[:200])
            except Exception as e:
                logger.warning("Copilot 模型异步调用异常，使用本地降级: %s", e)
    except Exception as e:
        logger.warning("Copilot 处理异常: %s", e)
    finally:
        conn.close()

    # 4. 降级方案
    if not answer:
        if ctx_parts:
            answer = "### 🤖 TDSQL 智能助手 (本地资产与规则引擎模式)\n\n" + "\n\n".join(ctx_parts) + "\n\n您可以提问关于上述实例的架构规范、连接排查或慢查询诊断建议。"
        else:
            answer = f"### 🤖 TDSQL 智能专家助手\n\n已收到您的问题：**{query}**。\n\n当前大模型服务连接暂时波动或未配置完成。您可以通过上方实例选择器切换数据库，或在『SQL审核』『实例体检』模块执行标准诊断。"

    # 5. 银行系统硬性只读安全审查 (Guardrail) 与只读强制提醒 (N-03)
    write_patterns = [
        r'\b(DROP\s+TABLE|DROP\s+DATABASE|TRUNCATE\s+TABLE|TRUNCATE\s+\w+)\b',
        r'\b(DELETE\s+FROM)\b',
        r'\b(UPDATE\s+\w+\s+SET)\b',
        r'\b(ALTER\s+TABLE)\b',
        r'\b(INSERT\s+INTO)\b',
        r'\b(GRANT\s+.*\s+TO|REVOKE\s+.*\s+FROM)\b',
    ]
    detected_violations = []
    for pat in write_patterns:
        matches = re.findall(pat, answer, re.I)
        if matches:
            detected_violations.extend(matches)

    safety_status = {
        "is_read_only": len(detected_violations) == 0,
        "violations": list(set(detected_violations))
    }
    if detected_violations:
        answer += "\n\n> ⚠️ **【银行系统只读安全红线提示】**\n> 诊断输出中检测到可能引发数据变更的写操作语句（如 " + ", ".join(list(set(detected_violations))[:3]) + "），已被系统安全防线标记！在数据库日常运维中请严格使用只读诊断语句。"

    # 6. 审计日志入库留痕 (B-01)
    if identity:
        conn_audit = _get_connection()
        try:
            ensure_db()
            AuditRepo.record(
                conn_audit, "COPILOT_CHAT", identity.username, identity.subject_id,
                "copilot_chat", body.connection_id or "GENERAL", "INVOKE",
                detail={"query": query[:200], "model": model_name, "latency_ms": latency_ms,
                        "is_read_only": safety_status["is_read_only"]}
            )
            conn_audit.commit()
        except Exception as e:
            logger.warning("Copilot 对话审计日志写入异常: %s", e)
        finally:
            conn_audit.close()

    # 5. 组装交互动作卡片 (Action Cards)
    action_cards = []

    # 提取只读 SQL
    sql_blocks = re.findall(r'```sql\s*(.*?)\s*```', answer, re.S | re.I)
    for s in sql_blocks:
        s_clean = s.strip()
        if re.match(r'^(SELECT|EXPLAIN|SHOW|DESCRIBE|DESC)\b', s_clean, re.I):
            action_cards.append({
                "card_type": "SQL_SUGGESTION",
                "title": "📋 复制只读诊断 SQL",
                "sql": s_clean,
                "desc": "安全只读 SQL，已通过银行只读安全校验，可直接在查询控制台或客户端执行"
            })
            action_cards.append({
                "card_type": "NAVIGATE_EDITOR",
                "title": "🔍 送入 SQL 审核编辑器",
                "sql": s_clean,
                "desc": "在 SQL 审核页面深度校验规则与语法规范"
            })
            break

    # 快捷功能导航卡片联动全模块页面
    if any(k in q_lower for k in ['体检', '健康', '监控', '上线检查', '会话', 'thread']):
        action_cards.append({
            "card_type": "NAVIGATE",
            "title": "📊 前往上线检查",
            "target_page": "schema-check",
            "desc": "查看实例表结构校验、连接使用率与健康评分"
        })
    elif any(k in q_lower for k in ['元数据', 'metadata', '元数据审核']):
        action_cards.append({
            "card_type": "NAVIGATE",
            "title": "📑 前往在线元数据审核",
            "target_page": "schema-extractor-audit",
            "desc": "查看抽取元数据与深度规则校验报告"
        })
    elif any(k in q_lower for k in ['慢sql', '慢查', '耗时', '优化']):
        action_cards.append({
            "card_type": "NAVIGATE",
            "title": "⚡ 前往慢SQL记录",
            "target_page": "slow-records",
            "desc": "抓取并分析各分片慢 SQL Top 排行与执行计划"
        })
    elif any(k in q_lower for k in ['大表', '容量', '行数']):
        action_cards.append({
            "card_type": "NAVIGATE",
            "title": "📦 前往大表治理",
            "target_page": "bigtable",
            "desc": "查看大表清单、拆分键与分区建议"
        })
    elif any(k in q_lower for k in ['规则', '规范', 'rule']):
        action_cards.append({
            "card_type": "NAVIGATE",
            "title": "📖 前往审核规则库",
            "target_page": "rules",
            "desc": "查看与配置 121 项 TDSQL 分布式与集中式开发规范"
        })

    return {
        "ok": True,
        "answer": answer,
        "model": model_name,
        "provider_name": provider_name,
        "latency_ms": latency_ms,
        "action_cards": action_cards,
        "safety_status": safety_status
    }

