# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：输出合同校验、引用、动作卡与本地降级模板（CP-W05，DETAIL §8）。

- 模型输出严格走 ModelAnswer（extra='forbid'）；引用/规则号/动作类型必须验证；
- outcome_claims 由服务端按事实卡核对，模型不得自填事实值；
- 无证据肯定断言与可疑百分比检测（版本化规则，否定/引用/假设/历史时点不误杀）；
- 动作卡由服务端白名单生成（OPEN_SOURCE/NAVIGATE/COPY_SUGGESTION/
  OPEN_AUDIT_EDITOR），模型不得提供 href；
- 本地确定性模板：render_usage_help / render_rule_explanation /
  render_evidence_summary / render_job_troubleshooting。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from backend.models.copilot import (
    ActionCard, ActionType, ModelAnswer, ROUTE_KEY_CLOSURE,
)
from backend.services.copilot.errors import CopilotError

logger = logging.getLogger("tdsql.copilot.output")

# ══════════════════════════════════════════════════════════════════
# §8.1.1 无证据断言检测（版本化；反例覆盖否定/引用/假设/历史时点）
# ══════════════════════════════════════════════════════════════════

ASSERTION_RULES_VERSION = 1

# 执行类断言（本助手已执行/已修复/已通过审核…）——恒拒绝
_EXECUTION_CLAIM_RE = re.compile(
    r"(?:本助手|助手|我|AI|模型)[^。；\n]{0,12}(?:已|已经)(?:完成)?"
    r"(?:执行|修复|扫描|审核|验收|发布|变更|应用|重建|删除|创建索引|建立索引)")
# 无出处百分比收益
_PERCENT_RE = re.compile(r"(?:提升|改善|降低|减少|加快)\s*(?:约|可达|达到)?\s*\d+(?:\.\d+)?\s*%")
# 否定/引用/假设/历史时点反例（不误杀）
_NEGATION_RE = re.compile(r"(?:不能|不得|无法|并未|没有|未|不代表|不等于|不等于|并非)")
_HYPOTHESIS_RE = re.compile(r"(?:可能|假设|如果|推测|疑似|或许|待验证|待确认)")
_HISTORICAL_RE = re.compile(r"(?:历史|当时|曾经|记录显示|截至)")


def detect_unfounded_assertions(text: str) -> list[str]:
    """返回命中的断言类型清单；否定/假设/历史引用上下文不误报。"""
    hits: list[str] = []
    for m in _EXECUTION_CLAIM_RE.finditer(text or ""):
        ctx = text[max(0, m.start() - 20):m.start()]
        if _NEGATION_RE.search(ctx):
            continue
        hits.append("EXECUTION_CLAIM")
    for m in _PERCENT_RE.finditer(text or ""):
        ctx = text[max(0, m.start() - 24):m.start()]
        if _NEGATION_RE.search(ctx) or _HYPOTHESIS_RE.search(ctx):
            continue
        hits.append("UNFOUNDED_PERCENTAGE")
    return hits


# ══════════════════════════════════════════════════════════════════
# 输出校验与引用核对
# ══════════════════════════════════════════════════════════════════

def validate_model_answer(raw: dict, evidence_ids: set[str],
                          knowledge_ids: set[str],
                          allowed_rule_ids: set[str]) -> ModelAnswer:
    """结构 + 引用 + 断言三层校验；不通过抛 CopilotError(OUTPUT_INVALID)。"""
    try:
        answer = ModelAnswer.model_validate(raw)
    except Exception as e:
        raise CopilotError("OUTPUT_INVALID",
                           message=f"模型输出结构不合规: {type(e).__name__}")
    # 引用核对：evidence_ids/knowledge_ids 必须来自本轮集合
    def _check_ids(ids, valid, what):
        for x in ids:
            if x not in valid:
                raise CopilotError("OUTPUT_INVALID",
                                   message=f"模型引用了不存在的{what}: {x}")
    for f in answer.findings:
        _check_ids(f.evidence_ids, evidence_ids, "证据")
        _check_ids(f.knowledge_ids, knowledge_ids, "知识")
    for s in answer.steps:
        _check_ids(s.evidence_ids, evidence_ids, "证据")
    for c in answer.sql_candidates:
        _check_ids(c.evidence_ids, evidence_ids, "证据")
    # outcome_claims：evidence 引用必须存在（事实值由服务端渲染）
    for oc in answer.outcome_claims:
        if oc.evidence_id not in evidence_ids:
            raise CopilotError("OUTPUT_INVALID",
                               message=f"结果断言引用了不存在的证据: {oc.evidence_id}")
    # 文本断言检测
    all_text = "。".join(
        [answer.summary]
        + [f.text for f in answer.findings]
        + [s.text for s in answer.steps]
        + list(answer.missing_evidence)
        + list(answer.limitations)
        + [c.reason for c in answer.sql_candidates])
    hits = detect_unfounded_assertions(all_text)
    if "EXECUTION_CLAIM" in hits:
        raise CopilotError("OUTPUT_INVALID",
                           message="模型声称执行了操作，已按合同拒绝")
    if "UNFOUNDED_PERCENTAGE" in hits:
        raise CopilotError("OUTPUT_INVALID",
                           message="模型输出了无出处的性能百分比")
    return answer


def render_outcome_claims(answer: ModelAnswer,
                          evidence_by_id: dict[str, dict]) -> list[str]:
    """服务端核对后渲染状态句；当前证据不提供该类事实时剔除并记限制。"""
    rendered: list[str] = []
    for oc in answer.outcome_claims:
        ev = evidence_by_id.get(oc.evidence_id)
        if not ev:
            continue
        data = ev.get("data") or {}
        value = data.get(oc.fact_key, None)
        if value is None:
            continue
        rendered.append(
            f"记录 {oc.evidence_id} 在 {oc.observed_at or ev.get('observed_at') or '采集时点'} "
            f"的 {oc.fact_key} 为 {value}（历史记录，非本轮实测）。")
    return rendered


# ══════════════════════════════════════════════════════════════════
# 动作卡（§8.3 服务端白名单生成）
# ══════════════════════════════════════════════════════════════════

def build_action_cards(evidence: list[dict],
                       sql_candidates: list[dict]) -> list[dict]:
    """按本轮证据与候选生成安全动作卡；模型不提供 URL。"""
    cards: list[ActionCard] = []
    idx = 0
    for ev in evidence[:4]:
        if ev.get("availability") != "AVAILABLE":
            continue
        idx += 1
        cards.append(ActionCard(
            action_id=f"A{idx}", type=ActionType.OPEN_SOURCE,
            label=f"查看原始{_kind_label(ev.get('source_kind', ''))}",
            evidence_id=ev["evidence_id"]))
    for i, _cand in enumerate(sql_candidates[:2]):
        idx += 1
        cards.append(ActionCard(
            action_id=f"A{idx}", type=ActionType.COPY_SUGGESTION,
            label=f"复制候选 {i + 1}", candidate_id=f"C{i + 1}"))
        idx += 1
        cards.append(ActionCard(
            action_id=f"A{idx}", type=ActionType.OPEN_AUDIT_EDITOR,
            label=f"送入审核编辑器（候选 {i + 1}）", candidate_id=f"C{i + 1}"))
    return [c.model_dump() for c in cards]


def _kind_label(kind: str) -> str:
    return {
        "AUDIT_HISTORY": "审核记录", "METADATA_JOB": "任务",
        "SLOW_QUERY": "慢SQL记录", "SCAN_SNAPSHOT": "扫描快照",
        "TABLE_TYPE_STAT": "表类型统计", "GATEWAY_REPORT": "网关报告",
        "RULE_RUNTIME": "规则定义", "USER_DRAFT": "用户资料",
        "KNOWLEDGE_PACK": "知识条目",
    }.get(kind, "记录")


# ══════════════════════════════════════════════════════════════════
# 本地确定性模板（§7.5）：输入同一冻结事实/知识，输出同一 answer schema
# ══════════════════════════════════════════════════════════════════

def _base_answer(summary: str) -> dict:
    return {"schema_version": 1, "summary": summary, "outcome_claims": [],
            "findings": [], "steps": [], "missing_evidence": [],
            "sql_candidates": [], "limitations": []}


def render_usage_help(question: str, knowledge: list[dict]) -> dict:
    ans = _base_answer("以下是本地使用指南中的相关内容（未调用模型）。")
    for k in knowledge[:5]:
        ans["findings"].append({
            "kind": "POLICY",
            "text": f"{k.get('title', '')}：{k.get('content', '')[:300]}",
            "evidence_ids": [], "knowledge_ids": [k.get("knowledge_id", "")]})
    if not knowledge:
        ans["missing_evidence"].append("本地知识库未命中该问题，请查阅使用手册")
        ans["limitations"].append("本地知识库不可用或未收录该主题")
    ans["limitations"].append("本地模板回答，非 AI 分析")
    return ans


def render_rule_explanation(rules_evidence: Optional[dict],
                            knowledge: list[dict]) -> dict:
    ans = _base_answer("以下为规则运行时定义与相关知识（未调用模型）。")
    if rules_evidence and rules_evidence.get("availability") == "AVAILABLE":
        eid = rules_evidence["evidence_id"]
        for r in (rules_evidence.get("data") or {}).get("rules", [])[:10]:
            ans["findings"].append({
                "kind": "FACT",
                "text": f"规则 {r.get('rule_id')}：级别 {r.get('severity')}，"
                        f"适用域 {r.get('scope') or 'all'}；{r.get('description') or ''}",
                "evidence_ids": [eid], "knowledge_ids": []})
            if r.get("fix_suggestion"):
                ans["steps"].append({"text": str(r["fix_suggestion"])[:300],
                                     "risk": "MANUAL_CHANGE", "evidence_ids": [eid]})
    else:
        ans["missing_evidence"].append("规则运行时定义不可用")
    for k in knowledge[:3]:
        ans["findings"].append({
            "kind": "POLICY",
            "text": f"{k.get('title', '')}：{k.get('content', '')[:200]}",
            "evidence_ids": [], "knowledge_ids": [k.get("knowledge_id", "")]})
    ans["limitations"].append("本地模板回答，非 AI 分析；项目规则与厂商语法是不同层级")
    return ans


def render_evidence_summary(evidence: list[dict], knowledge: list[dict]) -> dict:
    ans = _base_answer("已整理所选记录的关键事实（模型不可用或未配置）。")
    for ev in evidence[:4]:
        if ev.get("availability") != "AVAILABLE":
            ans["missing_evidence"].append(
                f"{ev.get('source_kind')} 记录不可用：{ev.get('reason_code') or 'MISSING'}")
            continue
        data = ev.get("data") or {}
        keys = ", ".join(f"{k}={data[k]}" for k in list(data)[:6])
        ans["findings"].append({
            "kind": "FACT",
            "text": f"{ev.get('source_kind')}（{ev.get('evidence_id')}）：{keys}",
            "evidence_ids": [ev["evidence_id"]], "knowledge_ids": []})
    ans["limitations"].append("本地证据摘要，非 AI 分析；未重新连接数据库")
    return ans


def render_job_troubleshooting(job_evidence: Optional[dict],
                               knowledge: list[dict]) -> dict:
    ans = _base_answer("已整理任务记录中的状态与错误信息（模型不可用或未配置）。")
    if job_evidence and job_evidence.get("availability") == "AVAILABLE":
        d = job_evidence.get("data") or {}
        eid = job_evidence["evidence_id"]
        ans["findings"].append({
            "kind": "FACT",
            "text": f"任务状态 {d.get('state')}，阶段 {d.get('phase')}，"
                    f"错误码 {d.get('error_code') or '无'}",
            "evidence_ids": [eid], "knowledge_ids": []})
        if d.get("error_message"):
            ans["findings"].append({
                "kind": "FACT",
                "text": f"错误信息摘要：{str(d['error_message'])[:200]}",
                "evidence_ids": [eid], "knowledge_ids": []})
        # 无 exit/RSS/日志证据时不认定 OOM
        ans["missing_evidence"].append("进程退出信号与资源（RSS/exit code）记录")
        ans["steps"].append({"text": "核对执行器心跳与任务错误码，再决定重试或缩小范围",
                             "risk": "READ_ONLY", "evidence_ids": [eid]})
    else:
        ans["missing_evidence"].append("任务记录不可用")
    for k in knowledge[:3]:
        ans["steps"].append({"text": f"参考：{k.get('title', '')}",
                             "risk": "READ_ONLY",
                             "evidence_ids": []})
    ans["limitations"].append("本地排障摘要，未做根因定论；不能把无证据状态认定必现")
    return ans
