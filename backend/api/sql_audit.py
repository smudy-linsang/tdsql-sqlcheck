"""
TDSQL SQL审核工具 - SQL审核 API

提供 RESTful 接口用于 SQL 审核和审核报告导出。
"""
from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.models import (
    AuditRequest,
    AuditResponse,
    FileAuditRequest,
    FileAuditResponse,
    Violation,
)
from backend.services.audit_service import AuditService

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
