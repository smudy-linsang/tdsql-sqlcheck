# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：Copilot 审计元数据 API（CP-W07，DETAIL §12.5）。

前缀 /api/v1/copilot-audit。admin 或 auditor+sys-auditlog；
仅本地审计元数据（无问答正文/内部 SQL/密钥）。
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.services.copilot.errors import CopilotError, error_payload
from backend.services.copilot.repository import AuditRepo
from backend.services.database import _get_connection, ensure_db

router = APIRouter(prefix="/api/v1/copilot-audit", tags=["CopilotAudit"])


def _rid() -> str:
    return uuid.uuid4().hex




@router.get("/events")
def list_events(request: Request, days: int = 7, operator: str = "",
                turn_id: str = "", limit: int = 50, offset: int = 0):
    # check_permission 前缀分支已保证 admin 或 auditor+sys-auditlog；
    # 本层再次显式断言（路由级 authz 依赖）。
    username = getattr(request.state, "username", "") or ""
    role = getattr(request.state, "role", "") or ""
    if not username or username == "anonymous":
        return JSONResponse(status_code=401, content=error_payload(
            "AUTH_REQUIRED", _rid()))
    if role == "admin":
        pass
    elif role == "auditor":
        from backend.services.copilot.authz import _visible_menus
        if "sys-auditlog" not in _visible_menus("auditor"):
            return JSONResponse(status_code=403, content=error_payload(
                "FORBIDDEN", _rid()))
    else:
        return JSONResponse(status_code=403, content=error_payload(
            "FORBIDDEN", _rid()))
    ensure_db()
    conn = _get_connection()
    try:
        # B组不可用时不提供审计查询（503）
        from backend.services.copilot import schema as schema_mod
        ready, _r, _st = schema_mod.evaluate_ready(conn)
        if not ready:
            return JSONResponse(status_code=503, content=error_payload(
                "COPILOT_SCHEMA_UNAVAILABLE", _rid()))
        days = max(1, min(31, days))
        limit = max(1, min(50, limit))
        rows = AuditRepo.query(conn, days=days, operator=operator,
                               turn_id=turn_id, limit=limit,
                               offset=max(0, offset))
        return {"request_id": _rid(), "items": [{
            "id": r["id"], "occurred_at": str(r.get("occurred_at") or ""),
            "operator": r.get("operator"), "event_type": r.get("event_type"),
            "session_id": r.get("session_id"), "turn_id": r.get("turn_id"),
            "target_type": r.get("target_type"), "target_id": r.get("target_id"),
            "result_code": r.get("result_code"),
            "detail_json": r.get("detail_json"),
            "request_id": r.get("request_id"),
        } for r in rows]}
    finally:
        conn.close()
