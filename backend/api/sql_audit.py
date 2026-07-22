"""
TDSQL SQL审核工具 - SQL审核 API

提供 RESTful 接口用于 SQL 审核和审核报告导出。
"""
from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse, Response
from typing import Optional
from urllib.parse import quote
import json
from datetime import datetime

from backend.models import (
    AuditRequest,
    AuditResponse,
    FileAuditRequest,
    FileAuditResponse,
    Violation,
)
from backend.services.audit_service import AuditService
from backend.services.database import _get_connection, ensure_db

router = APIRouter(prefix="/api/v1/audit", tags=["SQL审核"])

# 全局审核服务实例
audit_service = AuditService()


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


@router.post("/sql", response_model=AuditResponse, summary="审核单条SQL")
async def audit_sql(request: AuditRequest, http_request: Request):
    """
    审核单条 SQL 语句。

    - **sql**: 待审核的 SQL 语句
    - **project_id**: 项目ID（可选，绑定项目的规则集与门禁）
    """
    try:
        result, gate_result = audit_service.audit_single_sql(
            request.sql,
            created_by=_operator(http_request),
            project_id=request.project_id or "",
            evaluate_gate=bool(request.project_id),
        )
        return AuditResponse(
            passed=result.passed,
            violations=result.violations,
            sql_type=result.sql_type,
            gate_result=gate_result,
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
        results, summary, gate_result = audit_service.audit_file_content(
            request.content, file_path=request.file_path,
            created_by=_operator(http_request),
            project_id=request.project_id or "",
            evaluate_gate=bool(request.project_id),
        )
        return FileAuditResponse(results=results, summary=summary,
                                 gate_result=gate_result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件审核失败: {str(e)}")


@router.post("/upload", response_model=FileAuditResponse, summary="上传文件审核")
async def audit_upload(http_request: Request, file: UploadFile = File(...)):
    """
    上传文件进行 SQL 审核。

    支持 .sql、.xml 文件格式。
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
        results, summary, _ = audit_service.audit_file_content(
            text, file_path=file.filename,
            created_by=_operator(http_request),
        )
        return FileAuditResponse(results=results, summary=summary)
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="文件编码错误，请使用 UTF-8 编码")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"审核失败: {str(e)}")


@router.post("/batch-stream", summary="大文件/多SQL流式 NDJSON 审核")
async def audit_batch_stream(file: UploadFile = File(...)):
    """支持大文件 SQL 的异步流式批处理审核 (NDJSON 格式)"""
    from backend.services.database import split_sql_statements
    import json
    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="文件编码错误，请使用 UTF-8 编码")

    statements = split_sql_statements(text)

    async def stream_generator():
        for idx, stmt in enumerate(statements, 1):
            stmt_clean = stmt.strip()
            if not stmt_clean:
                continue
            res, _ = audit_service.audit_single_sql(stmt_clean)
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
        results, summary, _ = audit_service.audit_file_content(
            full_extracted_sql,
            file_path=filename,
            created_by=_operator(http_request),
            save_history=False
        )

        # 显式持久化落盘至 audit_history 表 (audit_type = 'extracted_schema')
        from backend.services.audit_service import _save_audit_history
        report_id = _save_audit_history(
            audit_type="extracted_schema",
            source=filename,
            results=results,
            summary=summary,
            created_by=_operator(http_request)
        )

        return {
            "status": "SUCCESS",
            "report_id": report_id,
            "filename": filename,
            "extracted_sql": full_extracted_sql,
            "results": results,
            "summary": summary
        }
    except Exception as e:
        logger.error(f"反向拉取元数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"拉取目标库元数据失败: {str(e)}")


@router.get("/extracted-reports", summary="在线元数据审核历史记录列表")
async def get_extracted_reports(limit: int = 20, offset: int = 0):
    """获取在线元数据审核的历史提取与审查列表"""
    ensure_db()
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT id, audit_type, source, total_sql, passed, failed, error_count,
                   warning_count, pass_rate, created_by, created_at, results_json
            FROM audit_history
            WHERE audit_type = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, ("extracted_schema", limit, offset)).fetchall()
        
        count_row = conn.execute("""
            SELECT COUNT(*) FROM audit_history 
            WHERE audit_type = ?
        """, ("extracted_schema",)).fetchone()
        
        if count_row:
            total = count_row[0] if isinstance(count_row, (tuple, list)) else list(count_row.values())[0]
        else:
            total = 0
        
        report_list = []
        for r in rows:
            if hasattr(r, "keys"):
                report_list.append(dict(r))
            elif isinstance(r, dict):
                report_list.append(r)
            else:
                report_list.append(dict(r))

        return {
            "total": total,
            "reports": report_list
        }
    finally:
        conn.close()


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
        .v-card {{ border: 1px solid #fee2e2; background: #fff5f5; padding: 10px 15px; border-radius: 6px; margin: 8px 0; font-size: 13px; }}
        .v-card.warning {{ border-color: #fef3c7; background: #fffbeb; }}
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
                sev_cls = "warning" if v.get("severity") == "WARNING" else "error"
                html_content += f"""
            <div class="v-card {sev_cls}">
                <b>[{v.get('rule_id')}] [{v.get('severity')}]</b> {v.get('message')}<br>
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
async def list_file_reports(limit: int = 50, offset: int = 0):
    """获取文件审核历史记录列表"""
    ensure_db()
    conn = _get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) AS cnt FROM audit_history WHERE audit_type = 'file'"
        ).fetchone()["cnt"]
        rows = conn.execute(
            """SELECT id, source, total_sql, passed, failed, error_count, warning_count,
                      pass_rate, created_by, created_at, gate_passed
               FROM audit_history WHERE audit_type = 'file'
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return {"items": [dict(r) for r in rows], "total": total}
    finally:
        conn.close()


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
.badge.ERROR {{ background:#fde8e8; color:#f56c6c; }} .badge.WARNING {{ background:#fdf6e8; color:#e6a23c; }} .badge.PASS {{ background:#e8f7e8; color:#67c23a; }}
.viol {{ margin:6px 0; padding:8px 12px; border-left:3px solid #f56c6c; background:#fef0f0; border-radius:0 4px 4px 0; font-size:13px; }}
.viol.warn {{ border-left-color:#e6a23c; background:#fdf6ec; }}
.viol .vr {{ font-weight:600; }} .viol .vm {{ color:#606266; margin:2px 0; }} .viol .vs {{ color:#67c23a; font-size:12px; }}
.footer {{ padding:16px 32px; text-align:center; font-size:12px; color:#909399; border-top:1px solid #ebeef5; }}
.no-data {{ padding:32px; text-align:center; color:#909399; }}
</style></head><body>
<div class="container">
<div class="header"><h1>TDSQL SQL审核平台 - 文件审核报告</h1><div class="sub">TDSQL SQL Audit Platform / File Audit Report</div></div>
<div class="meta">
<div class="meta-item"><span class="label">审核人:</span><span class="value">{report.get('created_by') or '匿名'}</span></div>
<div class="meta-item"><span class="label">文件名:</span><span class="value">{report.get('source', '-')}</span></div>
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
                sql_text = r.get("sql", "")[:300]
                sql_type = r.get("sql_type", "")
                line_no = r.get("line_number", "")
                status_badge = '<span class="badge PASS">通过</span>' if passed else f'<span class="badge ERROR">{len(violations)}项违规</span>'
                line_info = f" | 行号: {line_no}" if line_no else ""
                html_parts.append(f'<div class="sql-item"><div class="sh"><span><strong>#{i}</strong> {sql_type}{line_info}</span>{status_badge}</div><div class="sql-text">{sql_text}</div>')
                for v in violations:
                    sev = v.get("severity", "WARNING")
                    sev_class = "warn" if sev == "WARNING" else ""
                    rule_id = v.get("rule_id", "")
                    msg = v.get("message", "")
                    sug = v.get("suggestion", "")
                    sug_html = f'<div class="vs">建议: {sug}</div>' if sug else ""
                    html_parts.append(f'<div class="viol {sev_class}"><div class="vr">[{rule_id}] {sev}</div><div class="vm">{msg}</div>{sug_html}</div>')
                html_parts.append('</div>')

        html_parts.append(f'<div class="footer">TDSQL SQL审核平台 V2.0 | 报告生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | 报告ID: #{report.get("id")}</div></div></body></html>')
        html = "\n".join(html_parts)

        filename = f"TDSQL审核报告_{report.get('source', 'file')}_{time_display[:10]}.html"
        encoded_filename = quote(filename)
        return HTMLResponse(
            content=html,
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
