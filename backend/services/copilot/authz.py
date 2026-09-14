# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：Copilot 授权服务（CP-W03，DETAIL §9.1）。

四道闸（按序检查，任何查询/解密/出域前均执行）：
  1. 是否能用助手（有效登录身份 + copilot 菜单 + AUTH 开启 + subject 代际/iat）
  2. 是否获准带入该实例（copilot_instance_grants APPROVED+enabled）
  3. 是否能读取该源对象（源模块菜单权限 + 对象归属）
  4. 是否拥有该会话（owner_subject_id 匹配）

subject 代际身份：users 以 username 为主键，删除重建不得继承旧 grant/session。
JWT iat 必须严格晚于 subject.created_at（N-02），否则 401 提示重新登录。

N-09：启用业务资料（实例授权）与结构标识符前，必须具备至少两名不同自然人、
各自 ACTIVE 账号的管理员（role=admin 且有 copilot-admin 菜单）。
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from backend import config
from backend.services.copilot.errors import CopilotError
from backend.services.copilot.repository import GrantRepo, SubjectRepo
from backend.services.database import _get_connection, ensure_db

logger = logging.getLogger("tdsql.copilot.authz")

MENU_COPILOT = "copilot"
MENU_COPILOT_ADMIN = "copilot-admin"


# ══════════════════════════════════════════════════════════════════
# 身份与菜单
# ══════════════════════════════════════════════════════════════════

def _visible_menus(role: str) -> list[str]:
    from backend.services.auth_service import get_visible_menus
    return get_visible_menus(role)


def has_copilot_menu(role: str) -> bool:
    return MENU_COPILOT in _visible_menus(role)


def has_copilot_admin(role: str) -> bool:
    """copilot-admin 配置权：role=admin 且菜单可见（路由内显式断言）。"""
    return role == "admin" and MENU_COPILOT_ADMIN in _visible_menus(role)


def count_active_copilot_admins() -> int:
    """N-09：当前 ACTIVE 且 role=admin 且具 copilot-admin 菜单的账号数。"""
    ensure_db()
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT u.username FROM users u JOIN role_permissions rp "
            "ON rp.role_id = u.role AND rp.menu_key = 'copilot-admin' AND rp.visible = 1 "
            "WHERE u.role = 'admin' AND u.status = 'active'").fetchall()
        return len(rows)
    finally:
        conn.close()


def require_two_admin_gate() -> None:
    """N-09 前置：启用业务资料/结构标识符须至少两名独立 ACTIVE admin+copilot-admin。

    系统只能核验账号，不能仅凭两个用户名证明两个自然人；名单/职责分离由
    部署责任方核验（CP-GATE-DATA）。
    """
    if count_active_copilot_admins() < 2:
        raise CopilotError(
            "FORBIDDEN",
            message="启用实例资料授权前须具备至少两名独立管理员账号"
                    "（role=admin 且具 copilot-admin 权限），请先在部署手册登记名单")


class CopilotIdentity:
    """一次请求解析出的 Copilot 有效身份。"""

    def __init__(self, username: str, role: str, subject_id: str,
                 subject_created_at: str):
        self.username = username
        self.role = role
        self.subject_id = subject_id
        self.subject_created_at = subject_created_at

    def __repr__(self):
        return f"CopilotIdentity(user={self.username!r}, subject={self.subject_id[:8]}…)"


def resolve_identity(request) -> CopilotIdentity:
    """闸一：有效登录身份 + copilot 菜单 + subject 代际 + JWT iat 严格校验。

    - 即使 AUTH_ENABLED=false（匿名 admin 旁路），Copilot API 仍拒绝匿名；
    - 早于 subject.created_at 的 JWT 一律 401（N-02），iat 精度边界不足时
      等下一秒再登录，不能用 >= 放行旧 token。
    """
    username = getattr(request.state, "username", "") or ""
    role = getattr(request.state, "role", "") or ""
    if not config.auth_enabled() or username == "anonymous":
        raise CopilotError("AUTH_REQUIRED")
    if not has_copilot_menu(role):
        raise CopilotError("FORBIDDEN")

    ensure_db()
    conn = _get_connection()
    try:
        subj = SubjectRepo.get_active_by_username(conn, username)
        if not subj:
            raise CopilotError("AUTH_REQUIRED",
                               message="助手身份未初始化，请重新登录")
        # N-02：JWT iat 必须严格晚于 subject.created_at
        token = _extract_token(request)
        iat = _token_iat(token)
        created_ts = _parse_dt6(subj.get("created_at"))
        if iat is None or created_ts is None or iat <= created_ts:
            raise CopilotError("AUTH_REQUIRED",
                               message="登录态早于助手身份建立时间，请重新登录")
        return CopilotIdentity(username, role, subj["subject_id"],
                               str(subj.get("created_at") or ""))
    finally:
        conn.close()


def _extract_token(request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return ""


def _token_iat(token: str) -> Optional[int]:
    from backend.services.auth_service import verify_token
    payload = verify_token(token) if token else None
    if not payload:
        return None
    try:
        return int(payload.get("iat", 0))
    except (TypeError, ValueError):
        return None


def _parse_dt6(value) -> Optional[int]:
    """DATETIME(6)（UTC 字符串/datetime）→ epoch 秒（向下取整）。"""
    if value is None:
        return None
    from datetime import datetime, timezone
    if hasattr(value, "timestamp"):
        dt = value
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    s = str(value).strip().replace("T", " ").replace("Z", "")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════════
# 实例授权（闸二）
# ══════════════════════════════════════════════════════════════════

def check_instance_grant(conn, identity: CopilotIdentity,
                         connection_id: str) -> dict:
    """闸二：Copilot 实例资料授权（admin 也需逐实例授权）。"""
    grant = GrantRepo.get_enabled(conn, identity.subject_id, connection_id)
    if not grant:
        raise CopilotError("INSTANCE_NOT_GRANTED")
    return grant


def effective_allow_identifiers(conn, identity: CopilotIdentity,
                                connection_id: str) -> bool:
    """§4.4 方案甲逐实例闸：grant 允许且 identifier_approval_ref 非空。"""
    grant = GrantRepo.get_enabled(conn, identity.subject_id, connection_id)
    if not grant:
        return False
    if not int(grant.get("allow_schema_identifiers") or 0):
        return False
    return bool(grant.get("identifier_approval_ref"))


# ══════════════════════════════════════════════════════════════════
# 会话所有权（闸四）
# ══════════════════════════════════════════════════════════════════

def require_session_owner(session: Optional[dict],
                          identity: CopilotIdentity) -> dict:
    """会话不存在或非所有者统一 404（不暴露对象存在性）。"""
    if not session or session.get("owner_subject_id") != identity.subject_id:
        raise CopilotError("NOT_FOUND")
    return session


def require_turn_owner(turn: Optional[dict], identity: CopilotIdentity) -> dict:
    if not turn or turn.get("owner_subject_id") != identity.subject_id:
        raise CopilotError("NOT_FOUND")
    return turn


# ══════════════════════════════════════════════════════════════════
# 源模块权限（闸三的菜单侧；对象归属由 evidence 适配器核对）
# ══════════════════════════════════════════════════════════════════

#: 来源 kind → 所需源模块菜单（任一）
SOURCE_MENU_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "rule": ("rules",),
    "audit_history": ("audit-sql", "file-audit", "schema-extractor-audit"),
    "metadata_job": ("schema-extractor-audit",),
    "slow_query": ("slow-tasks", "slow-records"),
    "scan_snapshot": ("schema-check", "schema-extractor-audit", "slow-tasks",
                      "bigtable"),
    "table_type_stat": ("deep-diag-tabletype",),
    "gateway_report": ("deep-diag-gateway",),
    "user_draft": (),
}


def check_source_menu(identity: CopilotIdentity, kind: str) -> None:
    required = SOURCE_MENU_REQUIREMENTS.get(kind)
    if required is None:
        raise CopilotError("INVALID_REQUEST")
    if not required:
        return
    visible = _visible_menus(identity.role)
    if not any(m in visible for m in required):
        raise CopilotError("SOURCE_FORBIDDEN")


def permission_version() -> str:
    """当前权限版本（预览冻结用；DB 优先）。"""
    ensure_db()
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT config_value FROM system_config "
            "WHERE config_key = 'permission_version'").fetchone()
        return str(dict(row).get("config_value")) if row else "0"
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 账户生命周期接点（A组，同事务；不读 B组）
# ══════════════════════════════════════════════════════════════════

def on_user_created(conn, username: str, user_created_at: str) -> None:
    """create_user/bootstrap 同事务：分配新 subject。B组 SQL 次数为 0。"""
    SubjectRepo.ensure_for_user(conn, username, user_created_at)


def on_user_deleted(conn, username: str) -> None:
    """delete_user 同事务：吊销 ACTIVE subject（权威撤权，不等待清理 B组）。"""
    SubjectRepo.revoke_active(conn, username)
