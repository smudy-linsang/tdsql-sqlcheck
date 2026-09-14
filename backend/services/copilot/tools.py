# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：内部工具闭集 T01—T09（CP-W04，DETAIL §5.2）。

工具只由服务器场景工作流调用，CP-1 provider 请求中不发送 tools/functions；
本闭集不是可从网络直接任意调度的 RPC。每次输入 Pydantic extra='forbid'；
ID/索引由 preview 引用子集约束；所有返回附时间/完整性/来源。
工具方法签名统一 execute(actor, approved_context, args, deadline)。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from backend.models.copilot import Evidence
from backend.services.copilot.errors import CopilotError
from backend.services.copilot.evidence import EvidenceCollector
from backend.services.copilot.redaction import detect_sensitive, utf8_len

logger = logging.getLogger("tdsql.copilot.tools")


class ToolContext:
    """一轮已批准的上下文：身份、会话、投影模式、已选引用。"""

    def __init__(self, actor, conn, session: dict, identifiers_allowed: bool,
                 knowledge_store=None):
        self.actor = actor                    # CopilotIdentity
        self.conn = conn
        self.session = session
        self.identifiers_allowed = identifiers_allowed
        self.knowledge_store = knowledge_store
        self.collector = EvidenceCollector(conn, actor, session,
                                           identifiers_allowed=identifiers_allowed)


def _check_deadline(deadline: float):
    if time.monotonic() > deadline:
        raise CopilotError("TURN_TIMEOUT")


# ══════════════════════════════════════════════════════════════════
# T01 search_help —— 本地知识检索（8段，每段≤1200字；总输出≤8KiB）
# ══════════════════════════════════════════════════════════════════

def execute_search_help(ctx: ToolContext, args: dict, deadline: float) -> dict:
    _check_deadline(deadline)
    query = str(args.get("query") or "")[:1024]
    if ctx.knowledge_store is None or ctx.knowledge_store.bundle is None:
        return {"knowledge": [], "knowledge_status": "MISSING"}
    results = ctx.knowledge_store.bundle.search(query, product_family="TDSQL-MySQL")
    total = 0
    out = []
    for r in results[:8]:
        content = r["content"][:1200]
        total += utf8_len(content)
        if total > 8192:
            break
        out.append({k: r[k] for k in ("knowledge_id", "bundle_id", "source_id",
                                      "title", "section", "authority",
                                      "content_hash")} | {"content": content})
    return {"knowledge": out,
            "knowledge_status": ctx.knowledge_store.status}


# ══════════════════════════════════════════════════════════════════
# T02 explain_rules —— 规则运行时解释（≤10 规则）
# ══════════════════════════════════════════════════════════════════

def execute_explain_rules(ctx: ToolContext, args: dict, deadline: float) -> dict:
    _check_deadline(deadline)
    rule_ids = list(args.get("rule_ids") or [])[:10]
    ev = ctx.collector.read_rules(rule_ids)
    return {"evidence": [ev]}


# ══════════════════════════════════════════════════════════════════
# T03 read_audit_evidence —— 审核历史/元数据任务（含近期摘要≤3条）
# ══════════════════════════════════════════════════════════════════

def execute_read_audit_evidence(ctx: ToolContext, args: dict,
                                deadline: float) -> dict:
    _check_deadline(deadline)
    out = []
    ref = args.get("ref") or {}
    kind = ref.get("kind")
    if kind == "audit_history":
        out.append(ctx.collector.read_audit_history(
            str(ref.get("history_id")), list(ref.get("statement_indexes") or [])[:5]))
    elif kind == "metadata_job":
        ev = ctx.collector.read_metadata_job(
            str(ref.get("job_id")), int(ref.get("offset") or 0),
            min(int(ref.get("limit") or 10), 20))
        out.append(ev)
        # 预览显式选择时才附同库近期终态摘要（≤3条）
        if args.get("include_recent_jobs"):
            conn_id = ev.get("connection_id")
            db = ev.get("database")
            if conn_id and db:
                out.extend(ctx.collector.read_recent_jobs(
                    conn_id, db, str(ref.get("job_id"))))
    else:
        raise CopilotError("INVALID_REQUEST")
    return {"evidence": out}


# ══════════════════════════════════════════════════════════════════
# T04 read_job_status —— 单条任务状态
# ══════════════════════════════════════════════════════════════════

def execute_read_job_status(ctx: ToolContext, args: dict, deadline: float) -> dict:
    _check_deadline(deadline)
    ev = ctx.collector.read_metadata_job(str(args.get("job_id")))
    return {"evidence": [ev]}


# ══════════════════════════════════════════════════════════════════
# T05 read_slow_evidence —— 慢 SQL（1条 SQL、≤20行已有计划）
# ══════════════════════════════════════════════════════════════════

def execute_read_slow_evidence(ctx: ToolContext, args: dict, deadline: float) -> dict:
    _check_deadline(deadline)
    ev = ctx.collector.read_slow_query(str(args.get("slow_id")))
    plan = (ev.get("data") or {}).get("explain_plan")
    if plan:
        ev["data"]["explain_plan"] = "\n".join(plan.splitlines()[:20])
    return {"evidence": [ev]}


# ══════════════════════════════════════════════════════════════════
# T06 read_compare_evidence —— 2 份快照摘要（20项差异）
# ══════════════════════════════════════════════════════════════════

def execute_read_compare_evidence(ctx: ToolContext, args: dict,
                                  deadline: float) -> dict:
    _check_deadline(deadline)
    ids = list(args.get("snapshot_ids") or [])[:2]
    evs = ctx.collector.read_scan_snapshots(ids)
    out = list(evs)
    if len(ids) == 2 and all(e.get("availability") == "AVAILABLE" for e in evs):
        module = (evs[0].get("data") or {}).get("module")
        if module and module == (evs[1].get("data") or {}).get("module"):
            cmp_ev = ctx.collector.read_compare_report(ids[0], ids[1], module)
            if cmp_ev is not None:
                out.append(cmp_ev)
    return {"evidence": out[:4]}


# ══════════════════════════════════════════════════════════════════
# T07 read_tabletype_evidence —— 表类型统计
# ══════════════════════════════════════════════════════════════════

def execute_read_tabletype_evidence(ctx: ToolContext, args: dict,
                                    deadline: float) -> dict:
    _check_deadline(deadline)
    return {"evidence": [ctx.collector.read_table_type_stat(str(args.get("stat_id")))]}


# ══════════════════════════════════════════════════════════════════
# T08 read_gateway_evidence —— 网关报告结构化摘要投影（≤8KiB）
# ══════════════════════════════════════════════════════════════════

def execute_read_gateway_evidence(ctx: ToolContext, args: dict,
                                  deadline: float) -> dict:
    _check_deadline(deadline)
    return {"evidence": [ctx.collector.read_gateway_report(str(args.get("report_id")))]}


# ══════════════════════════════════════════════════════════════════
# T09 validate_sql_text —— 纯文本 SQL 复核（受控子进程，见 workflow）
# ══════════════════════════════════════════════════════════════════

def build_text_validation_payload(sql: str, instance_type: str,
                                  rule_overrides_hash: str) -> dict:
    """构造 T09 子进程输入（固定模块/序列化参数，禁止用户选择程序）。"""
    if utf8_len(sql) > 32 * 1024:
        raise CopilotError("CONTEXT_TOO_LARGE",
                           message="候选 SQL 超过 32KiB 复核上限")
    return {
        "sql": sql,
        "instance_type": instance_type,
        "rule_overrides_hash": rule_overrides_hash,
    }
