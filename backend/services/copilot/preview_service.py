# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：资料预览构建与受理（CP-W07 服务层，DETAIL §12.3/§12.4）。

预览阶段（不花模型额度，总 ≤10 秒）：
  · 校验场景/来源引用/草稿；source_refs 严格判别联合；
  · 敏感检测（question/draft 命中即 422 INPUT_SENSITIVE）；
  · 收集有界证据、知识选择、规则冻结、预算裁剪，封存最终资料区；
  · 计算 input_hash（keyring HMAC）与 snapshot_hash（内容+版本冻结）；
  · 任何源版本/权限/规则/路由/部署策略变化 → CONTEXT_CHANGED 重新预览。
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.services.copilot import schema as schema_mod
from backend.services.copilot.authz import (
    CopilotIdentity, check_instance_grant, check_source_menu,
    effective_allow_identifiers, permission_version,
)
from backend.services.copilot.crypto import hmac_digest
from backend.services.copilot.errors import CopilotError
from backend.services.copilot.knowledge import store as knowledge_store
from backend.services.copilot.policy import Limits, allow_schema_identifiers_deploy
from backend.services.copilot.redaction import (
    contains_sensitive, detect_sensitive, truncate_utf8,
)
from backend.services.copilot.repository import new_id
from backend.services.copilot.tools import (
    ToolContext, execute_explain_rules, execute_read_audit_evidence,
    execute_read_compare_evidence, execute_read_gateway_evidence,
    execute_read_job_status, execute_read_slow_evidence,
    execute_read_tabletype_evidence, execute_search_help,
)

PREVIEW_BUDGET_SECONDS = 10
MAX_SOURCE_REFS = 4
MAX_QUESTION_BYTES = 8192

#: 会话历史上限（QC1-B05）：最近 6 条已校验问答，总 ≤4KiB
_HISTORY_MAX_TURNS = 6
_HISTORY_MAX_BYTES = 4096

#: 场景必需来源（缺失 422 SOURCE_REQUIRED，不擅自读取最新记录）
SCENE_REQUIRED_KINDS = {
    "RULE_EXPLAIN": {"rule"},
    "AUDIT_EXPLAIN": {"audit_history", "metadata_job"},
    "JOB_TROUBLESHOOT": {"metadata_job"},
    "SLOW_EXPLAIN": {"slow_query"},
    "COMPARE_EXPLAIN": {"scan_snapshot"},
    "TABLETYPE_EXPLAIN": {"table_type_stat"},
    "GATEWAY_EXPLAIN": {"gateway_report"},
    "SQL_ADVISE": set(),       # 草稿或来源均可
    "USAGE_HELP": set(),
    "DIAGNOSTIC_HELP": set(),
}


def _build_history(conn, session: dict) -> list[dict]:
    """QC1-B05：从同 session 最近 SUCCEEDED 轮构造 ≤4KiB 对话历史。

    每轮重建，不使用供应商保存会话；历史只作上下文，不能作事实证据。
    排除失败/已取消/自检轮；只取同 session 内同实例的已校验问答。
    """
    rows = conn.execute(
        "SELECT p.id AS preview_id, t.id AS turn_id, "
        "p.payload_envelope, t.response_envelope, p.owner_subject_id "
        "FROM copilot_turns t "
        "JOIN copilot_previews p ON t.preview_id = p.id "
        "WHERE t.session_id = ? AND t.state = 'SUCCEEDED' "
        "AND t.turn_kind != 'PROVIDER_SELFTEST' "
        "AND t.response_envelope IS NOT NULL "
        "ORDER BY t.created_at DESC LIMIT ?",
        (session["id"], _HISTORY_MAX_TURNS)).fetchall()
    items: list[dict] = []
    total = 0
    for r in reversed(rows):  # 最旧在前
        try:
            from backend.services.copilot import crypto as crypto_mod
            kr = crypto_mod.load_keyring()
            # QC2-B01：AAD 必须与加密时一致（preview_id + owner）
            payload_raw = crypto_mod.decrypt(
                r["payload_envelope"], "copilot_previews",
                r["preview_id"], "payload_envelope",
                owner=r["owner_subject_id"], keyring=kr)
            q = json.loads(payload_raw).get("question", "")
        except Exception:
            continue
        try:
            ans = json.loads(r["response_envelope"])
            # QC3-B01：生产 response_envelope 结构为 {"answer": {"summary": ...}}
            s = ans.get("summary", "") or (ans.get("answer") or {}).get("summary", "")
        except Exception:
            continue
        if not q or not s:
            continue
        entry = {"question": q[:512], "answer_summary": s[:512]}
        entry_bytes = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
        if total + entry_bytes > _HISTORY_MAX_BYTES:
            break
        items.append(entry)
        total += entry_bytes
    return items


def build_preview(conn, identity: CopilotIdentity, session: dict,
                  req) -> dict:
    """构建预览：返回可入库的预览记录 + 展示视图。"""
    started = time.monotonic()
    deadline = started + PREVIEW_BUDGET_SECONDS
    scene = req.scene.value

    # ── 输入清洗与敏感检测 ──
    question = (req.question or "").strip()
    public_question_id = req.public_question_id
    if public_question_id:
        if question:
            raise CopilotError("INVALID_REQUEST",
                               message="公共固定问题与自由问题不能同时提交")
        fixed = _resolve_public_question(public_question_id)
        if fixed is None:
            raise CopilotError("INVALID_REQUEST", message="公共问题不存在或未批准")
        question = fixed["content"]
    if not question and not req.draft and not req.source_refs:
        raise CopilotError("INVALID_REQUEST", message="问题/草稿/来源至少提供一项")
    if len(question.encode("utf-8")) > MAX_QUESTION_BYTES:
        raise CopilotError("CONTEXT_TOO_LARGE", message="问题超过 8KiB 上限")
    hits = detect_sensitive(question)
    draft_text = req.draft.text if req.draft else ""
    if draft_text:
        hits = hits + detect_sensitive(draft_text)
    if hits:
        raise CopilotError("INPUT_SENSITIVE",
                           message=f"输入疑似包含敏感信息（{','.join(sorted(set(hits)))}），"
                                   "请删除后再试")

    # ── 场景必需来源校验 ──
    refs = req.source_refs or []
    if len(refs) > MAX_SOURCE_REFS:
        raise CopilotError("CONTEXT_TOO_LARGE", message="单轮最多 4 个来源")
    required = SCENE_REQUIRED_KINDS.get(scene, set())
    if required and not any(r.get("kind") in required for r in refs):
        raise CopilotError("SOURCE_REQUIRED",
                           message=f"本场景需要选择 {sorted(required)} 类型来源")
    if req.include_recent_jobs and scene != "JOB_TROUBLESHOOT":
        raise CopilotError("INVALID_REQUEST",
                           message="仅 JOB_TROUBLESHOOT 支持近期任务摘要")
    if req.include_recent_jobs and not any(
            r.get("kind") == "metadata_job" for r in refs):
        raise CopilotError("INVALID_REQUEST",
                           message="include_recent_jobs 需要当前任务引用")

    # ── 逐源权限（闸二+闸三）与实例一致性 ──
    identifiers_allowed = False
    session_conn = session.get("connection_id")
    for r in refs:
        kind = r.get("kind")
        check_source_menu(identity, kind)
        if session.get("scope_kind") == "INSTANCE" and session_conn:
            check_instance_grant(conn, identity, session_conn)
    if req.draft and session.get("scope_kind") == "INSTANCE" and session_conn:
        check_instance_grant(conn, identity, session_conn)

    # §4.4 三闸（M-01 整改）：部署 → 逐实例 → 端点能力，任一不满足即 ALIASED。
    # 端点闸在预览即评估主备两端，不让「预览显示将发标识符、实际出站被拒」不一致。
    deploy_allowed = allow_schema_identifiers_deploy()
    grant_allowed = False
    if session_conn and session.get("scope_kind") == "INSTANCE":
        grant_allowed = effective_allow_identifiers(conn, identity, session_conn)
    identifiers_allowed = bool(deploy_allowed and grant_allowed)

    # 路由/配置版本冻结（先解析出主备，供端点闸与快照使用）
    from backend.services.copilot.routing import resolve_route, route_snapshot
    from backend.services.copilot.policy import load_policy, policy_available
    route_info = None
    route_rev = None
    provider_revisions: dict[str, int] = {}
    try:
        if policy_available():
            route_info = resolve_route(conn, scene, load_policy())
    except Exception:
        route_info = None

    # 端点闸：主备两端都必须 allows_schema_identifiers=true 才放行真实名称
    endpoint_allowed = False
    if identifiers_allowed and route_info:
        try:
            pol = load_policy()
            legs = [route_info["primary"]] + (
                [route_info["fallback"]] if route_info.get("fallback") else [])
            endpoint_allowed = all(
                bool((pol.get(p["endpoint_id"]) or {}).get(
                    "allows_schema_identifiers")) for p in legs)
        except Exception:
            endpoint_allowed = False
    if identifiers_allowed and not endpoint_allowed:
        identifiers_allowed = False
    projection_mode = "SCHEMA_IDENTIFIERS" if identifiers_allowed else "ALIASED"

    # ── 证据收集（预览即封存）──
    ctx = ToolContext(identity, conn, session,
                      identifiers_allowed=identifiers_allowed,
                      knowledge_store=knowledge_store)
    evidence: list[dict] = []
    if scene in ("USAGE_HELP", "DIAGNOSTIC_HELP"):
        from backend.services.copilot.knowledge import _RULE_ID_RE
        rids = _RULE_ID_RE.findall(question)
        if rids:
            ev = execute_explain_rules(
                ctx, {"rule_ids": rids[:5]}, deadline).get("evidence") or []
            evidence.extend(ev)
    elif scene == "RULE_EXPLAIN":
        for r in refs:
            if r.get("kind") == "rule":
                ev = execute_explain_rules(
                    ctx, {"rule_ids": (r.get("rule_ids") or [])[:10]},
                    deadline)["evidence"]
                evidence.extend(ev)
    elif scene in ("AUDIT_EXPLAIN",):
        for r in refs[:2]:
            evidence.extend(execute_read_audit_evidence(ctx, {"ref": r},
                                                        deadline)["evidence"])
    elif scene == "JOB_TROUBLESHOOT":
        job_ref = next((r for r in refs if r.get("kind") == "metadata_job"), None)
        if job_ref:
            evidence.extend(execute_read_job_status(
                ctx, {"job_id": job_ref.get("job_id")}, deadline)["evidence"])
            if req.include_recent_jobs:
                conn_id = evidence[0].get("connection_id") if evidence else None
                db = evidence[0].get("database") if evidence else None
                if conn_id and db:
                    evidence.extend(ctx.collector.read_recent_jobs(
                        conn_id, db, str(job_ref.get("job_id"))))
    elif scene == "SLOW_EXPLAIN":
        ref = next((r for r in refs if r.get("kind") == "slow_query"), None)
        if ref:
            evidence.extend(execute_read_slow_evidence(
                ctx, {"slow_id": ref.get("slow_id")}, deadline)["evidence"])
    elif scene == "COMPARE_EXPLAIN":
        ref = next((r for r in refs if r.get("kind") == "scan_snapshot"), None)
        if ref:
            evidence.extend(execute_read_compare_evidence(
                ctx, {"snapshot_ids": ref.get("snapshot_ids") or []},
                deadline)["evidence"])
    elif scene == "TABLETYPE_EXPLAIN":
        ref = next((r for r in refs if r.get("kind") == "table_type_stat"), None)
        if ref:
            evidence.extend(execute_read_tabletype_evidence(
                ctx, {"stat_id": ref.get("stat_id")}, deadline)["evidence"])
    elif scene == "GATEWAY_EXPLAIN":
        ref = next((r for r in refs if r.get("kind") == "gateway_report"), None)
        if ref:
            evidence.extend(execute_read_gateway_evidence(
                ctx, {"report_id": ref.get("report_id")}, deadline)["evidence"])
    evidence = evidence[:MAX_SOURCE_REFS]

    # ── 知识选择（预览封存最终资料区）──
    knowledge: list[dict] = []
    if question:
        knowledge = execute_search_help(ctx, {"query": question}, deadline)[
            "knowledge"]

    # ── 规则快照冻结 ──
    rule_snapshot_hash = _rule_snapshot_hash()
    perm_ver = permission_version()
    grant_revision = None
    if session_conn:
        from backend.services.copilot.repository import GrantRepo
        g = GrantRepo.get(conn, identity.subject_id, session_conn)
        grant_revision = int(g["revision"]) if g else None

    # 路由/配置版本冻结（上面已解析 route_info，这里只补快照）
    if route_info:
        route_rev = int(route_info["route"]["revision"])
        provider_revisions[route_info["primary"]["id"]] = int(
            route_info["primary"]["revision"])
        if route_info.get("fallback"):
            provider_revisions[route_info["fallback"]["id"]] = int(
                route_info["fallback"]["revision"])

    payload = {
        "question": question,
        "public_question_id": public_question_id,
        "source_refs": refs,
        "draft": ({"kind": req.draft.kind, "text": truncate_utf8(req.draft.text, 32768),
                   "revision": req.draft.revision} if req.draft else None),
        "page_key": req.page_key,
    }
    model_projection = {
        "question": question,
        "history": _build_history(conn, session),
        "evidence": [{"evidence_id": e["evidence_id"],
                      "source_kind": e["source_kind"],
                      "availability": e["availability"],
                      "completeness": e["completeness"],
                      "data": e["data"]} for e in evidence],
        "knowledge": knowledge,
        "allowed_rule_ids": sorted(_allowed_rules(evidence)),
        "output_schema": "copilot_answer/v1",
    }
    data_class = "PUBLIC_HELP" if public_question_id else "INTERNAL_REDACTED"

    # ── 预算裁剪：至少一条必需证据可放下，否则 CONTEXT_TOO_LARGE ──
    proj_bytes = len(json.dumps(model_projection, ensure_ascii=False,
                                default=str).encode("utf-8"))
    if proj_bytes > Limits.get("COPILOT_CONTEXT_MAX_BYTES") * 4:
        raise CopilotError("CONTEXT_TOO_LARGE",
                           message="所选资料超出单次处理上限，请缩小范围")

    snapshot_material = json.dumps({
        "scene": scene, "session_id": session["id"],
        "rule_snapshot_hash": rule_snapshot_hash,
        "permission_version": perm_ver, "grant_revision": grant_revision,
        "route_revision": route_rev, "provider_revisions": provider_revisions,
        "projection_mode": projection_mode,
        "module_schema_epoch": _current_epoch(conn),
        "data_class": data_class,
    }, sort_keys=True, ensure_ascii=False)
    snapshot_hash = hashlib.sha256(snapshot_material.encode("utf-8")).hexdigest()
    input_hash = hmac_digest("copilot-input", json.dumps(
        payload, ensure_ascii=False, sort_keys=True))
    identifier_policy_revision = hashlib.sha256(
        f"{deploy_allowed}|{grant_allowed}|{projection_mode}".encode()).hexdigest()

    record = {
        "id": new_id(),
        "session_id": session["id"],
        "owner_subject_id": identity.subject_id,
        "owner": identity.username,
        "scene": scene,
        "input_hash": input_hash,
        "payload": payload,
        "evidence": evidence,
        "knowledge": knowledge,
        "model_projection": model_projection,
        "snapshot_hash": snapshot_hash,
        "permission_version": perm_ver,
        "grant_revision": grant_revision,
        "route_revision": route_rev,
        "provider_revisions_json": json.dumps(provider_revisions),
        "data_class": data_class,
        "projection_mode": projection_mode,
        "identifier_policy_revision": identifier_policy_revision,
        "module_schema_epoch": _current_epoch(conn),
        "route_snapshot": route_snapshot(route_info),
    }
    return record


def _resolve_public_question(public_question_id: str) -> Optional[dict]:
    if knowledge_store.bundle is None:
        return None
    return knowledge_store.bundle.fixed_public_answer(public_question_id)


def _rule_snapshot_hash() -> str:
    try:
        from backend.engine.checker import RuleChecker
        info = RuleChecker().get_rules_info()
        material = json.dumps(
            [(r.get("rule_id"), r.get("severity"), r.get("enabled"))
             for r in info], ensure_ascii=False)
        return hashlib.sha256(material.encode()).hexdigest()
    except Exception:
        return ""


def _current_epoch(conn) -> int:
    from backend.services.copilot.repository import RuntimeRepo
    row = RuntimeRepo.get(conn)
    return int((row or {}).get("module_schema_epoch") or 1)


def _allowed_rules(evidence: list[dict]) -> set[str]:
    out: set[str] = set()
    for ev in evidence:
        if ev.get("source_kind") == "RULE_RUNTIME":
            for r in (ev.get("data") or {}).get("rules", []):
                if r.get("rule_id"):
                    out.add(r["rule_id"])
    return out
