# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：场景工作流编排（CP-W06，DETAIL §5.3/§7.4/§11.3）。

固定编排（单轮证据收集至多 4 次工具调用，T09 另计最多 2 次）：
  USAGE_HELP / DIAGNOSTIC_HELP: T01
  RULE_EXPLAIN: T02 + 必要时 T03 + T01
  SQL_ADVISE: T02 + 已批准 T03/USER_DRAFT + T01 → 模型 → T09（最多2个候选）
  AUDIT_EXPLAIN: T03 + T02 + T01
  JOB_TROUBLESHOOT: T04 + 可选 T03 近期摘要 + T01
  SLOW_EXPLAIN: T05 + T01
  COMPARE_EXPLAIN: T06 + T01
  TABLETYPE_EXPLAIN: T07 + T01
  GATEWAY_EXPLAIN: T08 + T01

整轮单调 deadline（受理即冻结）；阶段预算：取证/检索 ≤10s、模型 ≤60s、
后处理 ≤10s、发布预留 5s。所有模型回答均为建议；LOCAL_ONLY/DEGRADED/FAILED
语义见 §11.1。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Optional

from backend.models.copilot import ModelAnswer, TurnKind
from backend.services.copilot import output as out_mod
from backend.services.copilot import providers as prov_mod
from backend.services.copilot import routing as routing_mod
from backend.services.copilot.errors import CopilotError
from backend.services.copilot.knowledge import store as knowledge_store
from backend.services.copilot.policy import Limits, load_policy
from backend.services.copilot.redaction import detect_sensitive, truncate_utf8
from backend.services.copilot.repository import (
    AttemptRepo, AuditRepo, ProviderRepo, TurnRepo, new_id,
)
from backend.services.copilot.tools import (
    ToolContext, build_text_validation_payload, execute_explain_rules,
    execute_read_audit_evidence, execute_read_compare_evidence,
    execute_read_gateway_evidence, execute_read_job_status,
    execute_read_slow_evidence, execute_read_tabletype_evidence,
    execute_search_help,
)

logger = logging.getLogger("tdsql.copilot.workflow")

PHASE_BUDGETS = {"evidence": 10, "model": 60, "post": 10, "publish": 5}


class TurnExecutor:
    """单轮执行器：被 runner 领取后执行，持有 fencing token。"""

    def __init__(self, conn, turn: dict, preview: dict, session: dict,
                 identity_like: dict, crypto_keyring, request_id: str = ""):
        self.conn = conn
        self.turn = turn
        self.preview = preview
        self.session = session
        self.identity = identity_like      # {"username","role","subject_id"}
        self.keyring = crypto_keyring
        self.request_id = request_id
        self.started = time.monotonic()
        self.deadline_at = turn.get("deadline_at")
        self.attempt_token = turn.get("attempt_token") or ""
        self.model_attempted = False
        self.model_attempts = 0
        self.last_model_error = ""
        self.usage_summary = {"input_tokens": None, "output_tokens": None,
                              "accounting_source": "UNKNOWN"}
        self.total_charged = 0

    # ── 工具 ─────────────────────────────────────────────
    def _remaining(self) -> float:
        """距整轮 deadline 的剩余秒数。"""
        if not self.deadline_at:
            return 90.0
        from datetime import datetime, timezone
        try:
            dl = datetime.fromisoformat(str(self.deadline_at).replace("Z", "+00:00"))
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc)
            return max(0.0, (dl - datetime.now(timezone.utc)).total_seconds())
        except Exception:
            return 90.0

    def _phase_deadline(self, phase: str) -> float:
        budget = PHASE_BUDGETS.get(phase, 10)
        return time.monotonic() + min(budget, max(0.0, self._remaining() - 5))

    def _check_cancel(self):
        if TurnRepo.is_cancel_requested(self.conn, self.turn["id"]):
            raise _TurnCancelled()

    def _publish(self, final_state: str, response: Optional[dict],
                 error_code: Optional[str], error_message: Optional[str],
                 provider_id: Optional[str] = None) -> bool:
        envelope = json.dumps(response, ensure_ascii=False) if response else None
        output_hash = hashlib.sha256(envelope.encode("utf-8")).hexdigest() \
            if envelope else None
        ok = TurnRepo.publish_terminal(
            self.conn, self.turn["id"], self.attempt_token, final_state,
            response_envelope=envelope, output_hash=output_hash,
            charged_tokens=self.total_charged, error_code=error_code,
            error_message=error_message, provider_id=provider_id)
        if ok:
            try:
                from backend.services.copilot.repository import SessionRepo
                SessionRepo.release_activity(self.conn, self.turn["session_id"],
                                             self.turn["id"])
                AuditRepo.record(
                    self.conn, "PUBLISH", self.identity["username"],
                    self.identity.get("subject_id"), "copilot_turns", self.turn["id"],
                    final_state, request_id=self.request_id,
                    session_id=self.turn["session_id"], turn_id=self.turn["id"])
                # 自检回写：PROVIDER_SELFTEST 成功时回写 tested_revision
                from backend.services.copilot.selftest import complete_self_test
                complete_self_test(self.conn, self.turn,
                                   success=(final_state == "SUCCEEDED"))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return ok

    # ── 证据收集（§5.3 固定编排）─────────────────────────
    def _collect(self) -> tuple[list[dict], list[dict], dict]:
        """返回 (evidence, knowledge, projection_info)。"""
        payload = self._request_payload()      # 取证读原始请求
        refs = payload.get("source_refs") or []
        scene = self.turn["scene"]
        question = payload.get("question") or ""
        identifiers_allowed = self.preview.get("projection_mode") == "SCHEMA_IDENTIFIERS"
        ctx = ToolContext(self.identity, self.conn, self.session,
                          identifiers_allowed=identifiers_allowed,
                          knowledge_store=knowledge_store)
        deadline = self._phase_deadline("evidence")
        evidence: list[dict] = []
        knowledge: list[dict] = []

        def _t01(q: str):
            nonlocal knowledge
            r = execute_search_help(ctx, {"query": q}, deadline)
            knowledge = r.get("knowledge") or []

        def _add_evs(res: dict):
            for ev in res.get("evidence") or []:
                if len(evidence) < 4:
                    evidence.append(ev)

        if scene == "PROVIDER_SELFTEST":
            # §12.6：自检不带业务资料，也不做知识检索；空集合是设计使然，不是"资料不可用"。
            return [], [], {"identifiers_allowed": False}
        if scene in ("USAGE_HELP", "DIAGNOSTIC_HELP"):
            _t01(question)
        elif scene == "RULE_EXPLAIN":
            rule_ids = []
            for r in refs:
                if r.get("kind") == "rule":
                    rule_ids.extend(r.get("rule_ids") or [])
            if rule_ids:
                _add_evs(execute_explain_rules(ctx, {"rule_ids": rule_ids[:10]},
                                               deadline))
            _t01(question or " ".join(rule_ids))
        elif scene == "AUDIT_EXPLAIN":
            for r in refs[:2]:
                _add_evs(execute_read_audit_evidence(ctx, {"ref": r}, deadline))
            rule_ids = _rule_ids_from_evidence(evidence)
            if rule_ids:
                _add_evs(execute_explain_rules(ctx, {"rule_ids": rule_ids[:10]},
                                               deadline))
            _t01(question)
        elif scene == "JOB_TROUBLESHOOT":
            job_ref = next((r for r in refs if r.get("kind") == "metadata_job"), None)
            if job_ref:
                _add_evs(execute_read_job_status(
                    ctx, {"job_id": job_ref.get("job_id")}, deadline))
            _t01(question or (evidence[0]["data"].get("error_code") if evidence and
                              evidence[0].get("data") else "") or "任务失败排查")
        elif scene == "SLOW_EXPLAIN":
            ref = next((r for r in refs if r.get("kind") == "slow_query"), None)
            if ref:
                _add_evs(execute_read_slow_evidence(
                    ctx, {"slow_id": ref.get("slow_id")}, deadline))
            _t01(question)
        elif scene == "COMPARE_EXPLAIN":
            ref = next((r for r in refs if r.get("kind") == "scan_snapshot"), None)
            if ref:
                _add_evs(execute_read_compare_evidence(
                    ctx, {"snapshot_ids": ref.get("snapshot_ids") or []}, deadline))
            _t01(question)
        elif scene == "TABLETYPE_EXPLAIN":
            ref = next((r for r in refs if r.get("kind") == "table_type_stat"), None)
            if ref:
                _add_evs(execute_read_tabletype_evidence(
                    ctx, {"stat_id": ref.get("stat_id")}, deadline))
            _t01(question)
        elif scene == "GATEWAY_EXPLAIN":
            ref = next((r for r in refs if r.get("kind") == "gateway_report"), None)
            if ref:
                _add_evs(execute_read_gateway_evidence(
                    ctx, {"report_id": ref.get("report_id")}, deadline))
            _t01(question)
        elif scene == "SQL_ADVISE":
            for r in refs[:2]:
                if r.get("kind") == "rule":
                    _add_evs(execute_explain_rules(
                        ctx, {"rule_ids": (r.get("rule_ids") or [])[:10]}, deadline))
            _t01(question)
        return evidence, knowledge, {"identifiers_allowed": identifiers_allowed}

    def _payload(self) -> dict:
        """出站体：本轮封存的模型投影（§12.3）。

        这是预览时按三闸脱敏、预算裁剪后封存的那一份，也是用户在预览里看到的那一份；
        runner 不得在此之上再添加来源。
        """
        from backend.services.copilot import crypto as crypto_mod
        raw = crypto_mod.decrypt(
            self.preview["model_projection_envelope"], "copilot_previews",
            self.preview["id"], "model_projection_envelope",
            owner=self.preview["owner_subject_id"], keyring=self.keyring)
        return json.loads(raw)

    def _request_payload(self) -> dict:
        """取证体：用户本轮实际提交的原始请求（question / source_refs / draft / page_key）。

        仅用于 runner 侧按 §5.2 工具合同重新取数，**绝不作为出站体**。
        投影里没有 source_refs，因此取证必须读这一份，否则证据链恒空。
        """
        from backend.services.copilot import crypto as crypto_mod
        raw = crypto_mod.decrypt(
            self.preview["payload_envelope"], "copilot_previews",
            self.preview["id"], "payload_envelope",
            owner=self.preview["owner_subject_id"], keyring=self.keyring)
        return json.loads(raw)

    # ── 模型调用 ─────────────────────────────────────────
    def _call_model(self, payload_json: dict) -> Optional[dict]:
        """主备调用：最多 2 次 HTTP；返回原始 answer dict 或 None。"""
        route_snap = _safe_json(self.turn.get("route_snapshot_envelope")) or {}
        if route_snap.get("mode") != "ROUTE":
            return None
        policy = load_policy()
        for leg in ("primary", "fallback"):
            leg_info = route_snap.get(leg)
            if not leg_info:
                continue
            if self.model_attempts >= 2:
                break
            self._check_cancel()
            provider = self._load_provider_frozen(leg_info)
            if provider is None:
                continue
            if routing_mod.provider_in_cooldown(provider):
                continue
            endpoint = policy.get(provider["endpoint_id"])
            if endpoint is None:
                continue
            answer = self._single_attempt(provider, endpoint, payload_json)
            if answer is not None:
                return answer
            if self.last_model_error and \
                    not routing_mod.can_switch(self.last_model_error):
                break  # 不可切换错误：停止该路径
        return None

    def _load_provider_frozen(self, leg_info: dict) -> Optional[dict]:
        """运行轮次冻结旧版本：revision 不符即失败关闭，不在途中换 key。"""
        p = ProviderRepo.get(self.conn, leg_info["provider_id"])
        if p is None:
            return None
        if int(p.get("revision") or 0) != int(leg_info.get("provider_revision") or 0):
            self.last_model_error = "PROVIDER_CONFIG_INVALID"
            return None
        if not int(p.get("enabled") or 0):
            # §12.6：自检的目的就是"启用前先验证"，此时 provider 必然 enabled=0。
            # 仅对 PROVIDER_SELFTEST 轮放行；普通业务轮仍要求 enabled=1。
            if self.turn.get("turn_kind") != TurnKind.PROVIDER_SELFTEST.value:
                return None
        return p

    def _single_attempt(self, provider: dict, endpoint: dict,
                        payload_json: dict) -> Optional[dict]:
        """单次出域尝试：EGRESS_START 审计 → 发送 → EGRESS_END/失败记账。"""
        from backend.services.copilot import crypto as crypto_mod
        self.model_attempts += 1
        self.model_attempted = True
        secret = None
        if provider.get("auth_mode") == "BEARER":
            if not provider.get("secret_envelope"):
                self.last_model_error = "PROVIDER_CONFIG_INVALID"
                return None
            try:
                secret = crypto_mod.decrypt(
                    provider["secret_envelope"], "copilot_providers",
                    provider["id"], "secret_envelope", owner="SYSTEM",
                    crypto_revision=int(provider.get("revision") or 1),
                    keyring=self.keyring)
            except Exception:
                self.last_model_error = "COPILOT_CRYPTO_UNAVAILABLE"
                return None
        body = prov_mod.build_request_body(
            provider, prov_mod.SYSTEM_TEMPLATE, payload_json,
            Limits.get("COPILOT_OUTPUT_MAX_TOKENS"))
        body_bytes = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        if body_bytes > Limits.get("COPILOT_CONTEXT_MAX_BYTES"):
            self.last_model_error = "CONTEXT_TOO_LARGE"
            return None

        # EGRESS 策略检查（数据分级/标识符）
        try:
            policy = load_policy()
            policy.egress_check(
                provider["endpoint_id"], self.preview.get("data_class") or "RESTRICTED",
                identifiers=self.preview.get("projection_mode") == "SCHEMA_IDENTIFIERS")
        except CopilotError as e:
            self.last_model_error = e.code
            return None

        url = policy.build_url(provider["endpoint_id"])
        attempt_id = new_id()
        attempt_no = AttemptRepo.next_no(self.conn, self.turn["id"])
        request_body_hash = hashlib.sha256(
            json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()
        # EGRESS_START：最终出域校验及请求构造之后、网络发送之前提交
        AttemptRepo.start(self.conn, {
            "id": attempt_id, "turn_id": self.turn["id"],
            "operator_subject_id": self.identity.get("subject_id") or "",
            "operator": self.identity["username"],
            "provider_id": provider["id"],
            "provider_revision": int(provider.get("revision") or 1),
            "attempt_no": attempt_no})
        AuditRepo.record(
            self.conn, "EGRESS_START", self.identity["username"],
            self.identity.get("subject_id"), "copilot_providers", provider["id"],
            "START", detail={
                "attempt_id": attempt_id, "attempt_no": attempt_no,
                "request_body_hash": request_body_hash,
                "projection_mode": self.preview.get("projection_mode"),
                "identifiers_included":
                    self.preview.get("projection_mode") == "SCHEMA_IDENTIFIERS",
                "endpoint_id": provider["endpoint_id"]},
            request_id=self.request_id, turn_id=self.turn["id"])
        self.conn.commit()

        import asyncio
        deadline_mono = time.monotonic() + min(
            Limits.get("COPILOT_PROVIDER_TOTAL_SECONDS"),
            max(1.0, self._remaining() - 5))
        # TurnExecutor.run() 由 runner 在专用线程中执行（无线程内运行中 loop），
        # 这里以 asyncio.run 建立短生命周期事件循环执行单次 HTTP 调用。
        result = asyncio.run(
            prov_mod.call_provider(
                url, provider["auth_mode"], secret, body,
                Limits.get("COPILOT_HTTP_CONNECT_SECONDS"),
                Limits.get("COPILOT_HTTP_READ_SECONDS"), deadline_mono))
        # EGRESS_END / 失败记账
        if result.ok:
            answer, usage, truncated = prov_mod.parse_response_body(result.body_text)
            in_tok = (usage or {}).get("prompt_tokens")
            out_tok = (usage or {}).get("completion_tokens")
            self.usage_summary = {
                "input_tokens": in_tok, "output_tokens": out_tok,
                "accounting_source": "PROVIDER" if usage else "UPPER_BOUND"}
            self.total_charged += _charge_tokens(in_tok, out_tok, usage)
            AttemptRepo.finish(
                self.conn, attempt_id, "OK" if answer is not None else "FAILED",
                result.latency_ms, in_tok, out_tok,
                "PROVIDER" if usage else "UPPER_BOUND",
                "OUTPUT_TRUNCATED" if truncated else
                (None if answer is not None else "OUTPUT_INVALID"),
                result.provider_request_id,
                hashlib.sha256(result.body_text.encode("utf-8")).hexdigest())
            self.conn.commit()
            if truncated:
                self.last_model_error = "OUTPUT_TRUNCATED"
                routing_mod.record_failure(self.conn, provider, "OUTPUT_TRUNCATED")
                return None
            if answer is None:
                self.last_model_error = "OUTPUT_INVALID"
                routing_mod.record_failure(self.conn, provider, "OUTPUT_INVALID")
                return None
            routing_mod.record_success(self.conn, provider)
            return answer
        # 失败
        self.last_model_error = result.error_code or "PROVIDER_UNAVAILABLE"
        self.total_charged += _charge_tokens(None, None, None)
        AttemptRepo.finish(
            self.conn, attempt_id, "FAILED", result.latency_ms, None, None,
            "UPPER_BOUND", self.last_model_error, result.provider_request_id, None)
        self.conn.commit()
        routing_mod.record_failure(self.conn, provider, self.last_model_error,
                                   result.retry_after_seconds)
        return None

    # ── 主流程 ───────────────────────────────────────────
    def run(self) -> None:
        """执行一轮（已被 runner 领取为 RUNNING）。"""
        turn_id = self.turn["id"]
        try:
            self._run_inner()
        except _TurnCancelled:
            TurnRepo.publish_terminal(self.conn, turn_id, self.attempt_token,
                                      "CANCELLED", error_code=None,
                                      error_message="用户已取消")
            self.conn.commit()
        except CopilotError as e:
            self._fail(e.code, e.message)
        except Exception as e:
            logger.exception("Copilot 轮次执行异常: %s", e)
            self._fail("INTERNAL_ERROR", "内部错误")

    def _run_inner(self) -> None:
        self._check_cancel()
        evidence, knowledge, proj = self._collect()
        self._check_cancel()
        scene = self.turn["scene"]

        # 本地模板降级素材
        has_evidence = any(e.get("availability") == "AVAILABLE" for e in evidence)
        has_knowledge = bool(knowledge)

        # 模型路径
        payload_json = self._payload()
        route_snap = _safe_json(self.turn.get("route_snapshot_envelope")) or {}
        model_answer: Optional[dict] = None
        if route_snap.get("mode") == "ROUTE":
            model_answer = self._call_model(payload_json)

        if model_answer is not None:
            # 输出校验（结构/引用/断言）
            evidence_ids = {e["evidence_id"] for e in evidence}
            knowledge_ids = {k["knowledge_id"] for k in knowledge}
            allowed_rules = _rule_ids_from_evidence(evidence)
            try:
                answer = out_mod.validate_model_answer(
                    model_answer, evidence_ids, knowledge_ids, set(allowed_rules))
            except CopilotError as e:
                # 降为可核验本地摘要并记录
                answer = None
                self.last_model_error = e.code
            if answer is not None:
                self._publish_model(answer, evidence, knowledge)
                return

        # 本地降级路径
        self._publish_local(evidence, knowledge, has_evidence, has_knowledge)

    def _publish_model(self, answer: ModelAnswer, evidence: list[dict],
                       knowledge: list[dict]) -> None:
        evidence_by_id = {e["evidence_id"]: e for e in evidence}
        claims_rendered = out_mod.render_outcome_claims(answer, evidence_by_id)
        answer_dict = answer.model_dump()
        actions = out_mod.build_action_cards(evidence, answer_dict["sql_candidates"])
        response = {
            "answer": answer_dict,
            "claims_rendered": claims_rendered,
            "sources": _source_cards(evidence, knowledge),
            "actions": actions,
            "model": {"provider_id": self.turn.get("provider_id"),
                      "attempted": True, "failure_code": None},
            "usage": self.usage_summary,
        }
        self._publish("SUCCEEDED", response, None, None)

    def _publish_local(self, evidence: list[dict], knowledge: list[dict],
                       has_evidence: bool, has_knowledge: bool) -> None:
        scene = self.turn["scene"]
        # §12.6：自检不带业务资料，"无证据"是设计使然，不能用 EVIDENCE_UNAVAILABLE 归因。
        if scene == "PROVIDER_SELFTEST":
            self._publish("FAILED", None,
                          self.last_model_error or "PROVIDER_UNAVAILABLE",
                          "自检未能完成：模型不可达或输出不合规")
            return
        route_snap = _safe_json(self.turn.get("route_snapshot_envelope")) or {}
        no_route = route_snap.get("mode") != "ROUTE"
        if scene == "JOB_TROUBLESHOOT":
            answer = out_mod.render_job_troubleshooting(
                evidence[0] if evidence else None, knowledge)
        elif scene == "RULE_EXPLAIN":
            answer = out_mod.render_rule_explanation(
                evidence[0] if evidence else None, knowledge)
        elif scene in ("USAGE_HELP", "DIAGNOSTIC_HELP"):
            answer = out_mod.render_usage_help(
                (self._payload().get("question") or ""), knowledge)
        else:
            answer = out_mod.render_evidence_summary(evidence, knowledge)

        if not has_evidence and not has_knowledge:
            self._publish("FAILED", None,
                          "EVIDENCE_UNAVAILABLE",
                          "所选资料不可用，请回到原页面确认后重试")
            return
        state = "LOCAL_ONLY" if no_route else "DEGRADED"
        actions = out_mod.build_action_cards(evidence, [])
        response = {
            "answer": answer,
            "claims_rendered": [],
            "sources": _source_cards(evidence, knowledge),
            "actions": actions,
            "model": {"provider_id": None, "attempted": self.model_attempted,
                      "failure_code": self.last_model_error or None},
            "usage": self.usage_summary,
        }
        self._publish(state, response, None, None)

    def _fail(self, code: str, message: str) -> None:
        self._publish("FAILED", None, code, message)


class _TurnCancelled(Exception):
    pass


def _charge_tokens(in_tok, out_tok, usage) -> int:
    """结算：有可信 usage 按实际；缺失按每次实际尝试 19,456 保守上界。"""
    if usage and in_tok is not None and out_tok is not None:
        try:
            return int(in_tok) + int(out_tok)
        except (TypeError, ValueError):
            pass
    return 19456


def _safe_json(text) -> Optional[dict]:
    if not text:
        return None
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _rule_ids_from_evidence(evidence: list[dict]) -> list[str]:
    ids: list[str] = []
    for ev in evidence:
        if ev.get("source_kind") == "RULE_RUNTIME":
            for r in (ev.get("data") or {}).get("rules", []):
                if r.get("rule_id"):
                    ids.append(r["rule_id"])
        for v in (ev.get("data") or {}).get("selected_results", []) or []:
            for viol in v.get("violations") or []:
                if viol.get("rule_id"):
                    ids.append(viol["rule_id"])
    return list(dict.fromkeys(ids))


def _source_cards(evidence: list[dict], knowledge: list[dict]) -> list[dict]:
    """来源卡片（≤12 个，每项 excerpt≤1024 字节）。"""
    cards = []
    for ev in evidence[:4]:
        cards.append({
            "evidence_id": ev.get("evidence_id"),
            "source_kind": ev.get("source_kind"),
            "source_id": ev.get("source_id"),
            "available": ev.get("availability") == "AVAILABLE",
            "observed_at": ev.get("observed_at"),
            "completeness": ev.get("completeness"),
            "excerpt": truncate_utf8(
                json.dumps(ev.get("data") or {}, ensure_ascii=False,
                           default=str), 1024),
        })
    for k in knowledge[:8]:
        cards.append({
            "evidence_id": None,
            "source_kind": "KNOWLEDGE_PACK",
            "source_id": k.get("source_id"),
            "available": True,
            "observed_at": None,
            "completeness": "COMPLETE",
            "excerpt": truncate_utf8(k.get("content", ""), 1024),
        })
    return cards[:12]
