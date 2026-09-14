# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：Copilot 持久层（CP-W02，DETAIL §10.2/§11.2）。

统一锁序：runtime(id=1) → users/subject（需要写账户时）→ session →
user/global 预算（principal 字典序）→ turn。所有写活动状态路径同一顺序；
事务内禁止网络和目标库访问；授权/版本用新鲜窄查询。

A组表（copilot_subjects/copilot_runtime）故障属核心失败关闭；B组九表故障
由调用方按 §10.4 传播为 COPILOT_SCHEMA_UNAVAILABLE。本层不做权限判定
（权限在 authz），只做原子读写与完整性约束。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from backend.services.database import _get_connection, ensure_db

logger = logging.getLogger("tdsql.copilot.repo")


def utcnow6() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def new_id() -> str:
    return uuid.uuid4().hex


def _row(r) -> Optional[dict]:
    return dict(r) if r is not None else None


# ══════════════════════════════════════════════════════════════════
# A组：copilot_runtime 全局控制行
# ══════════════════════════════════════════════════════════════════

class RuntimeRepo:
    """A组 runtime 行：跨进程受理/空间锁与模块结构状态。"""

    @staticmethod
    def get(conn, for_update: bool = False) -> Optional[dict]:
        sql = ("SELECT id, runner_id, heartbeat_at, accepting, config_revision, "
               "settings_json, content_used_bytes, content_reserved_bytes, updated_at, "
               "module_schema_state, module_schema_revision, module_schema_epoch, "
               "module_reconciled_epoch, module_schema_checked_at "
               "FROM copilot_runtime WHERE id = 1")
        if for_update:
            sql += " FOR UPDATE"
        return _row(conn.execute(sql).fetchone())

    @staticmethod
    def settings(conn, for_update: bool = False) -> dict:
        row = RuntimeRepo.get(conn, for_update=for_update)
        if not row:
            return {}
        try:
            return json.loads(row.get("settings_json") or "{}")
        except Exception:
            return {}

    @staticmethod
    def save_settings(conn, settings: dict, expected_revision: int) -> bool:
        cur = conn.execute(
            "UPDATE copilot_runtime SET settings_json = ?, "
            "config_revision = config_revision + 1, updated_at = UTC_TIMESTAMP(6) "
            "WHERE id = 1 AND config_revision = ?",
            (json.dumps(settings, ensure_ascii=False), expected_revision))
        return cur.rowcount == 1

    @staticmethod
    def heartbeat(runner_id: str) -> None:
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE copilot_runtime SET runner_id = ?, heartbeat_at = UTC_TIMESTAMP(6) "
                "WHERE id = 1", (runner_id,))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def set_accepting(accepting: bool, runner_id: Optional[str] = None) -> None:
        conn = _get_connection()
        try:
            if runner_id is not None:
                conn.execute(
                    "UPDATE copilot_runtime SET accepting = ?, runner_id = ?, "
                    "heartbeat_at = UTC_TIMESTAMP(6), updated_at = UTC_TIMESTAMP(6) "
                    "WHERE id = 1", (1 if accepting else 0, runner_id))
            else:
                conn.execute(
                    "UPDATE copilot_runtime SET accepting = ?, "
                    "updated_at = UTC_TIMESTAMP(6) WHERE id = 1",
                    (1 if accepting else 0,))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def reserve_storage(conn, nbytes: int, quota_bytes: int) -> bool:
        """runtime 锁内空间预留：used+reserved+n ≤ 配额×0.8（20% 估算余量）。"""
        cur = conn.execute(
            "UPDATE copilot_runtime SET content_reserved_bytes = content_reserved_bytes + ?, "
            "updated_at = UTC_TIMESTAMP(6) WHERE id = 1 AND "
            "content_used_bytes + content_reserved_bytes + ? <= ?",
            (nbytes, nbytes, int(quota_bytes * 0.8)))
        return cur.rowcount == 1

    @staticmethod
    def settle_storage(conn, reserved: int, actual: int) -> None:
        conn.execute(
            "UPDATE copilot_runtime SET "
            "content_reserved_bytes = GREATEST(0, content_reserved_bytes - ?), "
            "content_used_bytes = content_used_bytes + ?, "
            "updated_at = UTC_TIMESTAMP(6) WHERE id = 1", (reserved, actual))


# ══════════════════════════════════════════════════════════════════
# A组：copilot_subjects 代际身份
# ══════════════════════════════════════════════════════════════════

class SubjectRepo:
    """代际身份：owner 比较/授权/额度以 subject_id 为准，username 仅快照。"""

    @staticmethod
    def get_active_by_username(conn, username: str) -> Optional[dict]:
        return _row(conn.execute(
            "SELECT subject_id, username, user_created_at, state, created_at, revoked_at "
            "FROM copilot_subjects WHERE username = ? AND state = 'ACTIVE' "
            "ORDER BY created_at DESC LIMIT 1", (username,)).fetchone())

    @staticmethod
    def get_by_id(conn, subject_id: str) -> Optional[dict]:
        return _row(conn.execute(
            "SELECT subject_id, username, user_created_at, state, created_at, revoked_at "
            "FROM copilot_subjects WHERE subject_id = ?", (subject_id,)).fetchone())

    @staticmethod
    def assign(conn, username: str, user_created_at: str) -> str:
        """为账号分配新 subject（调用方在同事务持 users 行锁）。"""
        sid = new_id()
        conn.execute(
            "INSERT INTO copilot_subjects (subject_id, username, user_created_at, state, "
            "created_at) VALUES (?, ?, ?, 'ACTIVE', UTC_TIMESTAMP(6))",
            (sid, username, user_created_at))
        return sid

    @staticmethod
    def revoke_active(conn, username: str) -> int:
        cur = conn.execute(
            "UPDATE copilot_subjects SET state = 'REVOKED', revoked_at = UTC_TIMESTAMP(6) "
            "WHERE username = ? AND state = 'ACTIVE'", (username,))
        return cur.rowcount

    @staticmethod
    def ensure_for_user(conn, username: str, user_created_at: str) -> str:
        """取 ACTIVE subject；不存在则分配（调用方须在同事务持 users 行锁）。"""
        row = SubjectRepo.get_active_by_username(conn, username)
        if row:
            return row["subject_id"]
        return SubjectRepo.assign(conn, username, user_created_at)


# ══════════════════════════════════════════════════════════════════
# B组：providers / scene_routes
# ══════════════════════════════════════════════════════════════════

class ProviderRepo:
    @staticmethod
    def list(conn) -> list[dict]:
        rows = conn.execute(
            "SELECT id, name, endpoint_id, protocol, model_id, auth_mode, "
            "capabilities_json, enabled, revision, tested_revision, cooldown_until, "
            "consecutive_failures, last_error_code, created_at, updated_at, "
            "secret_envelope IS NOT NULL AS has_secret "
            "FROM copilot_providers ORDER BY name, id").fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get(conn, provider_id: str, for_update: bool = False) -> Optional[dict]:
        sql = ("SELECT id, name, endpoint_id, protocol, model_id, secret_envelope, "
               "auth_mode, capabilities_json, enabled, revision, tested_revision, "
               "cooldown_until, consecutive_failures, last_error_code, created_at, "
               "updated_at FROM copilot_providers WHERE id = ?")
        if for_update:
            sql += " FOR UPDATE"
        return _row(conn.execute(sql, (provider_id,)).fetchone())

    @staticmethod
    def insert(conn, p: dict) -> None:
        conn.execute(
            "INSERT INTO copilot_providers (id, name, endpoint_id, protocol, model_id, "
            "secret_envelope, auth_mode, capabilities_json, enabled, revision, "
            "tested_revision, cooldown_until, consecutive_failures, last_error_code, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,0,1,NULL,NULL,0,NULL,"
            "UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
            (p["id"], p["name"], p["endpoint_id"], p["protocol"], p["model_id"],
             p.get("secret_envelope"), p["auth_mode"], p["capabilities_json"]))

    @staticmethod
    def update_config(conn, p: dict, expected_revision: int) -> bool:
        """配置变更：revision+1、tested_revision=NULL、enabled=0（须重新自检）。"""
        cur = conn.execute(
            "UPDATE copilot_providers SET name = ?, endpoint_id = ?, model_id = ?, "
            "auth_mode = ?, capabilities_json = ?, secret_envelope = ?, "
            "revision = revision + 1, tested_revision = NULL, enabled = 0, "
            "updated_at = UTC_TIMESTAMP(6) WHERE id = ? AND revision = ?",
            (p["name"], p["endpoint_id"], p["model_id"], p["auth_mode"],
             p["capabilities_json"], p.get("secret_envelope"),
             p["id"], expected_revision))
        return cur.rowcount == 1

    @staticmethod
    def set_enabled(conn, provider_id: str, enabled: bool, expected_revision: int) -> bool:
        cur = conn.execute(
            "UPDATE copilot_providers SET enabled = ?, updated_at = UTC_TIMESTAMP(6) "
            "WHERE id = ? AND revision = ?",
            (1 if enabled else 0, provider_id, expected_revision))
        return cur.rowcount == 1

    @staticmethod
    def mark_tested(conn, provider_id: str, revision: int) -> bool:
        """自检回写：仅当当前 revision 匹配（迟到的旧配置结果不能批准新配置）。"""
        cur = conn.execute(
            "UPDATE copilot_providers SET tested_revision = ?, "
            "updated_at = UTC_TIMESTAMP(6) WHERE id = ? AND revision = ?",
            (revision, provider_id, revision))
        return cur.rowcount == 1

    @staticmethod
    def record_health(conn, provider_id: str, failures: int,
                      cooldown_until: Optional[str], error_code: Optional[str]) -> None:
        conn.execute(
            "UPDATE copilot_providers SET consecutive_failures = ?, cooldown_until = ?, "
            "last_error_code = ? WHERE id = ?",
            (failures, cooldown_until, error_code, provider_id))


class RouteRepo:
    @staticmethod
    def get(conn, scene: str) -> Optional[dict]:
        return _row(conn.execute(
            "SELECT scene_code, primary_provider_id, fallback_provider_id, "
            "privacy_profile, revision, enabled, updated_by, updated_at "
            "FROM copilot_scene_routes WHERE scene_code = ?", (scene,)).fetchone())

    @staticmethod
    def list(conn) -> list[dict]:
        rows = conn.execute(
            "SELECT scene_code, primary_provider_id, fallback_provider_id, "
            "privacy_profile, revision, enabled, updated_by, updated_at "
            "FROM copilot_scene_routes ORDER BY scene_code").fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def upsert(conn, scene: str, primary: Optional[str], fallback: Optional[str],
               privacy_profile: str, enabled: bool, updated_by: str,
               expected_revision: int) -> bool:
        existing = RouteRepo.get(conn, scene)
        if existing is None:
            if expected_revision != 0:
                return False
            conn.execute(
                "INSERT INTO copilot_scene_routes (scene_code, primary_provider_id, "
                "fallback_provider_id, privacy_profile, revision, enabled, updated_by, "
                "updated_at) VALUES (?,?,?,?,1,?,?,UTC_TIMESTAMP(6))",
                (scene, primary, fallback, privacy_profile, 1 if enabled else 0,
                 updated_by))
            return True
        if int(existing["revision"]) != expected_revision:
            return False
        cur = conn.execute(
            "UPDATE copilot_scene_routes SET primary_provider_id = ?, "
            "fallback_provider_id = ?, privacy_profile = ?, enabled = ?, "
            "revision = revision + 1, updated_by = ?, updated_at = UTC_TIMESTAMP(6) "
            "WHERE scene_code = ? AND revision = ?",
            (primary, fallback, privacy_profile, 1 if enabled else 0, updated_by,
             scene, expected_revision))
        return cur.rowcount == 1


# ══════════════════════════════════════════════════════════════════
# B组：instance_grants
# ══════════════════════════════════════════════════════════════════

class GrantRepo:
    @staticmethod
    def get(conn, subject_id: str, connection_id: str,
            for_update: bool = False) -> Optional[dict]:
        sql = ("SELECT subject_id, connection_id, username, enabled, approved_by, "
               "approval_ref, revision, updated_at, allow_schema_identifiers, "
               "identifier_approval_ref, approval_state, requested_by_subject_id, "
               "approved_by_subject_id, requested_at, approved_at "
               "FROM copilot_instance_grants WHERE subject_id = ? AND connection_id = ?")
        if for_update:
            sql += " FOR UPDATE"
        return _row(conn.execute(sql, (subject_id, connection_id)).fetchone())

    @staticmethod
    def get_enabled(conn, subject_id: str, connection_id: str) -> Optional[dict]:
        """当前有效授权（APPROVED 且 enabled=1）。"""
        return _row(conn.execute(
            "SELECT * FROM copilot_instance_grants WHERE subject_id = ? "
            "AND connection_id = ? AND enabled = 1 AND approval_state = 'APPROVED'",
            (subject_id, connection_id)).fetchone())

    @staticmethod
    def list_page(conn, limit: int = 20, offset: int = 0,
                  connection_id: str = "") -> list[dict]:
        if connection_id:
            rows = conn.execute(
                "SELECT * FROM copilot_instance_grants WHERE connection_id = ? "
                "ORDER BY updated_at DESC, subject_id LIMIT ? OFFSET ?",
                (connection_id, limit, offset)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM copilot_instance_grants "
                "ORDER BY updated_at DESC, subject_id LIMIT ? OFFSET ?",
                (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def list_for_subject(conn, subject_id: str) -> list[dict]:
        rows = conn.execute(
            "SELECT connection_id, enabled, approval_state, allow_schema_identifiers "
            "FROM copilot_instance_grants WHERE subject_id = ?", (subject_id,)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def upsert_request(conn, subject_id: str, connection_id: str, username: str,
                       requester_subject_id: str, approval_ref: str,
                       allow_schema_identifiers: bool,
                       identifier_approval_ref: Optional[str]) -> None:
        """申请/扩大范围：写 PENDING、enabled=0（立即停止旧授权效力）。"""
        conn.execute(
            "INSERT INTO copilot_instance_grants (subject_id, connection_id, username, "
            "enabled, approved_by, approval_ref, revision, updated_at, "
            "allow_schema_identifiers, identifier_approval_ref, approval_state, "
            "requested_by_subject_id, approved_by_subject_id, requested_at, approved_at) "
            "VALUES (?,?,?,0,NULL,?,1,UTC_TIMESTAMP(6),?,?,'PENDING',?,NULL,"
            "UTC_TIMESTAMP(6),NULL) "
            "ON DUPLICATE KEY UPDATE enabled = 0, approval_ref = VALUES(approval_ref), "
            "allow_schema_identifiers = VALUES(allow_schema_identifiers), "
            "identifier_approval_ref = VALUES(identifier_approval_ref), "
            "approval_state = 'PENDING', requested_by_subject_id = "
            "VALUES(requested_by_subject_id), approved_by = NULL, "
            "approved_by_subject_id = NULL, approved_at = NULL, "
            "requested_at = UTC_TIMESTAMP(6), revision = revision + 1, "
            "updated_at = UTC_TIMESTAMP(6)",
            (subject_id, connection_id, username, approval_ref,
             1 if allow_schema_identifiers else 0, identifier_approval_ref,
             requester_subject_id))

    @staticmethod
    def approve(conn, subject_id: str, connection_id: str, approver_username: str,
                approver_subject_id: str, expected_revision: int) -> bool:
        """PENDING→APPROVED/enabled=1，revision+1（旧 revision 返回 False）。"""
        cur = conn.execute(
            "UPDATE copilot_instance_grants SET approval_state = 'APPROVED', enabled = 1, "
            "approved_by = ?, approved_by_subject_id = ?, approved_at = UTC_TIMESTAMP(6), "
            "revision = revision + 1, updated_at = UTC_TIMESTAMP(6) "
            "WHERE subject_id = ? AND connection_id = ? AND approval_state = 'PENDING' "
            "AND revision = ?",
            (approver_username, approver_subject_id, subject_id, connection_id,
             expected_revision))
        return cur.rowcount == 1

    @staticmethod
    def revoke(conn, subject_id: str, connection_id: str) -> bool:
        """任一有权管理员可立即撤销（不等双签）。"""
        cur = conn.execute(
            "UPDATE copilot_instance_grants SET approval_state = 'REVOKED', enabled = 0, "
            "allow_schema_identifiers = 0, revision = revision + 1, "
            "updated_at = UTC_TIMESTAMP(6) "
            "WHERE subject_id = ? AND connection_id = ? AND approval_state != 'REVOKED'",
            (subject_id, connection_id))
        return cur.rowcount == 1


# ══════════════════════════════════════════════════════════════════
# B组：sessions
# ══════════════════════════════════════════════════════════════════

class SessionRepo:
    @staticmethod
    def get(conn, session_id: str, for_update: bool = False) -> Optional[dict]:
        sql = ("SELECT id, owner_subject_id, owner, scope_kind, connection_id, "
               "database_name, instance_type, initial_page_key, name_snapshot, "
               "name_source, title, revision, active_turn_id, state, created_at, "
               "updated_at, expires_at FROM copilot_sessions WHERE id = ?")
        if for_update:
            sql += " FOR UPDATE"
        return _row(conn.execute(sql, (session_id,)).fetchone())

    @staticmethod
    def insert(conn, s: dict) -> None:
        conn.execute(
            "INSERT INTO copilot_sessions (id, owner_subject_id, owner, scope_kind, "
            "connection_id, database_name, instance_type, initial_page_key, "
            "name_snapshot, name_source, title, revision, active_turn_id, state, "
            "created_at, updated_at, expires_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,1,NULL,'OPEN',"
            "UTC_TIMESTAMP(6),UTC_TIMESTAMP(6),?)",
            (s["id"], s["owner_subject_id"], s["owner"], s["scope_kind"],
             s.get("connection_id"), s.get("database_name"), s["instance_type"],
             s["initial_page_key"], s.get("name_snapshot"), s["name_source"],
             s["title"], s["expires_at"]))

    @staticmethod
    def list_for_owner(conn, owner_subject_id: str, state: Optional[str] = None,
                       limit: int = 20, cursor_updated: Optional[str] = None,
                       cursor_id: Optional[str] = None) -> list[dict]:
        """按 (updated_at, id) 降序游标分页。"""
        where = "owner_subject_id = ?"
        args: list[Any] = [owner_subject_id]
        if state:
            where += " AND state = ?"
            args.append(state)
        if cursor_updated and cursor_id:
            where += " AND (updated_at < ? OR (updated_at = ? AND id < ?))"
            args.extend([cursor_updated, cursor_updated, cursor_id])
        args.append(limit + 1)
        rows = conn.execute(
            f"SELECT id, owner_subject_id, owner, scope_kind, connection_id, "
            f"database_name, instance_type, initial_page_key, name_snapshot, "
            f"name_source, title, revision, active_turn_id, state, created_at, "
            f"updated_at, expires_at FROM copilot_sessions WHERE {where} "
            f"ORDER BY updated_at DESC, id DESC LIMIT ?", args).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def archive(conn, session_id: str, expected_revision: int) -> str:
        """归档：仅无活动轮允许。返回 ok/busy/conflict/archived/not_found。"""
        cur = conn.execute(
            "UPDATE copilot_sessions SET state = 'ARCHIVED', "
            "revision = revision + 1, updated_at = UTC_TIMESTAMP(6) "
            "WHERE id = ? AND state = 'OPEN' AND active_turn_id IS NULL "
            "AND revision = ?", (session_id, expected_revision))
        if cur.rowcount == 1:
            return "ok"
        row = SessionRepo.get(conn, session_id)
        if row is None:
            return "not_found"
        if row["state"] != "OPEN":
            return "conflict" if int(row["revision"]) != expected_revision else "archived"
        if row.get("active_turn_id"):
            return "busy"
        return "conflict"

    @staticmethod
    def touch_activity(conn, session_id: str, active_turn_id: Optional[str]) -> None:
        conn.execute(
            "UPDATE copilot_sessions SET active_turn_id = ?, revision = revision + 1, "
            "updated_at = UTC_TIMESTAMP(6) WHERE id = ?", (active_turn_id, session_id))

    @staticmethod
    def release_activity(conn, session_id: str, turn_id: str) -> None:
        conn.execute(
            "UPDATE copilot_sessions SET active_turn_id = NULL, revision = revision + 1, "
            "updated_at = UTC_TIMESTAMP(6) WHERE id = ? AND active_turn_id = ?",
            (session_id, turn_id))

    @staticmethod
    def mark_expired_batch(conn, limit: int = 100) -> list[str]:
        """保留期：到期先标 EXPIRED（不可读取/出域），返回本批 id。"""
        rows = conn.execute(
            "SELECT id FROM copilot_sessions WHERE state = 'OPEN' "
            "AND expires_at < UTC_TIMESTAMP(6) ORDER BY id LIMIT ?", (limit,)).fetchall()
        ids = [dict(r)["id"] for r in rows]
        for sid in ids:
            conn.execute(
                "UPDATE copilot_sessions SET state = 'EXPIRED', "
                "updated_at = UTC_TIMESTAMP(6) WHERE id = ? AND state = 'OPEN'", (sid,))
        return ids


# ══════════════════════════════════════════════════════════════════
# B组：previews
# ══════════════════════════════════════════════════════════════════

class PreviewRepo:
    @staticmethod
    def get(conn, preview_id: str, for_update: bool = False) -> Optional[dict]:
        sql = ("SELECT id, session_id, owner_subject_id, owner, scene, input_hash, "
               "payload_envelope, evidence_envelope, model_projection_envelope, "
               "snapshot_hash, permission_version, grant_revision, route_revision, "
               "provider_revisions_json, data_class, storage_reserved_bytes, "
               "created_at, expires_at, consumed_turn_id, projection_mode, "
               "identifier_policy_revision, module_schema_epoch "
               "FROM copilot_previews WHERE id = ?")
        if for_update:
            sql += " FOR UPDATE"
        return _row(conn.execute(sql, (preview_id,)).fetchone())

    @staticmethod
    def insert(conn, p: dict) -> None:
        conn.execute(
            "INSERT INTO copilot_previews (id, session_id, owner_subject_id, owner, "
            "scene, input_hash, payload_envelope, evidence_envelope, "
            "model_projection_envelope, snapshot_hash, permission_version, "
            "grant_revision, route_revision, provider_revisions_json, data_class, "
            "storage_reserved_bytes, created_at, expires_at, consumed_turn_id, "
            "projection_mode, identifier_policy_revision, module_schema_epoch) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,UTC_TIMESTAMP(6),?,NULL,?,?,?)",
            (p["id"], p["session_id"], p["owner_subject_id"], p["owner"], p["scene"],
             p["input_hash"], p["payload_envelope"], p["evidence_envelope"],
             p["model_projection_envelope"], p["snapshot_hash"], p["permission_version"],
             p.get("grant_revision"), p.get("route_revision"),
             p["provider_revisions_json"], p["data_class"], p["storage_reserved_bytes"],
             p["expires_at"], p["projection_mode"], p["identifier_policy_revision"],
             p["module_schema_epoch"]))

    @staticmethod
    def consume(conn, preview_id: str, turn_id: str) -> bool:
        cur = conn.execute(
            "UPDATE copilot_previews SET consumed_turn_id = ? "
            "WHERE id = ? AND consumed_turn_id IS NULL", (turn_id, preview_id))
        return cur.rowcount == 1

    @staticmethod
    def count_unconsumed(conn, owner_subject_id: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM copilot_previews WHERE owner_subject_id = ? "
            "AND consumed_turn_id IS NULL AND expires_at > UTC_TIMESTAMP(6)",
            (owner_subject_id,)).fetchone()
        return int(dict(row).get("c", 0)) if row else 0

    @staticmethod
    def release_expired_reservations(conn, limit: int = 100) -> int:
        """到期未消费 preview 释放其空间预留（已消费资料随会话保留）。"""
        rows = conn.execute(
            "SELECT id, storage_reserved_bytes FROM copilot_previews "
            "WHERE consumed_turn_id IS NULL AND expires_at <= UTC_TIMESTAMP(6) "
            "AND storage_reserved_bytes > 0 ORDER BY id LIMIT ?", (limit,)).fetchall()
        n = 0
        for r in rows:
            r = dict(r)
            conn.execute(
                "UPDATE copilot_previews SET storage_reserved_bytes = 0 WHERE id = ?",
                (r["id"],))
            conn.execute(
                "UPDATE copilot_runtime SET content_reserved_bytes = "
                "GREATEST(0, content_reserved_bytes - ?) WHERE id = 1",
                (int(r.get("storage_reserved_bytes") or 0),))
            n += 1
        return n


# ══════════════════════════════════════════════════════════════════
# B组：turns
# ══════════════════════════════════════════════════════════════════

class TurnRepo:
    @staticmethod
    def get(conn, turn_id: str) -> Optional[dict]:
        return _row(conn.execute(
            "SELECT * FROM copilot_turns WHERE id = ?", (turn_id,)).fetchone())

    @staticmethod
    def get_for_update(conn, turn_id: str) -> Optional[dict]:
        return _row(conn.execute(
            "SELECT * FROM copilot_turns WHERE id = ? FOR UPDATE", (turn_id,)).fetchone())

    @staticmethod
    def find_idempotent(conn, owner_subject_id: str, client_request_id: str) -> Optional[dict]:
        return _row(conn.execute(
            "SELECT * FROM copilot_turns WHERE owner_subject_id = ? "
            "AND client_request_id = ?", (owner_subject_id, client_request_id)).fetchone())

    @staticmethod
    def next_sequence(conn, session_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS n FROM copilot_turns "
            "WHERE session_id = ?", (session_id,)).fetchone()
        return int(dict(row).get("n", 1))

    @staticmethod
    def count_active(conn) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM copilot_turns WHERE state IN "
            "('ACCEPTED','RUNNING','CANCEL_REQUESTED')").fetchone()
        return int(dict(row).get("c", 0)) if row else 0

    @staticmethod
    def count_active_for_subject(conn, owner_subject_id: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM copilot_turns WHERE owner_subject_id = ? AND "
            "state IN ('ACCEPTED','RUNNING','CANCEL_REQUESTED')",
            (owner_subject_id,)).fetchone()
        return int(dict(row).get("c", 0)) if row else 0

    @staticmethod
    def insert(conn, t: dict) -> None:
        # 39 列；占位符 18 个（？），其余为固定字面值（状态/阶段/NULL/UTC 时间戳/0）
        conn.execute(
            "INSERT INTO copilot_turns (id, session_id, preview_id, owner, "
            "owner_subject_id, turn_kind, scene, rule_snapshot_hash, "
            "module_schema_epoch, client_request_id, request_hash, sequence_no, "
            "state, phase, attempt_token, runner_id, lease_until, created_at, "
            "deadline_at, updated_at, started_at, finished_at, cancel_requested_at, "
            "provider_id, route_snapshot_envelope, request_envelope, "
            "evidence_envelope, response_envelope, source_hash, output_hash, "
            "reserved_tokens, charged_tokens, error_code, error_message, "
            "feedback_rating, feedback_code, feedback_at, feedback_rule_ids_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'ACCEPTED','WAITING',NULL,NULL,NULL,"
            "UTC_TIMESTAMP(6),?,UTC_TIMESTAMP(6),NULL,NULL,NULL,NULL,?,?,?,NULL,?,NULL,"
            "?,0,NULL,NULL,NULL,NULL,NULL,NULL)",
            (t["id"], t["session_id"], t["preview_id"], t["owner"],
             t["owner_subject_id"], t["turn_kind"], t["scene"],
             t.get("rule_snapshot_hash"), t["module_schema_epoch"],
             t["client_request_id"], t["request_hash"], t["sequence_no"],
             t["deadline_at"], t["route_snapshot_envelope"], t["request_envelope"],
             t["evidence_envelope"], t.get("source_hash"),
             t.get("reserved_tokens", 0)))

    @staticmethod
    def claim_next(conn, runner_id: str, attempt_token: str,
                   lease_seconds: int = 20) -> Optional[dict]:
        """领取最早 ACCEPTED：CAS ACCEPTED→RUNNING + fencing token。"""
        row = conn.execute(
            "SELECT id FROM copilot_turns WHERE state = 'ACCEPTED' "
            "ORDER BY created_at, id LIMIT 1").fetchone()
        if not row:
            return None
        tid = dict(row)["id"]
        cur = conn.execute(
            "UPDATE copilot_turns SET state = 'RUNNING', phase = 'EVIDENCE', "
            "attempt_token = ?, runner_id = ?, started_at = UTC_TIMESTAMP(6), "
            "lease_until = UTC_TIMESTAMP(6) + INTERVAL ? SECOND, "
            "updated_at = UTC_TIMESTAMP(6) WHERE id = ? AND state = 'ACCEPTED'",
            (attempt_token, runner_id, lease_seconds, tid))
        if cur.rowcount != 1:
            return None
        return TurnRepo.get(conn, tid)

    @staticmethod
    def renew_lease(conn, turn_id: str, attempt_token: str,
                    lease_seconds: int = 20) -> bool:
        cur = conn.execute(
            "UPDATE copilot_turns SET lease_until = UTC_TIMESTAMP(6) + INTERVAL ? SECOND, "
            "updated_at = UTC_TIMESTAMP(6) WHERE id = ? AND attempt_token = ? "
            "AND state IN ('RUNNING','CANCEL_REQUESTED')",
            (lease_seconds, turn_id, attempt_token))
        return cur.rowcount == 1

    @staticmethod
    def update_phase(conn, turn_id: str, attempt_token: str, phase: str) -> bool:
        cur = conn.execute(
            "UPDATE copilot_turns SET phase = ?, updated_at = UTC_TIMESTAMP(6) "
            "WHERE id = ? AND attempt_token = ? AND state IN ('RUNNING','CANCEL_REQUESTED')",
            (phase, turn_id, attempt_token))
        return cur.rowcount == 1

    @staticmethod
    def request_cancel(conn, turn_id: str) -> str:
        """请求取消：返回 cancel_requested / already_terminal / not_found。"""
        cur = conn.execute(
            "UPDATE copilot_turns SET state = 'CANCEL_REQUESTED', "
            "cancel_requested_at = UTC_TIMESTAMP(6), updated_at = UTC_TIMESTAMP(6) "
            "WHERE id = ? AND state IN ('ACCEPTED','RUNNING')", (turn_id,))
        if cur.rowcount == 1:
            return "cancel_requested"
        row = TurnRepo.get(conn, turn_id)
        if row is None:
            return "not_found"
        if row["state"] == "CANCEL_REQUESTED":
            return "cancel_requested"
        return "already_terminal"

    @staticmethod
    def is_cancel_requested(conn, turn_id: str) -> bool:
        row = conn.execute(
            "SELECT state FROM copilot_turns WHERE id = ?", (turn_id,)).fetchone()
        return bool(row) and dict(row).get("state") == "CANCEL_REQUESTED"

    @staticmethod
    def publish_terminal(conn, turn_id: str, attempt_token: str, final_state: str,
                         phase: str = "DONE", response_envelope: Optional[str] = None,
                         output_hash: Optional[str] = None,
                         charged_tokens: int = 0, error_code: Optional[str] = None,
                         error_message: Optional[str] = None,
                         provider_id: Optional[str] = None) -> bool:
        """终态发布 CAS：仅当前有效 attempt 且活动态可写；迟到写 rowcount=0。"""
        cur = conn.execute(
            "UPDATE copilot_turns SET state = ?, phase = ?, response_envelope = "
            "COALESCE(?, response_envelope), output_hash = ?, charged_tokens = ?, "
            "error_code = ?, error_message = ?, provider_id = COALESCE(?, provider_id), "
            "finished_at = UTC_TIMESTAMP(6), lease_until = NULL, "
            "updated_at = UTC_TIMESTAMP(6) "
            "WHERE id = ? AND attempt_token = ? AND state IN ('RUNNING','CANCEL_REQUESTED')",
            (final_state, phase, response_envelope, output_hash, charged_tokens,
             error_code, error_message, provider_id, turn_id, attempt_token))
        return cur.rowcount == 1

    @staticmethod
    def force_terminal_admin(conn, turn_id: str, final_state: str,
                             error_code: str) -> bool:
        """恢复/对账路径：活动态 → INTERRUPTED 等终态（不依赖 attempt_token）。"""
        cur = conn.execute(
            "UPDATE copilot_turns SET state = ?, phase = 'DONE', error_code = ?, "
            "finished_at = UTC_TIMESTAMP(6), lease_until = NULL, "
            "updated_at = UTC_TIMESTAMP(6) "
            "WHERE id = ? AND state IN ('ACCEPTED','RUNNING','CANCEL_REQUESTED')",
            (final_state, error_code, turn_id))
        return cur.rowcount == 1

    @staticmethod
    def list_for_session(conn, session_id: str, limit: int = 20,
                         cursor_seq: Optional[int] = None) -> list[dict]:
        """按 sequence 升序游标分页。"""
        if cursor_seq is not None:
            rows = conn.execute(
                "SELECT id, session_id, sequence_no, state, phase, scene, turn_kind, "
                "created_at, deadline_at, started_at, finished_at, error_code, "
                "error_message, feedback_rating, feedback_code, charged_tokens "
                "FROM copilot_turns WHERE session_id = ? AND sequence_no > ? "
                "ORDER BY sequence_no LIMIT ?",
                (session_id, cursor_seq, limit + 1)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, session_id, sequence_no, state, phase, scene, turn_kind, "
                "created_at, deadline_at, started_at, finished_at, error_code, "
                "error_message, feedback_rating, feedback_code, charged_tokens "
                "FROM copilot_turns WHERE session_id = ? ORDER BY sequence_no LIMIT ?",
                (session_id, limit + 1)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def stale_leases(conn, limit: int = 50) -> list[dict]:
        rows = conn.execute(
            "SELECT id, attempt_token, state FROM copilot_turns "
            "WHERE state IN ('RUNNING','CANCEL_REQUESTED') "
            "AND lease_until IS NOT NULL AND lease_until < UTC_TIMESTAMP(6) "
            "ORDER BY id LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def queue_timeouts(conn, queue_seconds: int, limit: int = 50) -> list[dict]:
        rows = conn.execute(
            "SELECT id FROM copilot_turns WHERE state = 'ACCEPTED' AND "
            "created_at < UTC_TIMESTAMP(6) - INTERVAL ? SECOND ORDER BY id LIMIT ?",
            (queue_seconds, limit)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def set_feedback(conn, turn_id: str, rating: int, code: str,
                     rule_ids_json: Optional[str]) -> bool:
        """单轮反馈改写保持幂等（覆盖当前值，不累计点击数）。"""
        cur = conn.execute(
            "UPDATE copilot_turns SET feedback_rating = ?, feedback_code = ?, "
            "feedback_at = UTC_TIMESTAMP(6), feedback_rule_ids_json = ? "
            "WHERE id = ? AND state IN "
            "('SUCCEEDED','LOCAL_ONLY','DEGRADED','FAILED')",
            (rating, code, rule_ids_json, turn_id))
        return cur.rowcount == 1


# ══════════════════════════════════════════════════════════════════
# B组：daily_budgets / provider_attempts / audit_events
# ══════════════════════════════════════════════════════════════════

class BudgetRepo:
    @staticmethod
    def reserve(conn, principal: str, day: str, amount: int, limit: int) -> bool:
        """预留日额度（user/global 两条，principal 字典序由调用方保证）。"""
        conn.execute(
            "INSERT INTO copilot_daily_budgets (principal, day_utc, reserved_tokens, "
            "charged_tokens, updated_at) VALUES (?,?,0,0,UTC_TIMESTAMP(6)) "
            "ON DUPLICATE KEY UPDATE principal = principal", (principal, day))
        cur = conn.execute(
            "UPDATE copilot_daily_budgets SET reserved_tokens = reserved_tokens + ?, "
            "updated_at = UTC_TIMESTAMP(6) WHERE principal = ? AND day_utc = ? "
            "AND reserved_tokens + charged_tokens + ? <= ?",
            (amount, principal, day, amount, limit))
        return cur.rowcount == 1

    @staticmethod
    def settle(conn, principal: str, day: str, reserved: int, charged: int) -> None:
        """终态结算：释放未用预留，按实际/保守上界记账。"""
        conn.execute(
            "UPDATE copilot_daily_budgets SET "
            "reserved_tokens = GREATEST(0, reserved_tokens - ?), "
            "charged_tokens = charged_tokens + ?, updated_at = UTC_TIMESTAMP(6) "
            "WHERE principal = ? AND day_utc = ?", (reserved, charged, principal, day))

    @staticmethod
    def usage_today(conn, principal: str, day: str) -> dict:
        row = conn.execute(
            "SELECT reserved_tokens, charged_tokens FROM copilot_daily_budgets "
            "WHERE principal = ? AND day_utc = ?", (principal, day)).fetchone()
        if not row:
            return {"reserved_tokens": 0, "charged_tokens": 0}
        r = dict(row)
        return {"reserved_tokens": int(r.get("reserved_tokens") or 0),
                "charged_tokens": int(r.get("charged_tokens") or 0)}


class AttemptRepo:
    @staticmethod
    def next_no(conn, turn_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) + 1 AS n FROM copilot_provider_attempts "
            "WHERE turn_id = ?", (turn_id,)).fetchone()
        return int(dict(row).get("n", 1))

    @staticmethod
    def start(conn, a: dict) -> None:
        conn.execute(
            "INSERT INTO copilot_provider_attempts (id, turn_id, operator_subject_id, "
            "operator, provider_id, provider_revision, attempt_no, status, started_at, "
            "finished_at, latency_ms, input_tokens, output_tokens, usage_source, "
            "error_code, provider_request_id, response_digest) "
            "VALUES (?,?,?,?,?,?,?,'STARTED',UTC_TIMESTAMP(6),NULL,NULL,NULL,NULL,"
            "'UNKNOWN',NULL,NULL,NULL)",
            (a["id"], a["turn_id"], a["operator_subject_id"], a["operator"],
             a["provider_id"], a["provider_revision"], a["attempt_no"]))

    @staticmethod
    def finish(conn, attempt_id: str, status: str, latency_ms: Optional[int],
               input_tokens: Optional[int], output_tokens: Optional[int],
               usage_source: str, error_code: Optional[str],
               provider_request_id: Optional[str], response_digest: Optional[str]) -> None:
        conn.execute(
            "UPDATE copilot_provider_attempts SET status = ?, finished_at = "
            "UTC_TIMESTAMP(6), latency_ms = ?, input_tokens = ?, output_tokens = ?, "
            "usage_source = ?, error_code = ?, provider_request_id = ?, "
            "response_digest = ? WHERE id = ?",
            (status, latency_ms, input_tokens, output_tokens, usage_source, error_code,
             provider_request_id, response_digest, attempt_id))


class AuditRepo:
    """审计元数据（无正文；detail_json 白名单 ≤4KiB）。"""

    @staticmethod
    def record(conn, event_type: str, operator: str,
               operator_subject_id: Optional[str], target_type: str,
               target_id: Optional[str], result_code: str,
               detail: Optional[dict] = None, request_id: str = "",
               session_id: Optional[str] = None, turn_id: Optional[str] = None) -> None:
        detail_json = json.dumps(detail or {}, ensure_ascii=False)[:4096]
        conn.execute(
            "INSERT INTO copilot_audit_events (occurred_at, operator, "
            "operator_subject_id, event_type, session_id, turn_id, target_type, "
            "target_id, result_code, detail_json, request_id) "
            "VALUES (UTC_TIMESTAMP(6),?,?,?,?,?,?,?,?,?,?)",
            (operator, operator_subject_id, event_type, session_id, turn_id,
             target_type, target_id, result_code, detail_json,
             (request_id or "")[:32]))

    @staticmethod
    def record_standalone(**kw) -> None:
        """独立连接写审计（配置变更同事务由调用方决定）。"""
        conn = _get_connection()
        try:
            AuditRepo.record(conn, **kw)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def query(conn, days: int = 7, operator: str = "", turn_id: str = "",
              limit: int = 50, offset: int = 0) -> list[dict]:
        where = "occurred_at > UTC_TIMESTAMP(6) - INTERVAL ? DAY"
        args: list[Any] = [days]
        if operator:
            where += " AND operator = ?"
            args.append(operator)
        if turn_id:
            where += " AND turn_id = ?"
            args.append(turn_id)
        args.extend([limit, offset])
        rows = conn.execute(
            f"SELECT id, occurred_at, operator, operator_subject_id, event_type, "
            f"session_id, turn_id, target_type, target_id, result_code, detail_json, "
            f"request_id FROM copilot_audit_events WHERE {where} "
            f"ORDER BY id DESC LIMIT ? OFFSET ?", args).fetchall()
        return [dict(r) for r in rows]
