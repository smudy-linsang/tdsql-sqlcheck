"""慢SQL治理 / 原始慢日志 API（独立于现有扫描任务）。"""
from __future__ import annotations

import csv
import html
import io
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend import config
from backend.services.raw_slowlog_service import (
    RawSlowLogBusyError,
    RawSlowLogNotFoundError,
    RawSlowLogValidationError,
    raw_slowlog_service,
)


def _require_raw_slowlog_enabled() -> None:
    """在功能关闭时拒绝全部入口，避免历史配置被意外使用。"""
    if not config.raw_slowlog_enabled():
        raise HTTPException(status_code=404, detail="原始慢日志功能当前未启用")


router = APIRouter(
    prefix="/api/v1/raw-slowlogs",
    tags=["慢SQL治理-原始慢日志"],
    include_in_schema=False,
    dependencies=[Depends(_require_raw_slowlog_enabled)],
)


class RawSlowLogNodeRequest(BaseModel):
    node_key: str
    display_name: str
    ssh_host: str
    ssh_port: int = Field(22, ge=1, le=65535)
    host_key_alias: str
    remote_source_key: str
    declared_path_template: str
    parser_profile: str


class RawSlowLogSourceRequest(BaseModel):
    source_key: Optional[str] = None
    connection_id: str
    display_name: str
    transport: str = "ssh_exporter_v1"
    timezone: str = "Asia/Shanghai"
    poll_interval_seconds: int = 60
    max_batch_bytes: int = 8 * 1024 * 1024
    max_events_per_batch: int = 2000
    max_run_seconds: int = 25
    lag_alert_seconds: int = 600
    initial_position: str = "tail"
    initial_lookback_seconds: int = 300
    min_query_time_ms: int = 1000
    credential_ref: str = ""
    known_hosts_ref: str = ""
    nodes: list[RawSlowLogNodeRequest]


class EnabledRequest(BaseModel):
    enabled: bool


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


def _role(request: Request) -> str:
    return getattr(request.state, "role", "")


def _require_role(request: Request, allowed: set[str], action: str) -> None:
    if _role(request) not in allowed:
        raise HTTPException(status_code=403, detail=f"仅 {', '.join(sorted(allowed))} 可{action}")


def _translate(exc: Exception) -> None:
    if isinstance(exc, RawSlowLogBusyError):
        raise HTTPException(status_code=409, detail="E4091 SOURCE_BUSY：该采集源已有未过期运行租约")
    if isinstance(exc, RawSlowLogNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, RawSlowLogValidationError):
        raise HTTPException(status_code=400, detail=str(exc))
    raise exc


@router.get("/sources", summary="原始慢日志采集源列表")
def list_sources(request: Request):
    return {"items": raw_slowlog_service.list_sources(_role(request))}


@router.post("/sources", status_code=201, summary="创建禁用的原始慢日志采集源")
def create_source(body: RawSlowLogSourceRequest, request: Request):
    _require_role(request, {"admin"}, "创建采集源")
    data = body.model_dump()
    if not data.get("source_key"):
        raise HTTPException(status_code=422, detail="source_key 为必填项")
    try:
        return raw_slowlog_service.create_source(data, _operator(request))
    except Exception as exc:
        _translate(exc)


@router.get("/sources/{source_id}", summary="原始慢日志采集源详情")
def get_source(source_id: int, request: Request):
    try:
        return raw_slowlog_service.get_source(source_id, _role(request))
    except Exception as exc:
        _translate(exc)


@router.put("/sources/{source_id}", summary="更新采集源并自动停用")
def update_source(source_id: int, body: RawSlowLogSourceRequest, request: Request):
    _require_role(request, {"admin"}, "修改采集源")
    try:
        return raw_slowlog_service.update_source(source_id, body.model_dump(exclude_none=True), _operator(request))
    except Exception as exc:
        _translate(exc)


@router.post("/sources/{source_id}/probe", summary="执行只读连通性和日志格式探测")
def probe_source(source_id: int, request: Request):
    _require_role(request, {"admin", "dba"}, "执行 Probe")
    try:
        return raw_slowlog_service.probe_source(source_id, _operator(request))
    except Exception as exc:
        _translate(exc)


@router.put("/sources/{source_id}/enabled", summary="启用或停用采集源")
def set_enabled(source_id: int, body: EnabledRequest, request: Request):
    _require_role(request, {"admin"}, "启停采集源")
    try:
        return raw_slowlog_service.set_enabled(source_id, body.enabled, _operator(request))
    except Exception as exc:
        _translate(exc)


@router.post("/sources/{source_id}/collect", status_code=202, summary="异步触发一次已保存配置的采集")
def collect_source(source_id: int, request: Request, background_tasks: BackgroundTasks):
    _require_role(request, {"admin", "dba"}, "手动采集")
    try:
        run_id = raw_slowlog_service.queue_manual_collect(source_id, _operator(request))
        background_tasks.add_task(raw_slowlog_service.collect_source, source_id, _operator(request), "manual", run_id)
        return {"run_id": run_id, "status": "accepted", "message": "已受理；请查询运行状态。"}
    except Exception as exc:
        _translate(exc)


@router.get("/runs", summary="采集运行列表")
def list_runs(request: Request, source_id: Optional[int] = None, limit: int = Query(100, ge=1, le=500)):
    return {"items": raw_slowlog_service.list_runs(source_id, limit, _role(request))}


@router.get("/runs/{run_id}", summary="采集运行详情")
def get_run(run_id: int, request: Request):
    try:
        return raw_slowlog_service.get_run(run_id, _role(request))
    except Exception as exc:
        _translate(exc)


def _event_filters(source_id, source_node_id, db_name, start_time, end_time, fingerprint, min_query_time_us, limit, offset):
    return {"source_id": source_id, "source_node_id": source_node_id, "db_name": db_name,
            "start_time": start_time, "end_time": end_time, "fingerprint": fingerprint,
            "min_query_time_us": min_query_time_us, "limit": limit, "offset": offset}


@router.get("/events/export", summary="导出脱敏原始慢日志事件 CSV")
def export_events(
    request: Request,
    source_id: Optional[int] = None, source_node_id: Optional[int] = None, db_name: Optional[str] = None,
    start_time: Optional[str] = None, end_time: Optional[str] = None, fingerprint: Optional[str] = None,
    min_query_time_us: Optional[int] = Query(None, ge=0),
    format: str = Query("csv", pattern="^(csv|html)$"),
):
    result = raw_slowlog_service.list_events(_event_filters(
        source_id, source_node_id, db_name, start_time, end_time, fingerprint, min_query_time_us, 10000, 0))
    if format == "html":
        cells = []
        for event in result["items"]:
            cells.append("<tr>" + "".join(f"<td>{html.escape(str(value or ''))}</td>" for value in (
                event["event_time"], event["db_name"], event["source_node_id"], event["query_time_us"],
                event["lock_time_us"], event["sql_fingerprint"], event["sql_template"], event["collected_at"],
            )) + "</tr>")
        page = """<!doctype html><meta charset='utf-8'><title>TDSQL 原始慢日志事件报告</title>
        <style>body{font:14px sans-serif;margin:28px;color:#1f2937}table{border-collapse:collapse;width:100%}th,td{border:1px solid #cbd5e1;padding:7px;text-align:left;vertical-align:top}th{background:#e2e8f0}.notice{padding:10px;background:#fff7ed;border-left:4px solid #f97316}</style>
        <h1>TDSQL 原始慢日志事件报告</h1>
        <p>时间范围：Proxy 慢日志记录时间（不是 SQL 开始时间，也不是采集时间）。</p>
        <p class='notice'>本报告仅列出已成功采集的脱敏事件。零行不代表目标时间范围内不存在慢 SQL；请同时核验采集运行状态、节点覆盖和错误摘要。</p>
        <table><thead><tr><th>日志记录时间</th><th>库</th><th>节点</th><th>耗时(微秒)</th><th>锁等待(微秒)</th><th>指纹</th><th>脱敏SQL模板</th><th>采集时间</th></tr></thead><tbody>""" + "".join(cells) + "</tbody></table>"
        from backend.services.database import log_operation
        log_operation(_operator(request), "raw_slowlog_events_export", "raw_slowlog", "", f"format=html rows={len(result['items'])}")
        return HTMLResponse(page)
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["日志记录时间", "库", "节点", "耗时(微秒)", "锁等待(微秒)", "指纹", "脱敏SQL模板", "采集时间"])
    for event in result["items"]:
        writer.writerow([event["event_time"], event["db_name"], event["source_node_id"], event["query_time_us"],
                         event["lock_time_us"], event["sql_fingerprint"], event["sql_template"], event["collected_at"]])
    from backend.services.database import log_operation
    log_operation(_operator(request), "raw_slowlog_events_export", "raw_slowlog", "", f"rows={len(result['items'])}")
    return StreamingResponse(iter(["\ufeff" + stream.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=raw-slowlog-events.csv"})


@router.get("/events", summary="按 Proxy 慢日志记录时间查询原始事件")
def list_events(
    source_id: Optional[int] = None, source_node_id: Optional[int] = None, db_name: Optional[str] = None,
    start_time: Optional[str] = None, end_time: Optional[str] = None, fingerprint: Optional[str] = None,
    min_query_time_us: Optional[int] = Query(None, ge=0), limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return raw_slowlog_service.list_events(_event_filters(
        source_id, source_node_id, db_name, start_time, end_time, fingerprint, min_query_time_us, limit, offset))


@router.get("/events/{event_id}", summary="原始慢日志脱敏事件详情")
def get_event(event_id: int):
    try:
        return raw_slowlog_service.get_event(event_id)
    except Exception as exc:
        _translate(exc)
