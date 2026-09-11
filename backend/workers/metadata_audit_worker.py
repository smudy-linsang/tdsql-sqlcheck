# -*- coding: utf-8 -*-
"""元数据审核任务子进程入口（v1.6.3.5 / DU-2 / FIX-01+02 / D05）。

由 metadata_runner 以 `python -m backend.workers.metadata_audit_worker --job-id X
--attempt-token Y` 派生。在本进程内完成：连接目标库 → 只读提取 DDL → 流式审核
（DU-1 iter_audit_file）→ 制备产物（schema.sql/results.ndjson/results.json/
report.html/manifest.json）→ 原子发布 audit_history。

不 import backend.main（不启动 Web 应用/调度器/网关锁）。所有 child 状态更新带
attempt_token fencing；口令/JWT/完整 SQL 不写 stdout/日志。退出码：0=成功发布，
1=业务失败（已 FAILED），2=fencing/参数错误。
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("tdsql.metadata_worker")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _result_to_dict(r) -> dict:
    """AuditResult → audit_history 兼容的 JSON 记录。"""
    return {
        "sql": r.sql, "sql_type": r.sql_type, "passed": r.passed,
        "file_path": r.file_path, "line_number": r.line_number,
        "violations": [{
            "rule_id": v.rule_id,
            "severity": v.severity.value if hasattr(v.severity, "value") else str(v.severity),
            "message": v.message,
            "suggestion": getattr(v, "suggestion", "") or "",
            "line_number": getattr(v, "line_number", None),
        } for v in r.violations],
    }


def _compute_summary(records: list) -> dict:
    total = len(records)
    passed = sum(1 for r in records if r["passed"])
    failed = total - passed
    error_count = sum(1 for r in records for v in r["violations"]
                      if str(v["severity"]).upper() in ("ERROR", "FATAL", "CRITICAL"))
    warning_count = sum(1 for r in records for v in r["violations"]
                        if str(v["severity"]).upper() in ("WARNING", "WARN"))
    pass_rate = (passed / total * 100.0) if total else 0.0
    return {"total_sql": total, "passed": passed, "failed": failed,
            "error_count": error_count, "warning_count": warning_count,
            "pass_rate": pass_rate}


def _build_report_html(job: dict, ctx: dict, summary: dict, stats: dict,
                       records: list) -> str:
    """child 内制备自包含 HTML 报告（含冻结实例连接名称 + job_id + 扫描口径）。

    抽取纯渲染逻辑；冻结名经 HTML 转义，不反查现名。
    """
    from html import escape as _e
    conn_name = ""
    try:
        from backend.services.report_context import ReportContext
        rc = ReportContext.from_json(ctx.get("report_context") or "")
        if rc and rc.connections:
            conn_name = rc.connections[0].connection_name or ""
    except Exception:
        conn_name = ""
    now = _utcnow()
    rows = []
    for i, rec in enumerate(records, 1):
        sev_html = ""
        for v in rec["violations"]:
            sev = str(v["severity"]).upper()
            cls = "err" if sev in ("ERROR", "FATAL", "CRITICAL") else ("warn" if sev in ("WARNING", "WARN") else "info")
            sev_html += (f'<div class="v {cls}"><b>[{_e(v["rule_id"])}] [{_e(sev)}]</b> '
                         f'{_e(v["message"])}</div>')
        status = '<span style="color:#16a34a">通过</span>' if rec["passed"] else \
                 f'<span style="color:#dc2626">{len(rec["violations"])}项违规</span>'
        rows.append(
            f'<div class="it"><h3>#{i} {_e(rec["sql_type"])} {status}</h3>'
            f'<pre>{_e(rec["sql"][:4096])}</pre>{sev_html}</div>')
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>在线元数据审核报告 - {_e(job['db_name'])}</title>
<style>
body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#f4f6f9;color:#333;margin:0;padding:20px}}
.c{{max-width:1000px;margin:0 auto;background:#fff;padding:30px;border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,.05)}}
.h{{border-bottom:2px solid #2563eb;padding-bottom:15px;margin-bottom:20px}}
.h h1{{margin:0;font-size:24px;color:#0f1e34}}
.meta{{font-size:13px;color:#666;margin-top:8px}}
.kpi{{display:flex;gap:15px;margin-bottom:20px}}
.k{{flex:1;background:#f8fafc;padding:15px;border-radius:6px;text-align:center;border:1px solid #e2e8f0}}
.k b{{font-size:22px;display:block}}
.it{{margin-bottom:18px;border-bottom:1px dashed #e2e8f0;padding-bottom:14px}}
pre{{background:#0f1e34;color:#e2e8f0;padding:12px;border-radius:6px;font-family:monospace;font-size:13px;overflow-x:auto;white-space:pre-wrap}}
.v{{padding:8px 12px;border-radius:6px;margin:6px 0;font-size:13px;border-left:4px solid #9ca3af;background:#f3f4f6}}
.v.err{{border-left-color:#ef4444;background:#fef2f2}}.v.warn{{border-left-color:#f59e0b;background:#fffbeb}}
</style></head><body><div class="c">
<div class="h"><h1>在线元数据审核报告</h1>
<div class="meta">实例连接名称：<strong>{_e(conn_name)}</strong> ｜ 库：{_e(job['db_name'])} ｜
任务：{_e(job['id'])} ｜ 生成：{now}</div>
<div class="meta">扫描口径：枚举 {stats.get('enumerated_objects',0)} 个对象 / 选中 {stats.get('selected_objects',0)} / 提取 {stats.get('extracted_objects',0)}；拆句 {summary['total_sql']} 条</div></div>
<div class="kpi">
<div class="k"><b>{summary['total_sql']}</b>对象数</div>
<div class="k"><b style="color:#16a34a">{summary['passed']}</b>通过</div>
<div class="k"><b style="color:#dc2626">{summary['failed']}</b>未通过</div>
<div class="k"><b style="color:#2563eb">{summary['pass_rate']:.1f}%</b>通过率</div>
</div>
<h2>审核明细</h2>
{''.join(rows)}
</div></body></html>"""


def run(job_id: str, attempt_token: str) -> int:
    """执行一个元数据审核任务。返回退出码（0 成功 / 1 业务失败 / 2 fencing）。"""
    from backend.services.metadata_audit_repository import repository as repo
    from backend.services import metadata_audit_repository as R
    from backend.services import metadata_artifacts as art
    from backend.services.metadata_audit_pipeline import (
        extract_metadata, audit_streaming, MetadataExtractError)

    job = repo.get_job(job_id)
    if not job or job.get("attempt_token") != attempt_token:
        logger.error("fencing 校验失败：job_id=%s", job_id)
        return 2
    if job["state"] != R.STATE_RUNNING:
        logger.error("任务状态非 RUNNING：%s", job.get("state"))
        return 2

    ctx = json.loads(job["execution_context_json"])
    req = json.loads(job["request_json"])
    connection_id = job["connection_id"]
    db_name = job["db_name"]
    scopes = req.get("scopes") or ["TABLE", "INDEX", "VIEW", "SHARDKEY"]
    rule_set_id = ctx.get("rule_set_id") or ""
    overrides = ctx.get("overrides")
    instance_type = ctx.get("instance_type")
    instance_type_source = ctx.get("instance_type_source") or ""
    report_ctx_json = ctx.get("report_context") or ""
    created_by = job["created_by"]
    filename = f"extracted_{db_name}_{job_id[:8]}.sql"

    try:
        # ── 连接目标库（子进程独立建池，解密凭据不出本进程）──
        from backend.services.connection_registry import registry
        pool = registry.get(connection_id)

        # ── 提取（EXTRACTING）──
        repo.cas_state(job_id, attempt_token, (R.STATE_RUNNING,), R.STATE_RUNNING,
                       phase=R.PHASE_EXTRACTING)
        lines, stats = extract_metadata(pool, db_name, scopes,
                                        instance_label=ctx.get("connection_name", ""))
        schema_sql = "\n".join(lines)
        d = art.job_dir(job_id)
        art.atomic_write_text(d / "schema.sql", schema_sql)
        repo.update_progress(job_id, attempt_token, R.PHASE_EXTRACTING,
                             json.dumps(stats, ensure_ascii=False))

        # ── 流式审核（AUDITING），边审边写 results.ndjson ──
        repo.cas_state(job_id, attempt_token, (R.STATE_RUNNING,), R.STATE_RUNNING,
                       phase=R.PHASE_AUDITING)
        records = []
        nd_path = d / "results.ndjson"
        nd_part = nd_path.with_name("results.ndjson.part")
        with open(nd_part, "w", encoding="utf-8") as nd:
            for idx, r in enumerate(audit_streaming(
                    schema_sql, file_path=filename, rule_overrides=overrides,
                    instance_type=instance_type)):
                rec = _result_to_dict(r)
                nd.write(json.dumps(rec, ensure_ascii=False) + "\n")
                records.append(rec)
                if idx % 50 == 0:
                    repo.update_progress(job_id, attempt_token, R.PHASE_AUDITING,
                                         json.dumps({"audited_statements": idx + 1,
                                                     **stats}, ensure_ascii=False))
        nd_part.flush() if hasattr(nd_part, "flush") else None
        os.replace(nd_part, nd_path)

        # ── 收尾强制写最终计数（UAT-O-1635-R2-02）──
        # total_statements 用实际拆句数（len(records)），audited_statements 同步；
        # 不能用表数冒充，不再依赖 idx%50 的稀疏采样（否则终态停在 51 而非 63）。
        _final_count = len(records)
        # skipped_list 限长（progress_json 限 128KiB）：只存计数 + 前 50 条跳过明细
        _prog_stats = dict(stats)
        _skipped_list = _prog_stats.get("skipped_list") or []
        _prog_stats["skipped_objects"] = len(_skipped_list)
        _prog_stats["skipped_list"] = _skipped_list[:50]
        repo.update_progress(job_id, attempt_token, R.PHASE_SERIALIZING,
                             json.dumps({**_prog_stats,
                                         "total_statements": _final_count,
                                         "audited_statements": _final_count},
                                        ensure_ascii=False))

        # ── 序列化（SERIALIZING）──
        repo.cas_state(job_id, attempt_token, (R.STATE_RUNNING,), R.STATE_RUNNING,
                       phase=R.PHASE_SERIALIZING)
        summary = _compute_summary(records)
        results_json = json.dumps(records, ensure_ascii=False)
        report_html = _build_report_html(job, ctx, summary, stats, records)
        art.atomic_write_text(d / "results.json", results_json)
        art.atomic_write_text(d / "report.html", report_html)

        # ── 发布（PUBLISHING → 严格事务落库 audit_history）──
        repo.cas_state(job_id, attempt_token, (R.STATE_RUNNING,), R.STATE_PUBLISHING,
                       phase=R.PHASE_PERSISTING)
        audit_cols = (
            "extracted_schema", filename,
            summary["total_sql"], summary["passed"], summary["failed"],
            summary["error_count"], summary["warning_count"], summary["pass_rate"],
            results_json, created_by, "", None, "", _utcnow(),
            connection_id, db_name, rule_set_id or None,
            instance_type, instance_type_source, 0,
            report_ctx_json or None,
        )
        report_id = repo.publish(job_id, attempt_token,
                                 audit_columns_values=audit_cols,
                                 results_json=results_json)

        # ── 旁路对比快照（SNAPSHOTTING）：失败仅告警，不影响已发布成果 ──
        repo.cas_state(job_id, attempt_token, (R.STATE_PUBLISHED,), R.STATE_PUBLISHED,
                       phase=R.PHASE_SNAPSHOTTING)
        snapshot_id = None
        try:
            from backend.services.snapshot_extractors.schema_audit import extract_from_json
            from backend.services import scan_snapshot_service as _snap
            _items, _obj_total = extract_from_json(results_json, db_name, node="")
            snapshot_id = _snap.safe_create_snapshot("schema_audit", {
                "biz_ref_id": str(report_id), "connection_id": connection_id,
                "connection_name": ctx.get("connection_name", ""), "db_name": db_name,
                "node": "", "scan_label": filename,
                "scan_started_at": job.get("started_at") or _utcnow(),
                "scan_finished_at": _utcnow(), "created_by": created_by,
                "rule_set_id": rule_set_id or "", "instance_type": instance_type or "",
                "report_context_json": report_ctx_json or None,
            }, _items, _obj_total)
            if snapshot_id:
                repo.cas_state(job_id, attempt_token, (R.STATE_PUBLISHED,),
                               R.STATE_PUBLISHED, phase=R.PHASE_SNAPSHOTTING,
                               extra={"snapshot_id": int(snapshot_id)})
        except Exception as e:
            logger.warning("对比快照生成失败（旁路，不影响已发布成果）job=%s: %s",
                           job_id, e)

        # ── manifest（核心制备完成标志）──
        files_meta = {}
        for fn in art.ARTIFACT_FILES[:-1]:
            fp = d / fn
            if fp.exists():
                files_meta[fn] = {"bytes": fp.stat().st_size,
                                  "sha256": art.sha256_file(fp)}
        art.write_manifest(
            job_id, context_hash=ctx.get("context_hash", ""),
            files_meta=files_meta,
            counts={**stats, "total_statements": summary["total_sql"],
                    "violations": summary["error_count"] + summary["warning_count"]})
        # publish 已把 job 置 PUBLISHED；runner 负责回收确认后置 SUCCEEDED
        return 0
    except MetadataExtractError as e:
        # v1.6.3.5 / UAT-O-1635-R2-03：提取边界的目标库错误也语义化（保留定位 + 原始码）
        from backend.services.metadata_job_process import humanize_db_error
        repo.fail(job_id, attempt_token, error_code=e.code,
                  error_message=humanize_db_error(e.message))
        return 1
    except Exception as e:
        logger.error("元数据审核任务执行失败 job=%s: %s", job_id, e, exc_info=True)
        try:
            # v1.6.3.5 / UAT-M3：PyMySQL 原始错误码语义化为可读中文提示
            from backend.services.metadata_job_process import humanize_db_error
            repo.fail(job_id, attempt_token, error_code="WORKER_ERROR",
                      error_message=humanize_db_error(f"审核执行失败: {str(e)[:500]}"))
        except Exception:
            pass
        return 1


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--job-id", required=True)
    p.add_argument("--attempt-token", required=True)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    return run(args.job_id, args.attempt_token)


if __name__ == "__main__":
    sys.exit(main())
