# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：既有记录只读适配器（CP-W04，DETAIL §4.1/§4.2）。

所有适配器：禁止 SELECT * 后删敏感字段（显式列选择）；元数据库查询单次超时 3 秒、
最多 50 行、单轮最多 4 个来源；INSTANCE 来源必须与会话 connection_id 一致
（数据库级来源还须 database 一致）；源对象 connection_id/db 缺失且无法由既有
可信关联确认时返回 MISSING。

结构化证据优先，旧 HTML 不做全量反向解析；慢 SQL 的 explain_plan 只读已存 ≤32KiB
字段，不调用 analyze_explain_by_sql（它会连接目标库）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from backend.services.copilot.errors import CopilotError
from backend.services.copilot.redaction import (
    AliasMapper, detect_sensitive, truncate_utf8, utf8_len,
)
from backend.services.database import _get_connection, ensure_db

logger = logging.getLogger("tdsql.copilot.evidence")

QUERY_TIMEOUT_SECONDS = 3
MAX_ROWS = 50
MAX_SOURCES_PER_TURN = 4
MAX_RECENT_JOBS = 3
MAX_SNAPSHOT_JSON_BYTES = 2 * 1024 * 1024
MAX_GATEWAY_META_BYTES = 128 * 1024
MAX_EXPLAIN_BYTES = 32 * 1024
MAX_PROGRESS_JSON_BYTES = 128 * 1024
MAX_ISSUE_TOTAL_FULL_COMPARE = 2000
MAX_COMPARE_ITEMS = 20


def _row(r) -> Optional[dict]:
    return dict(r) if r is not None else None


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _json_len(conn, table: str, column: str, where: str, args: tuple) -> Optional[int]:
    row = conn.execute(
        f"SELECT OCTET_LENGTH({column}) AS n FROM {table} WHERE {where}", args).fetchone()
    if not row:
        return None
    v = dict(row).get("n")
    return int(v) if v is not None else None


class EvidenceCollector:
    """按预览引用收集有界证据；每个返回值为 §4.1 证据结构字典。"""

    def __init__(self, conn, identity, session: dict,
                 identifiers_allowed: bool = False):
        self.conn = conn
        self.identity = identity
        self.session = session
        self.mapper = AliasMapper()
        self.identifiers_allowed = identifiers_allowed
        self._counter = 0

    def _eid(self) -> str:
        self._counter += 1
        return f"E{self._counter}"

    # ── 会话一致性（§4.2）────────────────────────────────
    def _check_session_binding(self, connection_id: Optional[str],
                               db_name: Optional[str]) -> None:
        if self.session.get("scope_kind") != "INSTANCE":
            return
        want_conn = self.session.get("connection_id")
        if connection_id and want_conn and connection_id != want_conn:
            raise CopilotError("INVALID_REQUEST",
                               message="来源实例与会话实例不一致，请分别建会话解读")
        want_db = self.session.get("database_name")
        if db_name and want_db and db_name != want_db:
            raise CopilotError("INVALID_REQUEST",
                               message="来源数据库与会话数据库不一致，请分别建会话解读")

    def _project_name(self, kind: str, name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        return self.mapper.project_identifier(kind, name, self.identifiers_allowed)

    def _name_context(self, connection_id: Optional[str],
                      name: Optional[str], name_source: str) -> dict:
        """INV-07：连接名称取源记录快照；缺失明示，不以库名/端点冒充。"""
        return {
            "connection_id": connection_id,
            "connection_name": name or None,
            "name_source": name_source if name else "missing",
        }

    # ── T02 规则运行时 ──────────────────────────────────
    def read_rules(self, rule_ids: list[str]) -> dict:
        from backend.engine.checker import RuleChecker
        try:
            info = RuleChecker().get_rules_info()
        except Exception:
            info = []
        by_id = {r.get("rule_id"): r for r in info if isinstance(r, dict)}
        data_rules = []
        for rid in rule_ids[:10]:
            r = by_id.get(rid)
            if not r:
                continue
            data_rules.append({
                "rule_id": rid,
                "enabled": r.get("enabled"),
                "severity": r.get("severity"),
                "category": r.get("category"),
                "scope": r.get("instance_scope") or r.get("scope"),
                "spec_source": r.get("spec_source"),
                "description": r.get("description"),
                "fix_suggestion": r.get("fix_suggestion"),
            })
        return self._evidence("RULE_RUNTIME", "runtime-rules",
                              {"rules": data_rules},
                              completeness="COMPLETE" if data_rules else "UNKNOWN",
                              fact_keys=["rule_id", "enabled", "severity", "scope"])

    # ── T03 审核历史 / 元数据任务 ───────────────────────
    def read_audit_history(self, history_id: str,
                           statement_indexes: list[int]) -> dict:
        row = _row(self.conn.execute(
            "SELECT id, audit_type, connection_id, db_name, total_sql, passed, "
            "failed, error_count, warning_count, pass_rate, created_by, created_at, "
            "rule_set_id, instance_type, instance_type_source, skipped_rules_count, "
            "report_context_json, skipped_objects, skipped_benign, skipped_abnormal, "
            "omitted_results, results_summary, top_violations "
            "FROM audit_history WHERE id = ?", (history_id,)).fetchone())
        if not row:
            return self._missing("AUDIT_HISTORY", history_id)
        self._check_session_binding(row.get("connection_id"), row.get("db_name"))
        ctx = _safe_json(row.get("report_context_json")) or {}
        conn_name, name_source = _extract_context_name(ctx)
        data = {
            "audit_type": row.get("audit_type"),
            "total_sql": row.get("total_sql"),
            "passed": row.get("passed"),
            "failed": row.get("failed"),
            "error_count": row.get("error_count"),
            "warning_count": row.get("warning_count"),
            "pass_rate": row.get("pass_rate"),
            "rule_set_id": row.get("rule_set_id"),
            "instance_type": row.get("instance_type"),
            "skipped_objects": row.get("skipped_objects"),
            "skipped_benign": row.get("skipped_benign"),
            "skipped_abnormal": row.get("skipped_abnormal"),
            "omitted_results": row.get("omitted_results"),
            "results_summary": truncate_utf8(str(row.get("results_summary") or ""), 2048),
            "top_violations": truncate_utf8(str(row.get("top_violations") or ""), 2048),
            "created_at": str(row.get("created_at") or ""),
        }
        # 有界 results_json：先查长度，>2MiB 只给汇总
        size = _json_len(self.conn, "audit_history", "results_json", "id = ?",
                         (history_id,))
        if size is not None and size <= 2 * 1024 * 1024 and statement_indexes:
            rrow = _row(self.conn.execute(
                "SELECT results_json FROM audit_history WHERE id = ?",
                (history_id,)).fetchone())
            items = _safe_json((rrow or {}).get("results_json"))
            if isinstance(items, list):
                picked = []
                for idx in statement_indexes[:5]:
                    if 0 <= idx < len(items):
                        item = items[idx]
                        picked.append(_project_audit_result(item, self.mapper,
                                                            self.identifiers_allowed))
                if picked:
                    data["selected_results"] = picked
                    data["results_truncated"] = size > 0
        completeness = "COMPLETE"
        if row.get("skipped_abnormal"):
            completeness = "PARTIAL"
        ev = self._evidence("AUDIT_HISTORY", str(history_id), data,
                            completeness=completeness,
                            fact_keys=list(data.keys()))
        ev.update(self._name_context(row.get("connection_id"), conn_name, name_source))
        ev["database"] = row.get("db_name")
        ev["instance_type"] = row.get("instance_type") or "unknown"
        ev["observed_at"] = str(row.get("created_at") or "") or None
        return ev

    def read_metadata_job(self, job_id: str, offset: int = 0,
                          limit: int = 20) -> dict:
        row = _row(self.conn.execute(
            "SELECT id, connection_id, db_name, state, phase, error_code, "
            "error_message, created_at, started_at, finished_at, deadline_at, "
            "progress_json, report_id, request_id "
            "FROM metadata_audit_jobs WHERE id = ?", (job_id,)).fetchone())
        if not row:
            return self._missing("METADATA_JOB", job_id)
        self._check_session_binding(row.get("connection_id"), row.get("db_name"))
        # M-05：progress_json 先查长度，≤128KiB 才解析；缺字段必须 null/UNKNOWN
        progress: dict[str, Any] = {}
        pj_len = _json_len(self.conn, "metadata_audit_jobs", "progress_json",
                           "id = ?", (job_id,))
        if pj_len is not None and pj_len <= MAX_PROGRESS_JSON_BYTES:
            pj = _safe_json(row.get("progress_json")) or {}
            for k in ("enumerated_objects", "selected_objects", "extracted_objects",
                      "skipped_objects", "skipped_benign", "skipped_abnormal",
                      "total_statements", "audited_statements"):
                progress[k] = pj.get(k)  # 缺字段保持 null
        data = {
            "state": row.get("state"),
            "phase": row.get("phase"),
            "error_code": row.get("error_code"),
            "error_message": truncate_utf8(str(row.get("error_message") or ""), 1024),
            "created_at": str(row.get("created_at") or ""),
            "started_at": str(row.get("started_at") or "") or None,
            "finished_at": str(row.get("finished_at") or "") or None,
            "progress": progress,
        }
        completeness = "COMPLETE" if row.get("state") in (
            "SUCCEEDED", "FAILED", "CANCELLED") else "PARTIAL"
        ev = self._evidence("METADATA_JOB", str(job_id), data,
                            completeness=completeness,
                            fact_keys=["state", "phase", "error_code"])
        ev["connection_id"] = row.get("connection_id")
        ev["database"] = row.get("db_name")
        ev["observed_at"] = str(row.get("finished_at") or row.get("created_at") or "") or None
        return ev

    def read_recent_jobs(self, connection_id: str, db_name: str,
                         exclude_job_id: str) -> list[dict]:
        """同库近期至多 3 条终态摘要（SUCCEEDED/FAILED/CANCELLED；排除当前 job）。"""
        rows = self.conn.execute(
            "SELECT id, state, phase, error_code, finished_at FROM metadata_audit_jobs "
            "WHERE connection_id = ? AND db_name = ? AND id != ? "
            "AND state IN ('SUCCEEDED','FAILED','CANCELLED') "
            "ORDER BY finished_at DESC, id DESC LIMIT ?",
            (connection_id, db_name, exclude_job_id, MAX_RECENT_JOBS)).fetchall()
        out = []
        for r in rows:
            r = dict(r)
            out.append(self._evidence("METADATA_JOB", str(r["id"]), {
                "state": r.get("state"), "phase": r.get("phase"),
                "error_code": r.get("error_code"),
                "finished_at": str(r.get("finished_at") or "") or None,
            }, completeness="COMPLETE",
                fact_keys=["state", "phase", "error_code"],
                note="recent_terminal"))
        return out

    # ── T05 慢 SQL ──────────────────────────────────────
    def read_slow_query(self, slow_id: str) -> dict:
        row = _row(self.conn.execute(
            "SELECT id, connection_id, db_name, fingerprint, avg_time_ms, "
            "max_time_ms, rows_examined, rows_sent, lock_time_ms, exec_count, "
            "first_seen, last_seen, problem_type, severity, root_cause, suggestion, "
            "scan_task_id FROM slow_queries WHERE id = ?", (slow_id,)).fetchone())
        if not row:
            return self._missing("SLOW_QUERY", slow_id)
        conn_id = row.get("connection_id")
        # 归属缺失时才查可信任务归属；两者非空却冲突拒绝
        if not conn_id and row.get("scan_task_id"):
            trow = _row(self.conn.execute(
                "SELECT connection_id FROM scan_tasks WHERE id = ?",
                (row["scan_task_id"],)).fetchone())
            if trow:
                conn_id = trow.get("connection_id")
        self._check_session_binding(conn_id, row.get("db_name"))
        # 已存 EXPLAIN 计划（≤32KiB，不连接目标库）
        explain_plan = None
        erow = _row(self.conn.execute(
            "SELECT OCTET_LENGTH(distributed_analysis) AS n, distributed_analysis "
            "FROM slow_queries WHERE id = ?", (slow_id,)).fetchone())
        if erow and erow.get("n") is not None and int(erow["n"]) <= MAX_EXPLAIN_BYTES:
            explain_plan = truncate_utf8(str(erow.get("distributed_analysis") or ""),
                                         MAX_EXPLAIN_BYTES) or None
        data = {
            "db_name": self._project_name("DB", row.get("db_name")),
            "fingerprint": truncate_utf8(str(row.get("fingerprint") or ""), 1024),
            "avg_time_ms": row.get("avg_time_ms"),
            "max_time_ms": row.get("max_time_ms"),
            "rows_examined": row.get("rows_examined"),
            "rows_sent": row.get("rows_sent"),
            "lock_time_ms": row.get("lock_time_ms"),
            "exec_count": row.get("exec_count"),
            "first_seen": row.get("first_seen"),
            "last_seen": row.get("last_seen"),
            "problem_type": row.get("problem_type"),
            "severity": row.get("severity"),
            "explain_plan": explain_plan,
            "explain_available": explain_plan is not None,
        }
        ev = self._evidence("SLOW_QUERY", str(slow_id), data,
                            completeness="COMPLETE",
                            fact_keys=list(data.keys()))
        ev["connection_id"] = conn_id
        ev["database"] = row.get("db_name")
        ev["observed_at"] = row.get("last_seen") or None
        return ev

    # ── T06 扫描快照对比 ────────────────────────────────
    def read_scan_snapshots(self, snapshot_ids: list[str]) -> list[dict]:
        out = []
        for sid in snapshot_ids[:2]:
            row = _row(self.conn.execute(
                "SELECT id, module, connection_id, connection_name, db_name, "
                "scan_label, scan_started_at, scan_finished_at, object_total, "
                "issue_total, error_count, warning_count, truncated, "
                "truncated_count, schema_version, rule_set_id, instance_type, "
                "OCTET_LENGTH(snapshot_json) AS snap_bytes "
                "FROM scan_snapshots WHERE id = ?", (sid,)).fetchone())
            if not row:
                out.append(self._missing("SCAN_SNAPSHOT", sid))
                continue
            self._check_session_binding(row.get("connection_id"), row.get("db_name"))
            data = {
                "module": row.get("module"),
                "scan_label": row.get("scan_label"),
                "scan_started_at": str(row.get("scan_started_at") or ""),
                "scan_finished_at": str(row.get("scan_finished_at") or ""),
                "object_total": row.get("object_total"),
                "issue_total": row.get("issue_total"),
                "error_count": row.get("error_count"),
                "warning_count": row.get("warning_count"),
                "truncated": bool(row.get("truncated")),
                "truncated_count": row.get("truncated_count"),
                "rule_set_id": row.get("rule_set_id"),
                "snapshot_bytes": row.get("snap_bytes"),
            }
            completeness = "PARTIAL" if row.get("truncated") else "COMPLETE"
            ev = self._evidence("SCAN_SNAPSHOT", str(sid), data,
                                completeness=completeness,
                                fact_keys=list(data.keys()))
            ev.update(self._name_context(row.get("connection_id"),
                                         row.get("connection_name"), "snapshot"))
            ev["database"] = row.get("db_name")
            ev["instance_type"] = row.get("instance_type") or "unknown"
            ev["observed_at"] = str(row.get("scan_finished_at") or "") or None
            out.append(ev)
        return out

    def read_compare_report(self, base_snapshot_id: str, target_snapshot_id: str,
                            module: str) -> Optional[dict]:
        """精确匹配的既有比较留档摘要（≤128KiB）；无法证明版本匹配返回 None。"""
        row = _row(self.conn.execute(
            "SELECT id, module, connection_id, connection_name, db_name, "
            "base_total, target_total, fixed_count, new_count, remain_count, "
            "changed_count, fix_rate, base_scan_at, target_scan_at, "
            "OCTET_LENGTH(summary_json) AS sz "
            "FROM scan_compare_reports WHERE base_snapshot_id = ? AND "
            "target_snapshot_id = ? AND module = ? ORDER BY id DESC LIMIT 1",
            (base_snapshot_id, target_snapshot_id, module)).fetchone())
        if not row or int(row.get("sz") or 0) > MAX_GATEWAY_META_BYTES:
            return None
        data = {
            "base_total": row.get("base_total"),
            "target_total": row.get("target_total"),
            "fixed_count": row.get("fixed_count"),
            "new_count": row.get("new_count"),
            "remain_count": row.get("remain_count"),
            "changed_count": row.get("changed_count"),
            "fix_rate": row.get("fix_rate"),
            "base_scan_at": str(row.get("base_scan_at") or ""),
            "target_scan_at": str(row.get("target_scan_at") or ""),
        }
        ev = self._evidence("SCAN_SNAPSHOT",
                            f"cmp-{base_snapshot_id}-{target_snapshot_id}", data,
                            completeness="COMPLETE", fact_keys=list(data.keys()),
                            note="archived_compare")
        ev.update(self._name_context(row.get("connection_id"),
                                     row.get("connection_name"), "snapshot"))
        ev["database"] = row.get("db_name")
        return ev

    # ── T07 表类型统计 ──────────────────────────────────
    def read_table_type_stat(self, stat_id: str) -> dict:
        row = _row(self.conn.execute(
            "SELECT id, connection_id, database_filter, instance_type, type_source, "
            "database_count, total_tables, shard_tables, broadcast_tables, "
            "single_tables, baseline_tables, subpartition_tables, failed_databases, "
            "skipped_databases, overlap_count, created_at, "
            "secondary_partition_main_tables, secondary_partition_check_state, "
            "secondary_partition_candidates, secondary_partition_checked, "
            "secondary_partition_unknown, secondary_partition_unchecked, "
            "secondary_partition_inventory_state, secondary_partition_outside_shard "
            "FROM table_type_stat WHERE id = ?", (stat_id,)).fetchone())
        if not row:
            return self._missing("TABLE_TYPE_STAT", stat_id)
        self._check_session_binding(row.get("connection_id"),
                                    row.get("database_filter"))
        # 原样携带二级分区字段；check_state 原值保留，completeness 另映射
        data = {
            "database_count": row.get("database_count"),
            "total_tables": row.get("total_tables"),
            "shard_tables": row.get("shard_tables"),
            "broadcast_tables": row.get("broadcast_tables"),
            "single_tables": row.get("single_tables"),
            "baseline_tables": row.get("baseline_tables"),
            "subpartition_tables": row.get("subpartition_tables"),
            "failed_databases": row.get("failed_databases"),
            "skipped_databases": row.get("skipped_databases"),
            "secondary_partition_main_tables": row.get("secondary_partition_main_tables"),
            "secondary_partition_check_state": row.get("secondary_partition_check_state"),
            "secondary_partition_candidates": row.get("secondary_partition_candidates"),
            "secondary_partition_checked": row.get("secondary_partition_checked"),
            "secondary_partition_unknown": row.get("secondary_partition_unknown"),
            "secondary_partition_unchecked": row.get("secondary_partition_unchecked"),
            "secondary_partition_inventory_state":
                row.get("secondary_partition_inventory_state"),
            "secondary_partition_outside_shard":
                row.get("secondary_partition_outside_shard"),
        }
        check_state = row.get("secondary_partition_check_state")
        if check_state in (None, "UNKNOWN", "LEGACY", "NOT_APPLICABLE"):
            completeness = "UNKNOWN"
        elif row.get("failed_databases"):
            completeness = "PARTIAL"
        else:
            completeness = "COMPLETE"
        ev = self._evidence("TABLE_TYPE_STAT", str(stat_id), data,
                            completeness=completeness, fact_keys=list(data.keys()))
        ev["connection_id"] = row.get("connection_id")
        ev["database"] = row.get("database_filter")
        ev["instance_type"] = row.get("instance_type") or "unknown"
        ev["observed_at"] = str(row.get("created_at") or "") or None
        return ev

    # ── T08 网关报告 ────────────────────────────────────
    def read_gateway_report(self, report_id: str) -> dict:
        row = _row(self.conn.execute(
            "SELECT id, connection_id, log_file_name, log_type, total_queries, "
            "slow_queries, max_time_ms, avg_time_ms, created_at, "
            "OCTET_LENGTH(analysis_meta_json) AS meta_bytes, analysis_meta_json, "
            "report_context_json "
            "FROM gateway_log_reports WHERE id = ?", (report_id,)).fetchone())
        if not row:
            return self._missing("GATEWAY_REPORT", report_id)
        self._check_session_binding(row.get("connection_id"), None)
        ctx = _safe_json(row.get("report_context_json")) or {}
        conn_name, name_source = _extract_context_name(ctx)
        meta_bytes = int(row.get("meta_bytes") or 0)
        if meta_bytes == 0:
            return self._missing("GATEWAY_REPORT", report_id,
                                 reason="旧记录无结构化分析摘要")
        if meta_bytes > MAX_GATEWAY_META_BYTES:
            ev = self._evidence("GATEWAY_REPORT", str(report_id), {
                "log_type": row.get("log_type"),
                "total_queries": row.get("total_queries"),
                "slow_queries": row.get("slow_queries"),
                "meta_too_large": True,
            }, completeness="PARTIAL", fact_keys=["total_queries", "slow_queries"])
            ev.update(self._name_context(row.get("connection_id"), conn_name,
                                         name_source))
            ev["observed_at"] = str(row.get("created_at") or "") or None
            return ev
        meta = _safe_json(row.get("analysis_meta_json")) or {}
        data = {
            "log_type": row.get("log_type"),
            "total_queries": row.get("total_queries"),
            "slow_queries": row.get("slow_queries"),
            "max_time_ms": row.get("max_time_ms"),
            "avg_time_ms": row.get("avg_time_ms"),
            "analysis_summary": _project_gateway_meta(meta, self.mapper,
                                                      self.identifiers_allowed),
        }
        ev = self._evidence("GATEWAY_REPORT", str(report_id), data,
                            completeness="COMPLETE", fact_keys=list(data.keys()))
        ev.update(self._name_context(row.get("connection_id"), conn_name, name_source))
        ev["observed_at"] = str(row.get("created_at") or "") or None
        return ev

    # ── 基础构造 ────────────────────────────────────────
    def _evidence(self, kind: str, source_id: str, data: dict,
                  completeness: str = "UNKNOWN", fact_keys: Optional[list] = None,
                  note: str = "") -> dict:
        ev = {
            "evidence_id": self._eid(),
            "source_kind": kind,
            "source_id": source_id,
            "source_revision": _sha(json.dumps(data, ensure_ascii=False,
                                               default=str))[:32],
            "connection_id": None,
            "connection_name": None,
            "name_source": "missing",
            "database": None,
            "instance_type": "unknown",
            "engine_version": None,
            "proxy_version": None,
            "observed_at": None,
            "captured_at": None,
            "availability": "AVAILABLE",
            "completeness": completeness,
            "selected_count": 1,
            "total_count": 1,
            "truncated": False,
            "reason_code": note,
            "fact_keys": fact_keys or list(data.keys()),
            "data": data,
        }
        return ev

    def _missing(self, kind: str, source_id: str, reason: str = "") -> dict:
        ev = self._evidence(kind, source_id, {}, completeness="UNKNOWN")
        ev["availability"] = "MISSING"
        ev["reason_code"] = reason or "SOURCE_NOT_FOUND"
        return ev


def _safe_json(text) -> Optional[Any]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_context_name(ctx: dict) -> tuple[Optional[str], str]:
    """从 report_context 提取连接名称快照与来源。"""
    try:
        conns = ctx.get("connections") or []
        if conns:
            c0 = conns[0]
            return c0.get("connection_name"), c0.get("name_source", "snapshot")
    except Exception:
        pass
    return None, "missing"


def _project_audit_result(item: dict, mapper: AliasMapper,
                          identifiers_allowed: bool) -> dict:
    """审核结果条目的投影：语句类型/规则命中保留，SQL 文本只给脱敏模板。"""
    out = {
        "sql_type": item.get("sql_type"),
        "passed": item.get("passed"),
        "line_number": item.get("line_number"),
        "violations": [],
    }
    for v in (item.get("violations") or [])[:10]:
        if isinstance(v, dict):
            out["violations"].append({
                "rule_id": v.get("rule_id"),
                "severity": v.get("severity"),
                "message": truncate_utf8(str(v.get("message") or ""), 300),
            })
    return out


def _project_gateway_meta(meta: dict, mapper: AliasMapper,
                          identifiers_allowed: bool) -> dict:
    """网关分析摘要投影：只保留统计与分类字段，不带主机/路径。"""
    keep = ("summary", "total_queries", "slow_queries", "error_queries",
            "top_fingerprints", "categories", "quality")
    out = {}
    for k in keep:
        if k in meta:
            v = meta[k]
            out[k] = truncate_utf8(json.dumps(v, ensure_ascii=False, default=str),
                                   4096) if not isinstance(v, (int, float, str)) else v
    return out
