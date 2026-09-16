# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：Provider 自检（DETAIL §12.5/§12.6）。

自检实现为 turn_kind=PROVIDER_SELFTEST 的内部 turn：
- 不发业务资料；固定合成问题；PUBLIC_HELP 级别；
- 主备尝试上限 1，不借 fallback；
- 同键幂等（owner + provider_id + provider_revision + 模板版本）；
- 输出不合规 → 自检不通过（tested_revision 不写）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from backend.models.copilot import TurnKind
from backend.services.copilot.errors import CopilotError
from backend.services.copilot.repository import (
    AuditRepo, ProviderRepo, TurnRepo, new_id,
)

logger = logging.getLogger("tdsql.copilot.selftest")

_SELFTEST_SCENE = "PROVIDER_SELFTEST"
_SELFTEST_TEMPLATE_VERSION = "v1"

# 固定合成问题：不含任何业务标识符，仅验证连通性与输出格式
_SELFTEST_QUESTION = (
    "请回复以下 JSON：{\"ok\": true, \"model\": \"<你的模型名>\"}。"
    "这是连通性自检，不需要任何业务知识。")


def admit_self_test(conn, identity, provider: dict,
                    client_request_id: str) -> dict:
    """受理 provider 自检：创建 PROVIDER_SELFTEST 内部 turn。

    幂等：同 owner+provider_id+revision+模板版本 的同键重放返回原 turn。
    必须在调用方已持有 provider 行锁（for_update=True）的前提下调用。
    """
    provider_id = provider["id"]
    revision = int(provider["revision"])

    # 代次必须取当前 READY 值：runner 领取时会用 module_schema_epoch 做冻结校验，
    # 写死 0（或任何过期值）会让每一次自检都被判 CONTEXT_CHANGED 而必然 FAILED。
    from backend.services.copilot import schema as schema_mod
    _ready, _r, _st = schema_mod.evaluate_ready(conn)
    if not _ready:
        raise CopilotError("COPILOT_SCHEMA_UNAVAILABLE")
    _epoch = int(_st["module_schema_epoch"])

    # 幂等 hash：owner + provider_id + revision + 模板版本
    idem_key = hashlib.sha256(
        f"selftest:{identity.subject_id}:{provider_id}:{revision}:"
        f"{_SELFTEST_TEMPLATE_VERSION}".encode("utf-8")).hexdigest()[:32]

    existing = TurnRepo.find_idempotent(conn, identity.subject_id, idem_key)
    if existing is not None:
        return {"id": existing["id"], "reused": True}

    # 构造自检专用的 route_snapshot：只含待测 provider，无 fallback
    route_snap = {
        "mode": "ROUTE",
        "primary": {
            "provider_id": provider_id,
            "provider_revision": revision,
        },
        "fallback": None,  # 自检不借 fallback
    }

    # 构造自检专用的 payload（固定合成问题，不含业务资料）
    payload = {
        "question": _SELFTEST_QUESTION,
        "source_refs": [],
        "draft": None,
        "page_key": "",
    }

    from backend.services.copilot import crypto as crypto_mod
    keyring = crypto_mod.load_keyring()
    payload_envelope = crypto_mod.encrypt(
        json.dumps(payload, ensure_ascii=False), "copilot_previews",
        "selftest", "payload_envelope", owner=identity.subject_id,
        keyring=keyring)
    projection_envelope = crypto_mod.encrypt(
        json.dumps(payload, ensure_ascii=False), "copilot_previews",
        "selftest", "model_projection_envelope", owner=identity.subject_id,
        keyring=keyring)

    # 创建内部会话（自检不挂在用户会话上）
    from backend.services.copilot.repository import SessionRepo
    session_id = new_id()
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=120)).strftime(
        "%Y-%m-%d %H:%M:%S.%f")
    SessionRepo.insert(conn, {
        "id": session_id,
        "owner": identity.username,
        "owner_subject_id": identity.subject_id,
        "scope_kind": "GLOBAL_HELP",
        "connection_id": None,
        "database_name": None,
        "instance_type": "unknown",
        "initial_page_key": "copilot-admin",
        "name_snapshot": None,
        "name_source": "selftest",
        "title": f"自检 {provider_id[:8]} r{revision}",
        "expires_at": deadline,
    })

    # 创建 preview（自检也需要 preview 因为 runner 会读）
    from backend.services.copilot.repository import PreviewRepo
    preview_id = new_id()
    PreviewRepo.insert(conn, {
        "id": preview_id,
        "session_id": session_id,
        "owner_subject_id": identity.subject_id,
        "owner": identity.username,
        "scene": _SELFTEST_SCENE,
        "input_hash": hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False)
            .encode("utf-8")).hexdigest(),
        "payload_envelope": payload_envelope,
        "evidence_envelope": "[]",
        "model_projection_envelope": projection_envelope,
        "snapshot_hash": hashlib.sha256(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest(),
        "permission_version": "selftest",
        "grant_revision": None,
        "route_revision": None,
        "provider_revisions_json": json.dumps({
            "primary": {"provider_id": provider_id,
                         "provider_revision": revision}}),
        "data_class": "PUBLIC_HELP",
        "storage_reserved_bytes": 0,
        "expires_at": deadline,
        "projection_mode": "PUBLIC_HELP",
        "identifier_policy_revision": "selftest",
        "module_schema_epoch": _epoch,
    })

    # 创建 turn
    turn_id = new_id()
    TurnRepo.insert(conn, {
        "id": turn_id,
        "session_id": session_id,
        "preview_id": preview_id,
        "owner": identity.username,
        "owner_subject_id": identity.subject_id,
        "turn_kind": TurnKind.PROVIDER_SELFTEST.value,
        "scene": _SELFTEST_SCENE,
        "rule_snapshot_hash": None,
        "module_schema_epoch": _epoch,
        "client_request_id": idem_key,
        "request_hash": idem_key,
        "sequence_no": 1,
        "deadline_at": deadline,
        "route_snapshot_envelope": json.dumps(route_snap, ensure_ascii=False),
        "request_envelope": payload_envelope,
        "evidence_envelope": "[]",
        "source_hash": "",
        "reserved_tokens": 0,
    })

    AuditRepo.record(conn, "SELFTEST_START", identity.username,
                     identity.subject_id, "copilot_providers", provider_id,
                     "SELFTEST", detail={"revision": revision},
                     turn_id=turn_id)
    return {"id": turn_id, "reused": False}


def complete_self_test(conn, turn: dict, success: bool) -> None:
    """自检完成后回写 tested_revision。

    仅当 turn 是 PROVIDER_SELFTEST 且成功时才回写；
    迟到的旧配置结果不能批准新配置（ProviderRepo.mark_tested 内部已校验）。
    """
    if turn.get("turn_kind") != TurnKind.PROVIDER_SELFTEST.value:
        return
    if not success:
        return
    route_snap = json.loads(turn.get("route_snapshot_envelope") or "{}")
    primary = route_snap.get("primary") or {}
    provider_id = primary.get("provider_id")
    revision = primary.get("provider_revision")
    if not provider_id or not revision:
        return
    ok = ProviderRepo.mark_tested(conn, provider_id, int(revision))
    if ok:
        AuditRepo.record(conn, "SELFTEST_PASS", turn.get("owner", ""),
                         turn.get("owner_subject_id"), "copilot_providers",
                         provider_id, "TESTED",
                         detail={"revision": revision}, turn_id=turn["id"])
        logger.info("Provider %s r%s 自检通过", provider_id, revision)
    else:
        logger.warning("Provider %s r%s 自检结果已过期（revision 不匹配）",
                       provider_id, revision)
