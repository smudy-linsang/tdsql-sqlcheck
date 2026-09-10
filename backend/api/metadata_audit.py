# -*- coding: utf-8 -*-
"""在线元数据审核任务 API（v1.6.3.5 / DU-2 / FIX-02~04 / D07）。

设计出处：docs/DETAIL-v1.6.3.5-大库在线元数据审核稳定性修复.md §8.1。

资源根路径 `/api/v1/audit/metadata-jobs`。受理/查询走 MySQL 任务记录 + 唯一受理槽；
重任务（提取+审核）在独立 metadata-runner 子进程执行，Web 仅受理/查询/下载，不在
本进程做重型计算。所有错误有稳定 code 与中文 message，不返回 traceback/凭据。
"""

import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from backend.services.metadata_audit_repository import repository as repo
from backend.services.metadata_audit_repository import MetadataJobError
from backend.services.metadata_audit_repository import (
    STATE_SUCCEEDED, STATE_PUBLISHED, STATE_ACCEPTED, STATE_RUNNING,
    STATE_PUBLISHING, STATE_STOPPING, STATE_FAILED, STATE_CANCELLED,
    STATE_RECOVERY_REQUIRED, TERMINAL_STATES)
from backend.services import metadata_artifacts as art

logger = logging.getLogger("tdsql.metadata_audit")

router = APIRouter(prefix="/api/v1/audit", tags=["在线元数据审核任务"])

_JOB_TIMEOUT = 1800          # 与 worker 默认一致（受理预算）
_RUNNER_STALE_SECONDS = 10   # runner 心跳新鲜窗口


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


def _role(request: Request) -> str:
    return getattr(request.state, "role", "")


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or ""


def _no_store() -> dict:
    return {"Cache-Control": "no-store"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _norm_request(connection_id: str, db_name: str, scopes: list) -> str:
    """规范化请求（scopes 去重并固定顺序），用于 request_hash。"""
    return json.dumps({
        "connection_id": connection_id, "database": db_name,
        "scopes": sorted(set(s.upper() for s in scopes)),
    }, ensure_ascii=False, sort_keys=True)


def _request_hash(norm: str) -> str:
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _validate_scopes(scopes) -> list:
    from backend.services.metadata_audit_pipeline import VALID_SCOPES
    if scopes is None:
        return ["TABLE", "INDEX", "VIEW", "SHARDKEY"]
    if not isinstance(scopes, list):
        raise HTTPException(status_code=422, detail="scopes 必须为数组")
    out = [str(s).upper() for s in scopes]
    bad = [s for s in out if s not in VALID_SCOPES]
    if bad:
        raise HTTPException(status_code=422,
                            detail=f"无效审核范围: {bad}（仅允许 {VALID_SCOPES}）")
    if not out:
        raise HTTPException(status_code=422,
                            detail="未选择任何审核范围（TABLE/INDEX/VIEW/SHARDKEY）")
    return out


def _check_runner_ready():
    """受理前置：runner 必须存活（accepting=1 且心跳新鲜）。否则 503（fail-closed）。

    UAT-O-1635-R2-01：心跳缺失/解析失败/过期/明显未来时间一律拒绝受理，
    不得因解析异常被放行。用共享 is_fresh_heartbeat（兼容 DB datetime 与 ISO 格式）。
    """
    from backend.services import metadata_job_process as jp
    slot = repo.slot_state()
    if not slot or not slot.get("accepting"):
        raise MetadataJobError(
            "EXECUTOR_UNAVAILABLE",
            "元数据执行服务未就绪（runner 未启动），请稍后重试或联系管理员。", 503)
    if not jp.is_fresh_heartbeat(slot.get("runner_heartbeat_at"),
                                 window_seconds=_RUNNER_STALE_SECONDS):
        raise MetadataJobError(
            "EXECUTOR_UNAVAILABLE",
            "元数据执行服务心跳已过期或不可读（runner 可能已停止），请稍后重试。", 503)


def _freeze_context(connection_id: str, db_name: str, scopes: list) -> dict:
    """受理时冻结执行上下文（规则尺度 + 实例口径 + 报告来源 + 执行版本）。

    一次读取，不使用 30 秒缓存后二次读取另一规则集。失败抛 MetadataJobError，
    不无提示降回 default。
    """
    from backend.services.connection_registry import registry, ConnectionNotFoundError
    from backend.services.report_context import (
        capture_report_context, context_to_json_column, ORIGIN_BOUND)
    from backend.services.instance_type_service import instance_type_service
    from backend.services.ruleset_service import ruleset_service
    from backend.config import APP_VERSION

    try:
        conn_info = registry.get_saved(connection_id) or {}
    except ConnectionNotFoundError:
        raise MetadataJobError("CONNECTION_NOT_FOUND", "选定的数据库实例不存在。", 404)
    if not conn_info:
        raise MetadataJobError("CONNECTION_NOT_FOUND", "选定的数据库实例不存在。", 404)

    final_db = db_name or conn_info.get("database", "mysql")
    ictx = instance_type_service.resolve(connection_id)
    rule_set_id, overrides = ruleset_service.get_active_overrides()
    rctx = capture_report_context(connection_id, final_db, ORIGIN_BOUND)
    report_ctx_json = context_to_json_column(rctx) or ""
    conn_name = ""
    if rctx.connections:
        conn_name = rctx.connections[0].connection_name or ""

    ctx = {
        "engine_version": APP_VERSION, "engine_build": APP_VERSION,
        "rule_set_id": rule_set_id, "overrides": overrides,
        "instance_type": ictx.instance_type.value,
        "instance_type_source": getattr(ictx.source, "value", str(ictx.source)),
        "report_context": report_ctx_json, "connection_name": conn_name,
        "scope_flags": scopes,
    }
    ctx["context_hash"] = hashlib.sha256(
        json.dumps(ctx, ensure_ascii=False, sort_keys=True,
                   default=str).encode("utf-8")).hexdigest()
    return ctx, final_db, conn_info


def _connection_fingerprint(conn_info: dict) -> str:
    """非口令连接配置 + 密文凭据哈希（检测受理后配置变化；不保存明文/密文副本）。"""
    material = {
        "host": conn_info.get("host"), "port": conn_info.get("port"),
        "username": conn_info.get("username"), "database": conn_info.get("database"),
        "charset": conn_info.get("charset"), "name": conn_info.get("name"),
        "pwd_enc": conn_info.get("password_encrypted") or "",
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _job_summary(job: dict) -> dict:
    """状态接口的概要（不含全量 SQL/结果）。"""
    progress = {}
    if job.get("progress_json"):
        try:
            progress = json.loads(job["progress_json"])
        except (json.JSONDecodeError, TypeError):
            progress = {}
    # 耗时用共享 UTC 解析（UAT-O-1635-R2-02：database 兼容层转 ISO 带 T，
    # 旧 strptime 空格格式解析失败导致 elapsed 恒为 None）。已完成按 started→finished，
    # 运行中按 started→now；未开始（无 started_at）才是 None。
    from backend.services import metadata_job_process as _jp
    elapsed = None
    st_dt = _jp.parse_utc(job.get("started_at"))
    if st_dt is not None:
        if job["state"] in TERMINAL_STATES and job.get("finished_at"):
            end_dt = _jp.parse_utc(job.get("finished_at")) or _now_utc()
        else:
            end_dt = _now_utc()
        elapsed = max(0, int((end_dt - st_dt).total_seconds()))
    return {
        "job_id": job["id"], "state": job["state"], "phase": job["phase"],
        "connection_name": (json.loads(job["execution_context_json"] or "{}")
                            .get("connection_name") or ""),
        "database": job["db_name"],
        "elapsed_seconds": elapsed,
        "progress": {
            "enumerated_objects": progress.get("enumerated_objects"),
            "selected_objects": progress.get("selected_objects"),
            "extracted_objects": progress.get("extracted_objects"),
            "total_statements": progress.get("total_statements"),
            "audited_statements": progress.get("audited_statements"),
        },
        "report_id": job.get("report_id"), "snapshot_id": job.get("snapshot_id"),
        "cleanup_ok": job.get("cleanup_ok"),
        # v1.6.3.5 / SIT-N-01：失败原因/退出码顶层暴露（用户能看到真实原因，不只"失败"）
        "error_code": job.get("error_code"),
        "error_message": job.get("error_message"),
        "exit_code": job.get("exit_code"),
        "error": ({"code": job.get("error_code"), "message": job.get("error_message")}
                  if job.get("error_code") else None),
        "created_at": str(job.get("created_at") or ""),
        "finished_at": str(job.get("finished_at") or ""),
    }


def _own_or_404(request: Request, job: Optional[dict]) -> dict:
    """所有权校验：仅创建者或 admin 可访问；其他用户 404（不泄漏存在性）。"""
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if _role(request) != "admin" and job.get("created_by") != _operator(request):
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.post("/metadata-jobs", summary="创建在线元数据审核任务")
async def create_metadata_job(request: Request):
    rid = _request_id(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="请求体必须是合法 JSON")

    connection_id = str(body.get("connection_id") or "").strip()
    db_name = str(body.get("database") or body.get("database_name") or "").strip()
    if not connection_id:
        raise HTTPException(status_code=422, detail="请选择目标数据库实例")
    scopes = _validate_scopes(body.get("scopes"))

    # 幂等键：优先 Idempotency-Key 头，其次 body.client_submission_key
    idem = (request.headers.get("idempotency-key")
            or body.get("client_submission_key") or "").strip()
    if not idem:
        raise HTTPException(status_code=422,
                            detail="缺少幂等键（Idempotency-Key 头或 client_submission_key）")

    _check_runner_ready()
    # 磁盘受理前置检查（设计 §5.4/§8.3）：空间不足/核验不可用 → 507/503，不建任务
    from backend.services import metadata_job_process as jp
    ok_disk, disk_code, disk_msg = jp.check_disk_before_accept(str(art.artifact_root()))
    if not ok_disk:
        raise MetadataJobError(disk_code, disk_msg,
                               503 if disk_code == "STORAGE_CHECK_UNAVAILABLE" else 507)
    ctx, final_db, conn_info = _freeze_context(connection_id, db_name, scopes)
    norm = _norm_request(connection_id, final_db, scopes)
    req_hash = _request_hash(norm)
    fingerprint = _connection_fingerprint(conn_info)

    try:
        job, created_new = repo.create_job(
            created_by=_operator(request), request_id=rid,
            idempotency_key=idem, request_hash=req_hash,
            connection_id=connection_id, db_name=final_db, request_json=norm,
            execution_context_json=json.dumps(ctx, ensure_ascii=False, default=str),
            connection_fingerprint=fingerprint,
            report_deadline_seconds=_JOB_TIMEOUT)
    except MetadataJobError as e:
        return Response(
            content=json.dumps({"code": e.code, "message": e.message,
                                "request_id": rid}, ensure_ascii=False),
            status_code=e.http_status,
            media_type="application/json", headers=_no_store())

    payload = _job_summary(job)
    payload["request_id"] = rid
    status = 202 if created_new else 200
    return Response(content=json.dumps(payload, ensure_ascii=False),
                    status_code=status, media_type="application/json",
                    headers={**_no_store(), "Location": f"/api/v1/audit/metadata-jobs/{job['id']}"})


@router.get("/metadata-jobs/{job_id}", summary="查询任务状态")
async def get_metadata_job(job_id: str, request: Request):
    job = _own_or_404(request, repo.get_job(job_id))
    payload = _job_summary(job)
    payload["request_id"] = _request_id(request)
    return Response(content=json.dumps(payload, ensure_ascii=False),
                    media_type="application/json", headers=_no_store())


@router.get("/metadata-jobs", summary="任务列表")
async def list_metadata_jobs(request: Request, limit: int = 20, offset: int = 0,
                             state: str = ""):
    limit = max(1, min(int(limit), 100))
    creator = None if _role(request) == "admin" else _operator(request)
    rows, total = repo.list_jobs(creator, limit=limit, offset=offset,
                                 state=(state or "").strip())
    items = [_job_summary(r) for r in rows]
    return Response(content=json.dumps(
        {"items": items, "total": total, "request_id": _request_id(request)},
        ensure_ascii=False), media_type="application/json", headers=_no_store())


@router.get("/metadata-jobs/{job_id}/results", summary="分页读取审核结果")
async def get_metadata_results(job_id: str, request: Request,
                               offset: int = 0, limit: int = 50):
    job = _own_or_404(request, repo.get_job(job_id))
    if job["state"] != STATE_SUCCEEDED:
        raise HTTPException(status_code=409,
                            detail=f"任务当前状态 {job['state']}，仅 SUCCEEDED 可读取结果")
    limit = max(1, min(int(limit), 100))
    page = art.read_results_page(job_id, int(offset), limit)
    if not page.get("available"):
        raise HTTPException(status_code=410,
                            detail="审核已完成，产物当前不可用（ARTIFACT_MISSING）")
    return Response(content=json.dumps({
        "items": page["items"], "total": page["total"],
        "next_offset": page["next_offset"], "request_id": _request_id(request),
    }, ensure_ascii=False), media_type="application/json", headers=_no_store())


@router.get("/metadata-jobs/{job_id}/sql", summary="下载完整提取 SQL 文件")
async def download_metadata_sql(job_id: str, request: Request):
    job = _own_or_404(request, repo.get_job(job_id))
    if job["state"] != STATE_SUCCEEDED or job.get("artifact_state") != "READY":
        raise HTTPException(status_code=410, detail="审核产物当前不可用")
    try:
        data = art.read_full_sql(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=410, detail="审核产物当前不可用（ARTIFACT_MISSING）")
    return Response(content=data, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename=metadata_{job_id}.sql"})


@router.get("/metadata-jobs/{job_id}/sql-preview", summary="SQL 预览（前 64KiB）")
async def metadata_sql_preview(job_id: str, request: Request):
    job = _own_or_404(request, repo.get_job(job_id))
    if job["state"] != STATE_SUCCEEDED:
        raise HTTPException(status_code=409, detail="任务未完成")
    prev = art.read_sql_preview(job_id)
    if not prev.get("available"):
        raise HTTPException(status_code=410, detail="审核产物当前不可用（ARTIFACT_MISSING）")
    return Response(content=json.dumps({
        "preview": prev["preview"], "truncated": prev["truncated"],
        "total_bytes": prev["total_bytes"], "request_id": _request_id(request),
    }, ensure_ascii=False), media_type="application/json", headers=_no_store())


@router.get("/metadata-jobs/{job_id}/results/{statement_index}/sql",
            summary="单条完整 SQL")
async def metadata_result_sql(job_id: str, statement_index: int, request: Request):
    job = _own_or_404(request, repo.get_job(job_id))
    if job["state"] != STATE_SUCCEEDED:
        raise HTTPException(status_code=409, detail="任务未完成")
    rec = art.read_result_detail(job_id, int(statement_index))
    if rec is None:
        raise HTTPException(status_code=404, detail="语句不存在")
    return Response(content=(rec.get("sql") or "").encode("utf-8"),
                    media_type="text/plain; charset=utf-8")


@router.get("/metadata-jobs/{job_id}/results/{statement_index}/detail",
            summary="单条完整结果（含全文 SQL 与全部违规）")
async def metadata_result_detail(job_id: str, statement_index: int, request: Request):
    job = _own_or_404(request, repo.get_job(job_id))
    if job["state"] != STATE_SUCCEEDED:
        raise HTTPException(status_code=409, detail="任务未完成")
    rec = art.read_result_detail(job_id, int(statement_index))
    if rec is None:
        raise HTTPException(status_code=404, detail="语句不存在")
    return Response(content=json.dumps(rec, ensure_ascii=False),
                    media_type="application/json", headers=_no_store())


@router.get("/metadata-jobs/{job_id}/html", summary="下载完整 HTML 报告")
async def download_metadata_html(job_id: str, request: Request):
    job = _own_or_404(request, repo.get_job(job_id))
    if job["state"] != STATE_SUCCEEDED or job.get("artifact_state") != "READY":
        raise HTTPException(status_code=410, detail="审核产物当前不可用")
    try:
        data = art.read_report_html(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=410, detail="审核产物当前不可用（ARTIFACT_MISSING）")
    return Response(content=data, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename=metadata_report_{job_id}.html"})


@router.post("/metadata-jobs/{job_id}/cancel", summary="取消任务")
async def cancel_metadata_job(job_id: str, request: Request):
    job = _own_or_404(request, repo.get_job(job_id))
    # 已提交成果不可撤销
    if job["state"] in (STATE_PUBLISHED, STATE_SUCCEEDED):
        return Response(content=json.dumps({
            "code": "RESULT_ALREADY_COMMITTED",
            "message": "结果已提交，不能撤销；报告仍可下载。",
            "request_id": _request_id(request)}, ensure_ascii=False),
            status_code=409, media_type="application/json", headers=_no_store())
    if job["state"] == STATE_CANCELLED:
        return Response(content=json.dumps(_job_summary(job), ensure_ascii=False),
                        media_type="application/json", headers=_no_store())
    if job["state"] in (STATE_FAILED,):
        return Response(content=json.dumps(_job_summary(job), ensure_ascii=False),
                        media_type="application/json", headers=_no_store())
    updated = repo.request_cancel(job_id)
    return Response(content=json.dumps(_job_summary(updated), ensure_ascii=False),
                    status_code=202, media_type="application/json",
                    headers=_no_store())
