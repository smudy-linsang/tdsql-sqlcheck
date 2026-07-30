"""
TDSQL SQL审核工具 - SQL审核 API

提供 RESTful 接口用于 SQL 审核和审核报告导出。
"""
from fastapi import APIRouter, UploadFile, File, HTTPException, Query, Request, Form
from fastapi.responses import StreamingResponse, HTMLResponse, Response
from typing import Optional
from urllib.parse import quote
import html
import json
import logging
from datetime import datetime

from backend.models import (
    AuditRequest,
    AuditResponse,
    FileAuditRequest,
    FileAuditResponse,
    Violation,
)
from backend.services.audit_service import AuditService
from backend.services.database import _get_connection, ensure_db, log_operation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/audit", tags=["SQL审核"])

# 全局审核服务实例
audit_service = AuditService()


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


def _role(request: Request) -> str:
    return getattr(request.state, "role", "")


def _require_admin(request: Request, action: str):
    """删除类操作仅系统管理员可用。

    注意：/api/v1/audit/extracted-reports 未登记在 auth_service._PATH_TO_MENU 中，
    而未映射路径中间件默认放行（auth_service.py "无映射的路径默认放行"），
    因此权限必须在处理函数内显式把关，不能依赖中间件。
    """
    if _role(request) != "admin":
        raise HTTPException(status_code=403, detail=f"仅系统管理员可{action}")


def _active_scale() -> tuple[str, str]:
    """取当前全局生效规则集的 (id, name)，供审核响应标注尺度（带缓存，无额外开销）。"""
    try:
        from backend.services.ruleset_service import ruleset_service
        rid, _ = ruleset_service.get_active_overrides()
        info = ruleset_service.get_ruleset(rid) or {}
        return rid, info.get("name", rid)
    except Exception:
        return "default", ""


def _deprecated_params(project_id: Optional[str]) -> Optional[dict]:
    """V1.4：传了 project_id 则提示其不再决定尺度（静默忽略会让调用方误以为仍生效）。"""
    if project_id:
        return {"project_id": "V1.4 起规则集已改为管理员全局启用，本参数不再影响评估尺度，将在后续版本移除"}
    return None


def _scope_fields(ictx, skipped: int) -> dict:
    """V1.5：由实例类型上下文生成响应口径字段（自证口径，I4）。"""
    cn = "分布式" if ictx.instance_type.value == "distributed" else "集中式"
    notice = (f"本次按【{cn}实例】口径评估，已跳过 {skipped} 条不适用于该实例类型的规则。"
              if skipped else "")
    return {
        "instance_type": ictx.instance_type.value,
        "instance_type_source": ictx.source.value,
        "instance_type_conflict": ictx.conflict,
        "skipped_rules_count": skipped,
        "scope_notice": notice,
    }


def _audit_log(request: Request, operation_type: str, target_id: str, detail: str):
    """写操作审计日志；失败仅告警，不影响主流程"""
    try:
        client = getattr(request, "client", None)
        log_operation(
            operator=_operator(request),
            operation_type=operation_type,
            target_type="audit_history",
            target_id=str(target_id)[:64],
            detail=str(detail)[:500],
            ip_address=(client.host if client else ""),
            user_agent=request.headers.get("user-agent", "")[:200],
        )
    except Exception as e:
        logger.warning(f"审计日志写入失败: {e}")


@router.post("/sql", response_model=AuditResponse, summary="审核单条SQL")
async def audit_sql(request: AuditRequest, http_request: Request):
    """
    审核单条 SQL 语句。

    - **sql**: 待审核的 SQL 语句
    - **project_id**: 项目ID（可选，绑定项目的规则集与门禁）
    """
    try:
        # V1.4：尺度全局化；V1.5.1：实例类型由解析器多源分级得出（A类保守合并）
        result, gate_result, ictx = audit_service.audit_single_sql(
            request.sql,
            created_by=_operator(http_request),
            project_id=request.project_id or "",
            evaluate_gate=bool(request.connection_id),
            connection_id=request.connection_id or "",
            instance_type=request.instance_type,
        )
        rs_id, rs_name = _active_scale()
        skipped = audit_service.checker.count_skipped_by_scope(ictx.instance_type.value)
        return AuditResponse(
            passed=result.passed,
            violations=result.violations,
            sql_type=result.sql_type,
            gate_result=gate_result,
            rule_set_id=rs_id,
            rule_set_name=rs_name,
            deprecated_params=_deprecated_params(request.project_id),
            **_scope_fields(ictx, skipped),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"SQL解析失败: {str(e)}")


@router.post("/file", response_model=FileAuditResponse, summary="审核文件内容")
async def audit_file(request: FileAuditRequest, http_request: Request):
    """
    审核文件内容（支持 MyBatis XML、纯 SQL 文件）。

    - **content**: 文件内容
    - **file_path**: 文件路径（可选，用于 MyBatis XML 识别）
    - **project_id**: 项目ID（可选，绑定项目的规则集与门禁）
    """
    try:
        # V1.4：尺度全局化；V1.5：实例类型由解析器得出
        results, summary, gate_result, ictx = audit_service.audit_file_content(
            request.content, file_path=request.file_path,
            created_by=_operator(http_request),
            project_id=request.project_id or "",
            evaluate_gate=bool(request.connection_id),
            connection_id=request.connection_id or "",
            instance_type=request.instance_type,
        )
        rs_id, rs_name = _active_scale()
        skipped = audit_service.checker.count_skipped_by_scope(ictx.instance_type.value)
        return FileAuditResponse(results=results, summary=summary,
                                 gate_result=gate_result,
                                 rule_set_id=rs_id,
                                 rule_set_name=rs_name,
                                 deprecated_params=_deprecated_params(request.project_id),
                                 **_scope_fields(ictx, skipped))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件审核失败: {str(e)}")


@router.post("/upload", response_model=FileAuditResponse, summary="上传文件审核")
async def audit_upload(http_request: Request, file: UploadFile = File(...),
                       instance_type: Optional[str] = Form(None)):
    """
    上传文件进行 SQL 审核。

    支持 .sql、.xml 文件格式。
    V1.5：B类通道（无目标实例），instance_type 由调用方声明，未声明取全局默认。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名为空")

    allowed_extensions = (".sql", ".xml")
    if not file.filename.lower().endswith(allowed_extensions):
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式，仅支持: {', '.join(allowed_extensions)}",
        )

    try:
        content = await file.read()
        text = content.decode("utf-8")
        results, summary, _, ictx = audit_service.audit_file_content(
            text, file_path=file.filename,
            created_by=_operator(http_request),
            instance_type=instance_type,
        )
        skipped = audit_service.checker.count_skipped_by_scope(ictx.instance_type.value)
        return FileAuditResponse(results=results, summary=summary,
                                 **_scope_fields(ictx, skipped))
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="文件编码错误，请使用 UTF-8 编码")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"审核失败: {str(e)}")


@router.post("/batch-stream", summary="大文件/多SQL流式 NDJSON 审核")
async def audit_batch_stream(file: UploadFile = File(...),
                             instance_type: Optional[str] = Form(None),
                             meta: int = Query(1)):
    """支持大文件 SQL 的异步流式批处理审核 (NDJSON 格式)。

    V1.5：B类通道，instance_type 由调用方声明（未声明取全局默认）；
    首帧输出 type=meta 元信息帧，不识别的外部消费方可传 ?meta=0 关闭（默认开启）。
    """
    from backend.services.database import split_sql_statements
    import json
    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="文件编码错误，请使用 UTF-8 编码")

    statements = split_sql_statements(text)

    # V1.5：解析一次实例类型，随流传递（B类通道，无 connection_id）
    ictx = audit_service._resolve_instance("", instance_type)
    it = ictx.instance_type.value
    skipped = audit_service.checker.count_skipped_by_scope(it)

    async def stream_generator():
        # V1.5：NDJSON 首帧元信息（唯一破坏性变更，?meta=0 可关闭）
        if meta != 0:
            yield json.dumps({
                "type": "meta",
                "instance_type": it,
                "instance_type_source": ictx.source.value,
                "skipped_rules_count": skipped,
            }, ensure_ascii=False) + "\n"
        for idx, stmt in enumerate(statements, 1):
            stmt_clean = stmt.strip()
            if not stmt_clean:
                continue
            res, _, _ = audit_service.audit_single_sql(stmt_clean, instance_type=it)
            item = {
                "index": idx,
                "passed": res.passed,
                "violations_count": len(res.violations),
                "violations": [{"rule_id": v.rule_id, "message": v.message, "severity": str(v.severity)} for v in res.violations]
            }
            yield json.dumps(item, ensure_ascii=False) + "\n"

    return StreamingResponse(stream_generator(), media_type="application/x-ndjson")


@router.post("/extract-and-audit", summary="反向拉取元数据生成SQL文件并审核")
async def extract_and_audit(http_request: Request, payload: dict):
    """
    拉取指定 TDSQL 实例与数据库的元数据（表/索引/视图），
    反向生成完整 .sql 文件并提交文件审核引擎进行规则化审核。
    """
    connection_id = payload.get("connection_id")
    database_name = payload.get("database") or payload.get("database_name") or ""
    scopes = payload.get("scopes") or ["TABLE", "INDEX", "VIEW", "SHARDKEY"]
    if not connection_id:
        raise HTTPException(status_code=400, detail="请选择目标数据库实例")
    _started_at = datetime.now().isoformat()   # V1.3: 快照 scan_started_at

    from backend.services.connection_registry import registry, ConnectionNotFoundError
    try:
        pool = registry.get(connection_id)
        conn_info = registry.get_saved(connection_id) or {}
    except ConnectionNotFoundError:
        raise HTTPException(status_code=400, detail="选定的数据库实例未激活，请在「实例管理」中连接或重试")

    try:
        from backend.connectors.metadata_fetcher import MetadataFetcher
        fetcher = MetadataFetcher(pool)
        
        # 1. 抓取该库下的表清单与 VIEW 列表
        target_db = database_name or conn_info.get("database", "mysql")
        
        extracted_sqls = []
        extracted_sqls.append(f"-- ============================================================================")
        extracted_sqls.append(f"-- TDSQL 自动拉取的最新在线元数据描述文件")
        host_str = conn_info.get('host', 'TDSQL')
        port_str = conn_info.get('port', 3306)
        extracted_sqls.append(f"-- 目标实例: {conn_info.get('name', 'TDSQL')} ({host_str}:{port_str})")
        extracted_sqls.append(f"-- 目标数据库: {target_db}")
        extracted_sqls.append(f"-- 提取日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        extracted_sqls.append(f"-- ============================================================================\n")

        with pool.get_connection() as conn:
            cursor = conn.cursor()
            # 获取数据库下所有的 TABLES 与 VIEWS
            cursor.execute("""
                SELECT TABLE_NAME, TABLE_TYPE 
                FROM information_schema.TABLES 
                WHERE TABLE_SCHEMA = %s
            """, (target_db,))
            db_objects = cursor.fetchall()
            
            for obj in db_objects:
                obj_name = obj.get("TABLE_NAME") or obj.get("table_name")
                obj_type = obj.get("TABLE_TYPE") or obj.get("table_type")
                if not obj_name:
                    continue
                
                if "TABLE" in scopes and "VIEW" not in obj_type.upper():
                    try:
                        cursor.execute(f"SHOW CREATE TABLE `{target_db}`.`{obj_name}`")
                        res = cursor.fetchone()
                        create_sql = ""
                        if res and isinstance(res, dict):
                            create_sql = res.get("Create Table") or res.get("CREATE TABLE") or ""
                            if not create_sql:
                                for v in res.values():
                                    val_str = str(v or "").strip()
                                    if "CREATE" in val_str.upper():
                                        create_sql = val_str
                                        break
                        if create_sql:
                            extracted_sqls.append(f"-- SQL Object: CREATE TABLE")
                            extracted_sqls.append(f"-- Table: {obj_name}")
                            extracted_sqls.append(f"{create_sql.rstrip(';')};\n")
                    except Exception as e:
                        logger.warning(f"拉取表 {obj_name} DDL 失败: {e}")
                        
                elif "VIEW" in scopes and "VIEW" in obj_type.upper():
                    try:
                        cursor.execute(f"SHOW CREATE VIEW `{target_db}`.`{obj_name}`")
                        res = cursor.fetchone()
                        create_sql = ""
                        if res and isinstance(res, dict):
                            create_sql = res.get("Create View") or res.get("CREATE VIEW") or ""
                            if not create_sql:
                                for v in res.values():
                                    val_str = str(v or "").strip()
                                    if "CREATE" in val_str.upper():
                                        create_sql = val_str
                                        break
                        if create_sql:
                            extracted_sqls.append(f"-- SQL Object: CREATE VIEW")
                            extracted_sqls.append(f"-- View: {obj_name}")
                            extracted_sqls.append(f"{create_sql.rstrip(';')};\n")
                    except Exception as e:
                        logger.warning(f"拉取视图 {obj_name} DDL 失败: {e}")

        full_extracted_sql = "\n".join(extracted_sqls)
        filename = f"extracted_{target_db}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"

        # 2. 调用文件审核引擎进行规则化全面评估
        # V1.4：尺度取自全局生效规则集；V1.5：传 connection_id（A类通道，自动解析实例类型）
        from backend.services.ruleset_service import ruleset_service as _rs_svc
        _rule_set_id = _rs_svc.get_active_rule_set_id()
        results, summary, _, ictx = audit_service.audit_file_content(
            full_extracted_sql,
            file_path=filename,
            created_by=_operator(http_request),
            connection_id=connection_id,
            save_history=False
        )
        _skipped = audit_service.checker.count_skipped_by_scope(ictx.instance_type.value)

        # 显式持久化落盘至 audit_history 表 (audit_type = 'extracted_schema')
        # V1.3(D1): 补落 connection_id / db_name；V1.4: rule_set_id；V1.5: 实例类型口径
        from backend.services.audit_service import _save_audit_history
        report_id = _save_audit_history(
            audit_type="extracted_schema",
            source=filename,
            results=results,
            summary=summary,
            created_by=_operator(http_request),
            connection_id=connection_id,
            db_name=target_db,
            rule_set_id=_rule_set_id,
            instance_ctx=ictx,
            skipped_rules_count=_skipped,
        )

        # V1.3: 旁路生成对比快照（失败仅告警，不影响审核主流程）
        snapshot_id = None
        try:
            from backend.services.snapshot_extractors.schema_audit import extract as _extract
            from backend.services import scan_snapshot_service as _snap
            _items, _obj_total = _extract(results, target_db, node="")
            snapshot_id = _snap.safe_create_snapshot("schema_audit", {
                "biz_ref_id": str(report_id),
                "connection_id": connection_id,
                "connection_name": conn_info.get("name", ""),
                "db_name": target_db,
                "node": "",
                "scan_label": filename,
                "scan_started_at": _started_at,
                "scan_finished_at": datetime.now().isoformat(),
                "created_by": _operator(http_request),
                "rule_set_id": _rule_set_id,
                "instance_type": ictx.instance_type.value,
            }, _items, _obj_total)
        except Exception as e:
            logger.warning(f"生成元数据审核快照失败: {e}")

        return {
            "status": "SUCCESS",
            "report_id": report_id,
            "snapshot_id": snapshot_id,
            "filename": filename,
            "extracted_sql": full_extracted_sql,
            "results": results,
            "summary": summary,
            # V1.5：响应自证口径（改完这一处，用户报告的 R077 误报即消失）
            **_scope_fields(ictx, _skipped),
        }
    except Exception as e:
        logger.error(f"反向拉取元数据失败: {e}")
        raise HTTPException(status_code=400, detail=f"拉取目标库元数据失败: {str(e)}")


@router.get("/extracted-reports", summary="在线元数据审核历史记录列表")
async def get_extracted_reports(limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0),
                                connection_id: str = "", db_name: str = "",
                                date_from: str = "", date_to: str = ""):
    """获取在线元数据审核的历史提取与审查列表

    V1.3(D1): 支持按实例/库名/时间范围筛选，并回显实例名。
    列表不再返回 results_json 大字段（前端未使用，明细走 /report/{id}/html）。
    """
    ensure_db()
    conn = _get_connection()
    try:
        where, args = ["h.audit_type = ?"], ["extracted_schema"]
        if connection_id == "__unknown__":
            where.append("(h.connection_id IS NULL OR h.connection_id = '')")
        elif connection_id:
            where.append("h.connection_id = ?")
            args.append(connection_id)
        if db_name:
            where.append("h.db_name = ?")
            args.append(db_name)
        if date_from:
            where.append("DATE(h.created_at) >= ?")
            args.append(date_from)
        if date_to:
            where.append("DATE(h.created_at) <= ?")
            args.append(date_to)
        cond = " AND ".join(where)

        rows = conn.execute(f"""
            SELECT h.id, h.audit_type, h.source, h.total_sql, h.passed, h.failed,
                   h.error_count, h.warning_count, h.pass_rate, h.created_by, h.created_at,
                   h.connection_id, h.db_name, COALESCE(c.name, '') AS connection_name,
                   h.instance_type, h.instance_type_source, h.skipped_rules_count
            FROM audit_history h
            LEFT JOIN tdsql_connections c ON c.id = h.connection_id
            WHERE {cond}
            ORDER BY h.created_at DESC
            LIMIT ? OFFSET ?
        """, (*args, limit, offset)).fetchall()

        count_row = conn.execute(f"""
            SELECT COUNT(*) AS cnt FROM audit_history h WHERE {cond}
        """, args).fetchone()
        total = dict(count_row).get("cnt", 0) if count_row else 0

        report_list = [dict(r) for r in (rows or [])]

        return {
            "total": total,
            "reports": report_list
        }
    finally:
        conn.close()


# 单批删除上限：既防一次误清全表，也避免超长 IN 列表拖垮元数据库
MAX_DELETE_BATCH = 500


@router.post("/extracted-reports/batch-delete",
             summary="批量删除在线元数据审核历史记录（仅系统管理员）")
async def batch_delete_extracted_reports(payload: dict, http_request: Request):
    """按 id 批量删除"历史元数据审核记录"。

    - 仅 admin 可调用；
    - 只删 audit_type='extracted_schema'，避免误伤文件审核（'file'）等其它类型；
    - audit_results 由外键 ON DELETE CASCADE 自动清理；
      gate_audit_logs 为 ON DELETE SET NULL，门禁合规痕迹保留不受影响；
    - purge_snapshots=True 时同时删除对应的对比基线快照（module='schema_audit'），
      但被 scan_compare_reports 留档引用的快照一律保留——否则留档会指向不存在的快照。
      默认 False：快照是 V1.3 设计中独立冻结的比对基线，保留策略也按独立表清理，
      静默摧毁基线是不可逆且更糟的后果。
    """
    _require_admin(http_request, "删除历史元数据审核记录")

    raw_ids = (payload or {}).get("ids") or []
    purge_snapshots = bool((payload or {}).get("purge_snapshots"))
    try:
        ids = sorted({int(i) for i in raw_ids if int(i) > 0})
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="ids 必须为正整数列表")
    if not ids:
        raise HTTPException(status_code=400, detail="请至少勾选一条待删除记录")
    if len(ids) > MAX_DELETE_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多删除 {MAX_DELETE_BATCH} 条，请缩小筛选范围后分批操作")

    ensure_db()
    conn = _get_connection()
    try:
        ph = ",".join(["?"] * len(ids))
        rows = conn.execute(
            f"""SELECT id, source FROM audit_history
                WHERE id IN ({ph}) AND audit_type = ?""",
            (*ids, "extracted_schema")).fetchall()
        found_ids = [dict(r)["id"] for r in (rows or [])]
        if not found_ids:
            raise HTTPException(status_code=404, detail="勾选的记录不存在或已被删除")

        fph = ",".join(["?"] * len(found_ids))
        biz_refs = [str(i) for i in found_ids]

        # 关联快照：module='schema_audit' 且 biz_ref_id 指向 audit_history.id
        snap_rows = conn.execute(
            f"""SELECT id FROM scan_snapshots
                WHERE module = ? AND biz_ref_id IN ({fph})""",
            ("schema_audit", *biz_refs)).fetchall()
        snap_ids = [dict(r)["id"] for r in (snap_rows or [])]

        snapshots_deleted = 0
        kept_referenced = 0
        if purge_snapshots and snap_ids:
            sph = ",".join(["?"] * len(snap_ids))
            ref_rows = conn.execute(
                f"""SELECT DISTINCT s.id FROM scan_snapshots s
                    JOIN scan_compare_reports r
                      ON r.base_snapshot_id = s.id OR r.target_snapshot_id = s.id
                    WHERE s.id IN ({sph})""", tuple(snap_ids)).fetchall()
            referenced = {dict(r)["id"] for r in (ref_rows or [])}
            kept_referenced = len(referenced)
            deletable = [i for i in snap_ids if i not in referenced]
            if deletable:
                dph = ",".join(["?"] * len(deletable))
                cur = conn.execute(
                    f"DELETE FROM scan_snapshots WHERE id IN ({dph})", tuple(deletable))
                snapshots_deleted = getattr(cur, "rowcount", 0) or 0

        cur = conn.execute(
            f"DELETE FROM audit_history WHERE id IN ({fph}) AND audit_type = ?",
            (*found_ids, "extracted_schema"))
        deleted = getattr(cur, "rowcount", 0) or 0
        conn.commit()
    finally:
        conn.close()

    skipped_ids = [i for i in ids if i not in set(found_ids)]
    _audit_log(http_request, "delete_audit_history",
               ",".join(str(i) for i in found_ids),
               f"deleted={deleted};purge_snapshots={purge_snapshots};"
               f"snapshots_found={len(snap_ids)};snapshots_deleted={snapshots_deleted};"
               f"snapshots_kept_referenced={kept_referenced};skipped={len(skipped_ids)}")

    return {
        "status": "SUCCESS",
        "deleted": deleted,
        "deleted_ids": found_ids,
        "skipped_ids": skipped_ids,
        "snapshots_found": len(snap_ids),
        "snapshots_deleted": snapshots_deleted,
        "snapshots_kept": len(snap_ids) - snapshots_deleted,
        "snapshots_kept_referenced": kept_referenced,
    }


@router.get("/report/{report_id}/html", summary="导出元数据审核报告HTML")
async def export_extracted_report_html(report_id: int):
    """导出指定在线元数据审核记录的精美 HTML 格式报告"""
    ensure_db()
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM audit_history WHERE id = ?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="审核报告不存在")
        
        r_dict = dict(row) if not isinstance(row, dict) else row
        try:
            results_data = json.loads(r_dict.get("results_json") or "[]")
        except Exception:
            results_data = []

        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>TDSQL 在线元数据规则审核报告 - {r_dict.get('source')}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background:#f4f6f9; color:#333; margin:0; padding:20px; }}
        .container {{ max-width: 1000px; margin: 0 auto; background: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
        .header {{ border-bottom: 2px solid #2563eb; padding-bottom: 15px; margin-bottom: 20px; }}
        .header h1 {{ margin: 0; font-size: 24px; color: #0f1e34; }}
        .meta {{ font-size: 13px; color: #666; margin-top: 8px; }}
        .kpi-grid {{ display: flex; gap: 15px; margin-bottom: 25px; }}
        .kpi-card {{ flex: 1; background: #f8fafc; padding: 15px; border-radius: 6px; text-align: center; border: 1px solid #e2e8f0; }}
        .kpi-num {{ font-size: 22px; font-weight: bold; margin-bottom: 4px; }}
        .v-card {{ padding: 10px 15px; border-radius: 6px; margin: 8px 0; font-size: 13px; border: 1px solid #e5e7eb; border-left: 4px solid #9ca3af; background: #f3f4f6; }}
        .v-card.error {{ border-color: #fee2e2; border-left-color: #ef4444; background: #fef2f2; }}
        .v-card.warning {{ border-color: #fef3c7; border-left-color: #f59e0b; background: #fffbeb; }}
        .v-card.info {{ border-color: #e5e7eb; border-left-color: #9ca3af; background: #f3f4f6; }}
        .sql-box {{ background: #0f1e34; color: #e2e8f0; padding: 12px; border-radius: 6px; font-family: monospace; font-size: 13px; overflow-x: auto; white-space: pre-wrap; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>TDSQL 在线元数据规则审核报告</h1>
            <div class="meta">提取文件: <b>{r_dict.get('source')}</b> | 审核人: {r_dict.get('created_by') or 'System'} | 审计时间: {r_dict.get('created_at')}</div>
        </div>
        <div class="kpi-grid">
            <div class="kpi-card"><div class="kpi-num">{r_dict.get('total_sql')}</div><div>对象总数</div></div>
            <div class="kpi-card"><div class="kpi-num" style="color:#16a34a">{r_dict.get('passed')}</div><div>通过数</div></div>
            <div class="kpi-card"><div class="kpi-num" style="color:#dc2626">{r_dict.get('failed')}</div><div>未通过数</div></div>
            <div class="kpi-card"><div class="kpi-num" style="color:#2563eb">{r_dict.get('pass_rate', 0):.1f}%</div><div>整体通过率</div></div>
        </div>
        <h2>元数据审核明细列表</h2>
"""
        for idx, res in enumerate(results_data, 1):
            passed_tag = '<span style="color:#16a34a;font-weight:bold">[通过]</span>' if res.get('passed') else f'<span style="color:#dc2626;font-weight:bold">[{len(res.get("violations", []))}项违规]</span>'
            html_content += f"""
        <div style="margin-bottom: 20px; border-bottom: 1px dashed #e2e8f0; padding-bottom: 15px;">
            <h3>#{idx} {res.get('sql_type', 'DDL')} {passed_tag}</h3>
            <div class="sql-box">{res.get('sql', '')}</div>
"""
            for v in res.get("violations", []):
                sev_u = str(v.get("severity", "WARNING")).upper()
                sev_cls = "error" if sev_u in ("ERROR", "FATAL", "CRITICAL") else ("warning" if sev_u in ("WARNING", "WARN") else "info")
                html_content += f"""
            <div class="v-card {sev_cls}">
                <b>[{v.get('rule_id')}] [{sev_u}]</b> {v.get('message')}<br>
                💡 <b>修复建议：</b>{v.get('suggestion', '无')}
            </div>
"""
            html_content += "        </div>"

        html_content += "    </div>\n</body>\n</html>"
        return Response(content=html_content, media_type="text/html", headers={"Content-Disposition": f"attachment; filename=Extracted_Schema_Report_{report_id}.html"})
    finally:
        conn.close()


@router.get("/report/{report_id}/sql", summary="下载历史提取的元数据SQL文件")
async def download_extracted_report_sql(report_id: int):
    """下载指定在线元数据审核历史中生成的元数据 .sql 文件"""
    ensure_db()
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM audit_history WHERE id = ?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="审核记录不存在")
        
        r_dict = dict(row) if not isinstance(row, dict) else row
        try:
            results_data = json.loads(r_dict.get("results_json") or "[]")
        except Exception:
            results_data = []

        sql_blocks = []
        for r in results_data:
            if r.get("sql"):
                sql_blocks.append(f"-- SQL Object: {r.get('sql_type', 'DDL')}\n{r.get('sql')}")
        
        full_sql = "\n\n".join(sql_blocks)
        filename = r_dict.get("source") or f"extracted_{report_id}.sql"
        if not filename.endswith(".sql"):
            filename += ".sql"

        return Response(
            content=full_sql,
            media_type="text/plain;charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={quote(filename)}"}
        )
    finally:
        conn.close()




@router.get("/rules", summary="获取审核规则列表")
async def get_rules():
    """获取所有已启用的审核规则列表"""
    return {"rules": audit_service.get_rule_list()}


@router.get("/report/{report_id}/export", summary="导出审核报告PDF")
async def export_audit_report(report_id: int):
    """
    导出指定审核记录的PDF报告。

    报告包含：
    - 审核摘要（SQL总数、通过率、各级别统计）
    - 违规详情（规则ID、严重级别、描述）
    - 优化建议汇总

    Args:
        report_id: audit_history 表中的记录ID
    """
    try:
        from backend.services.report_service import generate_audit_report_pdf
        pdf_bytes, filename = generate_audit_report_pdf(report_id)
        return StreamingResponse(
            iter([pdf_bytes]),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(pdf_bytes)),
            },
        )
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="reportlab 未安装，请执行: pip install reportlab",
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF生成失败: {str(e)}")


# ============ 文件审核报告 ============

@router.get("/file-reports", summary="获取文件审核报告列表")
async def list_file_reports(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    """获取文件审核历史记录列表"""
    ensure_db()
    conn = _get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) AS cnt FROM audit_history WHERE audit_type = 'file'"
        ).fetchone()["cnt"]
        rows = conn.execute(
            """SELECT id, source, total_sql, passed, failed, error_count, warning_count,
                      pass_rate, created_by, created_at, gate_passed, instance_type
               FROM audit_history WHERE audit_type = 'file'
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return {"items": [dict(r) for r in rows], "total": total}
    finally:
        conn.close()


@router.post("/file-reports/batch-delete",
             summary="批量删除文件审核报告（仅系统管理员）")
async def batch_delete_file_reports(payload: dict, http_request: Request):
    """按 id 批量删除文件审核报告历史记录（audit_type='file'）。
    - 仅 admin 可调用 (_require_admin)；
    - 仅删除 audit_type='file' 的记录，隔离安全边界；
    - 支持写审计日志。
    """
    _require_admin(http_request, "删除文件审核报告")

    raw_ids = (payload or {}).get("ids") or []
    try:
        ids = sorted({int(i) for i in raw_ids if int(i) > 0})
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="ids 必须为正整数列表")
    if not ids:
        raise HTTPException(status_code=400, detail="请至少勾选一条待删除记录")
    if len(ids) > MAX_DELETE_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多删除 {MAX_DELETE_BATCH} 条，请缩小筛选范围后分批操作")

    ensure_db()
    conn = _get_connection()
    try:
        ph = ",".join(["?"] * len(ids))
        rows = conn.execute(
            f"""SELECT id FROM audit_history
                WHERE id IN ({ph}) AND audit_type = ?""",
            (*ids, "file")).fetchall()
        found_ids = [dict(r)["id"] for r in (rows or [])]
        if not found_ids:
            raise HTTPException(status_code=404, detail="勾选的记录不存在或已被删除")

        fph = ",".join(["?"] * len(found_ids))
        cur = conn.execute(
            f"DELETE FROM audit_history WHERE id IN ({fph}) AND audit_type = ?",
            (*found_ids, "file"))
        deleted = getattr(cur, "rowcount", 0) or 0
        conn.commit()
    finally:
        conn.close()

    skipped_ids = [i for i in ids if i not in set(found_ids)]
    _audit_log(http_request, "delete_file_audit_history",
               ",".join(str(i) for i in found_ids),
               f"deleted={deleted};skipped={len(skipped_ids)}")

    return {
        "status": "SUCCESS",
        "deleted": deleted,
        "deleted_ids": found_ids,
        "skipped_ids": skipped_ids,
    }


@router.get("/file-reports/{report_id}/html", summary="下载文件审核HTML报告")
async def export_file_report_html(report_id: int):
    """生成并下载指定文件审核记录的HTML报告"""
    try:
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM audit_history WHERE id = %s AND audit_type = 'file'",
                (report_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="审核报告不存在")
            report = dict(row)
        finally:
            conn.close()

        results = json.loads(report.get("results_json") or "[]")
        created_at = report.get("created_at", "")
        time_display = created_at[:19].replace("T", " ") if isinstance(created_at, str) else str(created_at)[:19]
        pass_rate = float(report.get("pass_rate") or 0)
        rate_class = "pass" if pass_rate >= 80 else "warn" if pass_rate >= 50 else "fail"

        itype = report.get("instance_type") or ""
        inst_type_cn = "集中式规则" if itype == "centralized" else ("分布式规则" if itype == "distributed" else "分布式规则")

        html_parts = []
        html_parts.append(f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TDSQL SQL审核报告 - {report.get('source', '未知文件')}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:"Microsoft YaHei","Segoe UI",Arial,sans-serif; background:#f0f2f5; color:#303030; padding:20px; }}
.container {{ max-width:900px; margin:0 auto; background:#fff; border-radius:8px; box-shadow:0 2px 12px rgba(0,0,0,0.08); overflow:hidden; }}
.header {{ background:#1a1a2e; color:#fff; padding:24px 32px; }}
.header h1 {{ font-size:22px; margin-bottom:6px; }}
.header .sub {{ font-size:13px; color:#a0aec0; }}
.meta {{ display:flex; flex-wrap:wrap; gap:24px; padding:20px 32px; background:#f7f8fa; border-bottom:1px solid #ebeef5; }}
.meta-item {{ font-size:14px; }}
.meta-item .label {{ color:#909399; margin-right:6px; }}
.meta-item .value {{ font-weight:600; }}
.summary {{ display:flex; gap:16px; padding:24px 32px; flex-wrap:wrap; }}
.sc {{ flex:1; min-width:100px; text-align:center; padding:16px; border-radius:6px; }}
.sc.total {{ background:#e8f4fd; }} .sc.pass {{ background:#e8f7e8; }} .sc.fail {{ background:#fde8e8; }}
.sc.rate.pass {{ background:#e8f7e8; }} .sc.rate.warn {{ background:#fdf6e8; }} .sc.rate.fail {{ background:#fde8e8; }}
.sc .num {{ font-size:28px; font-weight:700; }} .sc .lbl {{ font-size:12px; color:#606266; margin-top:4px; }}
.stitle {{ padding:16px 32px 8px; font-size:16px; font-weight:600; border-top:1px solid #ebeef5; }}
.sql-item {{ margin:0 32px 16px; padding:16px; border:1px solid #ebeef5; border-radius:6px; }}
.sql-item .sh {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
.sql-text {{ font-family:Consolas,Courier New,monospace; font-size:13px; background:#f5f7fa; padding:8px 12px; border-radius:4px; margin:8px 0; white-space:pre-wrap; word-break:break-all; }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; }}
.badge.ERROR {{ background:#fde8e8; color:#f56c6c; }} .badge.WARNING {{ background:#fdf6e8; color:#e6a23c; }} .badge.INFO {{ background:#f4f4f5; color:#909399; }} .badge.PASS {{ background:#e8f7e8; color:#67c23a; }}
.viol {{ margin:6px 0; padding:8px 12px; border-left:3px solid #909399; background:#f4f4f5; border-radius:0 4px 4px 0; font-size:13px; }}
.viol.error {{ border-left-color:#f56c6c; background:#fef0f0; }}
.viol.warn {{ border-left-color:#e6a23c; background:#fdf6ec; }}
.viol.info {{ border-left-color:#909399; background:#f4f4f5; }}
.viol .vr {{ font-weight:600; color:#303133; }}
.viol.error .vr {{ color:#f56c6c; }}
.viol.warn .vr {{ color:#e6a23c; }}
.viol.info .vr {{ color:#606266; }}
.viol .vm {{ color:#606266; margin:2px 0; }} .viol .vs {{ color:#67c23a; font-size:12px; }}
.footer {{ padding:16px 32px; text-align:center; font-size:12px; color:#909399; border-top:1px solid #ebeef5; }}
.no-data {{ padding:32px; text-align:center; color:#909399; }}
</style></head><body>
<div class="container">
<div class="header"><h1>TDSQL SQL审核平台 - 文件审核报告</h1><div class="sub">TDSQL SQL Audit Platform / File Audit Report</div></div>
<div class="meta">
<div class="meta-item"><span class="label">审核人:</span><span class="value">{report.get('created_by') or '匿名'}</span></div>
<div class="meta-item"><span class="label">文件名:</span><span class="value">{report.get('source', '-')}</span></div>
<div class="meta-item"><span class="label">规则架构:</span><span class="value" style="color:#2563eb">{inst_type_cn}</span></div>
<div class="meta-item"><span class="label">审核时间:</span><span class="value">{time_display}</span></div>
<div class="meta-item"><span class="label">报告ID:</span><span class="value">#{report.get('id')}</span></div>
</div>
<div class="summary">
<div class="sc total"><div class="num">{report.get('total_sql', 0)}</div><div class="lbl">SQL总数</div></div>
<div class="sc pass"><div class="num">{report.get('passed', 0)}</div><div class="lbl">通过</div></div>
<div class="sc fail"><div class="num">{report.get('failed', 0)}</div><div class="lbl">未通过</div></div>
<div class="sc rate {rate_class}"><div class="num">{pass_rate:.1f}%</div><div class="lbl">通过率</div></div>
<div class="sc total"><div class="num" style="color:#f56c6c">{report.get('error_count', 0)}</div><div class="lbl">ERROR</div></div>
<div class="sc total"><div class="num" style="color:#e6a23c">{report.get('warning_count', 0)}</div><div class="lbl">WARNING</div></div>
</div>
<div class="stitle">逐条审核结果（共 {len(results)} 条）</div>""")

        if not results:
            html_parts.append('<div class="no-data">无审核结果数据</div>')
        else:
            for i, r in enumerate(results, 1):
                passed = r.get("passed", False)
                violations = r.get("violations", [])
                sql_text = html.escape(r.get("sql", ""))
                sql_type = r.get("sql_type", "")
                line_no = r.get("line_number", "")
                status_badge = '<span class="badge PASS">通过</span>' if passed else f'<span class="badge ERROR">{len(violations)}项违规</span>'
                line_info = f" | 行号: {line_no}" if line_no else ""
                html_parts.append(f'<div class="sql-item"><div class="sh"><span><strong>#{i}</strong> {sql_type}{line_info}</span>{status_badge}</div><div class="sql-text">{sql_text}</div>')
                for v in violations:
                    sev = str(v.get("severity", "WARNING")).upper()
                    sev_class = "error" if sev in ("ERROR", "FATAL", "CRITICAL") else ("warn" if sev in ("WARNING", "WARN") else "info")
                    rule_id = v.get("rule_id", "")
                    msg = v.get("message", "")
                    sug = v.get("suggestion", "")
                    sug_html = f'<div class="vs">建议: {sug}</div>' if sug else ""
                    html_parts.append(f'<div class="viol {sev_class}"><div class="vr">[{rule_id}] {sev}</div><div class="vm">{msg}</div>{sug_html}</div>')
                html_parts.append('</div>')

        html_parts.append(f'<div class="footer">TDSQL SQL审核平台 V2.0 | 报告生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | 报告ID: #{report.get("id")}</div></div></body></html>')
        html_content = "\n".join(html_parts)

        filename = f"TDSQL审核报告_{report.get('source', 'file')}_{time_display[:10]}.html"
        encoded_filename = quote(filename)
        return HTMLResponse(
            content=html_content,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HTML报告生成失败: {str(e)}")

@router.get("/slow-report/{slow_id}/export", summary="导出慢SQL分析报告PDF")
async def export_slow_query_report(slow_id: int):
    """
    导出指定慢SQL记录的分析报告PDF。

    报告包含：
    - 基本信息（执行次数、耗时、扫描行数等）
    - SQL文本
    - 分析结果（问题类型、根因、建议）
    - 优化建议与优化后SQL

    Args:
        slow_id: slow_queries 表中的记录ID
    """
    try:
        from backend.services.report_service import generate_slow_query_report_pdf
        pdf_bytes, filename = generate_slow_query_report_pdf(slow_id)
        return StreamingResponse(
            iter([pdf_bytes]),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(pdf_bytes)),
            },
        )
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="reportlab 未安装，请执行: pip install reportlab",
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF生成失败: {str(e)}")
