"""
TDSQL SQL审核工具 - 扫描结果纵向对比 API

设计依据：docs/API-v1.3-扫描结果对比.md

统一前缀 /api/v1/scan-compare。所有接口需 scan-compare 菜单权限，
并按 module 二次校验调用者是否具备该模块自身权限（防越权）。
"""
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response

from backend.services import scan_compare_service as compare_service
from backend.services import scan_snapshot_service as snapshot_service
from backend.services.scan_compare_service import CompareError
from backend.services.database import _get_connection, ensure_db, log_operation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scan-compare", tags=["扫描结果对比"])

# module → 该模块自身的菜单权限键
_MODULE_MENU = {
    "schema_audit": "schema-extractor-audit",
    "slow_scan": "slow-tasks",
    "bigtable": "bigtable",
}


# ── 通用工具 ──

def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


def _role(request: Request) -> str:
    return getattr(request.state, "role", "")


def _err(code: str, message: str, status: int = 400):
    return HTTPException(status_code=status, detail={"detail": message, "code": code})


def _check_module(module: str) -> str:
    """校验 module。

    缺失与非法都返回 400 E4006（而非 FastAPI 默认的 422）：
    422 不带 `code` 字段，会破坏 API 文档 §1.3 "失败一律返回 {detail, code}" 的约定；
    且平台既有先例（daily_inspect.compare 对 connection_id）也是 400 + 明确提示。
    """
    if not module:
        raise _err("E4006", "必须指定 module（schema_audit / slow_scan / bigtable）")
    if module not in _MODULE_MENU:
        raise _err("E4006", f"不支持的模块类型: {module}")
    return module


def _check_module_perm(request: Request, module: str):
    """二次越权校验：仅有 scan-compare 权限不足以查看任意模块数据"""
    _check_module(module)
    role = _role(request)
    if role == "admin":
        return
    try:
        from backend.services.auth_service import get_visible_menus
        menus = get_visible_menus(role) or []
    except Exception as e:
        logger.warning(f"读取角色可见菜单失败，按拒绝处理: {e}")
        raise _err("E4031", "无该模块数据的访问权限", status=403)
    if _MODULE_MENU[module] not in menus:
        raise _err("E4031", "无该模块数据的访问权限", status=403)


def _require_admin(request: Request):
    """仅 admin 可调用，否则 403 E4031"""
    if _role(request) != "admin":
        raise _err("E4031", "仅系统管理员可删除对比报告留档", status=403)


def _audit(request: Request, operation_type: str, target_id: str = "", detail: str = ""):
    """写审计日志；失败仅告警，不影响主流程"""
    try:
        client = getattr(request, "client", None)
        log_operation(
            operator=_operator(request),
            operation_type=operation_type,
            target_type="scan_compare",
            target_id=str(target_id)[:64],
            detail=str(detail)[:500],
            ip_address=(client.host if client else ""),
            user_agent=request.headers.get("user-agent", "")[:200],
        )
    except Exception as e:
        logger.warning(f"审计日志写入失败: {e}")


def _raise_compare_error(e: CompareError):
    raise HTTPException(status_code=e.status,
                        detail={"detail": e.message, "code": e.code})


# ── 1. 快照列表 ──

@router.get("/snapshots", summary="扫描快照列表（按实例/库名/时间筛选）")
def list_snapshots(request: Request, module: str = "", connection_id: str = "",
                   db_name: str = "", date_from: str = "", date_to: str = "",
                   limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0)):
    # module 设为可选参数 + 手动校验，缺失时返回 400 E4006 而非 FastAPI 默认 422
    module = _check_module(module)
    _check_module_perm(request, module)
    data = snapshot_service.list_snapshots(
        module=module, connection_id=connection_id, db_name=db_name,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset)
    _audit(request, "view_snapshots", "",
           f"module={module};conn={connection_id};db={db_name};range={date_from}~{date_to}")
    return data


# ── 2. 快照详情 ──

@router.get("/snapshots/{snapshot_id}", summary="快照详情")
def get_snapshot(snapshot_id: int, request: Request, with_issues: bool = False):
    snap = snapshot_service.get_snapshot(snapshot_id, with_issues=True)
    if not snap:
        raise _err("E4004", "快照不存在或已被数据保留策略清理", status=404)
    _check_module_perm(request, snap.get("module") or "")

    resp = {
        "id": snap.get("id"),
        "module": snap.get("module"),
        "biz_ref_id": snap.get("biz_ref_id"),
        "connection_id": snap.get("connection_id"),
        "connection_name": snap.get("connection_name"),
        "db_name": snap.get("db_name"),
        "scan_label": snap.get("scan_label"),
        "scan_started_at": snap.get("scan_started_at"),
        "scan_finished_at": snap.get("scan_finished_at"),
        "stats": snap.get("stats") or {},
        "fingerprint_algo": snap.get("fingerprint_algo"),
        "truncated": snap.get("truncated"),
        "truncated_count": snap.get("truncated_count", 0),
        "source_kind": snap.get("source_kind"),
        "created_by": snap.get("created_by"),
        "created_at": snap.get("created_at"),
    }
    if with_issues:
        resp["issues"] = snap.get("issues") or []
    _audit(request, "view_snapshot_detail", str(snapshot_id),
           f"module={snap.get('module')}")
    return resp


# ── 3. 两次扫描结果比对（核心）──

@router.post("/compare", summary="两次扫描结果比对")
def compare_snapshots(request: Request, payload: dict):
    module = _check_module((payload or {}).get("module") or "")
    _check_module_perm(request, module)
    snapshot_ids = (payload or {}).get("snapshot_ids") or []
    include_details = (payload or {}).get("include_details", True)
    detail_limit = (payload or {}).get("detail_limit") or compare_service.DEFAULT_DETAIL_LIMIT

    try:
        result = compare_service.run_compare(
            snapshot_ids, module=module,
            include_details=bool(include_details), detail_limit=detail_limit)
    except CompareError as e:
        _raise_compare_error(e)
    except (TypeError, ValueError) as e:
        raise _err("E4001", f"参数非法: {e}")

    s = result.get("summary") or {}
    _audit(request, "compare_snapshot",
           f"{result['base']['id']}vs{result['target']['id']}",
           f"module={module};conn={result.get('connection_id')};"
           f"fixed={s.get('fixed_count')};new={s.get('new_count')}")
    return result


# ── 4. 对比报告 HTML ──

@router.get("/compare/html", summary="导出对比报告HTML")
def compare_html(request: Request, module: str = "",
                 snapshot_ids: list[int] = Query(default=None),
                 inline: bool = False, token: str = "", access_token: str = ""):
    """浏览器直开的下载型接口。window.open 无法带请求头，故额外接受 token 查询参数
    （由认证中间件识别），此处仅做权限与业务处理。
    """
    module = _check_module(module)
    _check_module_perm(request, module)
    try:
        result = compare_service.run_compare(
            snapshot_ids or [], module=module,
            include_details=True, detail_limit=compare_service.MAX_DETAIL_LIMIT)
    except CompareError as e:
        _raise_compare_error(e)

    from backend.services.scan_compare_report import render_compare_html
    html_content = render_compare_html(result)

    base_id, target_id = result["base"]["id"], result["target"]["id"]
    _audit(request, "export_compare_html", f"{base_id}vs{target_id}",
           f"module={module};conn={result.get('connection_id')}")

    headers = {}
    if not inline:
        headers["Content-Disposition"] = (
            f"attachment; filename=ScanCompare_{module}_{base_id}_{target_id}.html")
    return Response(content=html_content, media_type="text/html; charset=utf-8",
                    headers=headers)


# ── 5. 存量数据回填 ──

@router.post("/snapshots/rebuild", summary="存量历史回填快照（admin/dba）")
def rebuild_snapshots(request: Request, payload: dict):
    role = _role(request)
    if role not in ("admin", "dba"):
        raise _err("E4031", "仅管理员或DBA可执行存量回填", status=403)
    module = _check_module((payload or {}).get("module") or "")
    _check_module_perm(request, module)
    limit = (payload or {}).get("limit") or 200
    overwrite = bool((payload or {}).get("overwrite", False))

    try:
        stat = snapshot_service.rebuild_snapshots(module, limit=limit, overwrite=overwrite)
    except ValueError as e:
        raise _err("E4006", str(e))

    _audit(request, "rebuild_snapshot", module,
           f"created={stat.get('created')};skipped={stat.get('skipped')};"
           f"failed={stat.get('failed')}")
    return stat


# ── 6. 对比报告留档 ──

@router.get("/reports", summary="对比报告留档列表")
def list_reports(request: Request, module: str = "", connection_id: str = "",
                 limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0)):
    if module:
        _check_module_perm(request, module)
    ensure_db()
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))

    where, args = [], []
    if module:
        where.append("module = ?")
        args.append(module)
    if connection_id:
        where.append("connection_id = ?")
        args.append(connection_id)
    cond = (" WHERE " + " AND ".join(where)) if where else ""

    conn = _get_connection()
    try:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM scan_compare_reports{cond}", args).fetchone()
        total = dict(total_row)["cnt"] if total_row else 0
        rows = conn.execute(
            f"SELECT id, module, connection_id, connection_name, db_name, title, "
            f"base_snapshot_id, target_snapshot_id, base_scan_at, target_scan_at, "
            f"base_total, target_total, fixed_count, new_count, remain_count, "
            f"changed_count, fix_rate, created_by, created_at "
            f"FROM scan_compare_reports{cond} ORDER BY created_at DESC, id DESC "
            f"LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        items = []
        for r in rows or []:
            d = dict(r)
            for k in ("base_scan_at", "target_scan_at", "created_at"):
                d[k] = snapshot_service._fmt_dt(d.get(k))
            items.append(d)
        return {"total": total, "items": items}
    finally:
        conn.close()


@router.post("/reports", summary="保存对比报告留档")
def save_report(request: Request, payload: dict):
    module = _check_module((payload or {}).get("module") or "")
    _check_module_perm(request, module)
    snapshot_ids = (payload or {}).get("snapshot_ids") or []
    title = ((payload or {}).get("title") or "")[:500]

    try:
        result = compare_service.run_compare(
            snapshot_ids, module=module, include_details=False)
    except CompareError as e:
        _raise_compare_error(e)

    s = result.get("summary") or {}
    base, target = result["base"], result["target"]
    if not title:
        title = (f"{result.get('connection_name') or ''} "
                 f"{base.get('scan_finished_at', '')[:10]} → "
                 f"{target.get('scan_finished_at', '')[:10]} 对比").strip()

    ensure_db()
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO scan_compare_reports
              (module, connection_id, connection_name, db_name,
               base_snapshot_id, target_snapshot_id, base_scan_at, target_scan_at,
               title, base_total, target_total, fixed_count, new_count,
               remain_count, changed_count, fix_rate, summary_json, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            module, result.get("connection_id") or "", result.get("connection_name") or "",
            result.get("db_name") or "",
            base.get("id"), target.get("id"),
            base.get("scan_finished_at") or None, target.get("scan_finished_at") or None,
            title,
            s.get("base_total", 0), s.get("target_total", 0),
            s.get("fixed_count", 0), s.get("new_count", 0),
            s.get("remain_count", 0), s.get("changed_count", 0),
            s.get("fix_rate", 0.0),
            json.dumps(s, ensure_ascii=False),
            _operator(request),
        ))
        conn.commit()
        report_id = getattr(cur, "lastrowid", None)
    finally:
        conn.close()

    _audit(request, "save_compare_report", str(report_id), f"module={module};title={title}")
    return {"id": report_id, "title": title}


@router.delete("/reports/{report_id}", summary="删除对比报告留档（仅 admin）")
def delete_report(report_id: int, request: Request):
    _require_admin(request)
    ensure_db()
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT module FROM scan_compare_reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            raise _err("E4004", "报告留档不存在", status=404)
        module = dict(row).get("module") or ""
        _check_module_perm(request, module)
        conn.execute("DELETE FROM scan_compare_reports WHERE id = ?", (report_id,))
        conn.commit()
    finally:
        conn.close()

    _audit(request, "delete_compare_report", str(report_id), f"module={module}")
    return {"status": "SUCCESS", "id": report_id}
