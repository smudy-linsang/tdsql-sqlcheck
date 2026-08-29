"""G11 网关日志分析 API 路由"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from typing import List, Optional
from pydantic import BaseModel
from backend.services.gateway_log_service import gateway_log_service

router = APIRouter(prefix="/api/v1/gateway-log", tags=["Gateway Log"])

# v1.6.2.2-UAT-O-10：报告是含内联脚本的完整 HTML 文档。前端以 iframe 嵌入，
# srcdoc 会继承主文档 CSP（script-src 无 unsafe-inline）导致报告交互脚本被拦。
# 报告响应使用文档级专用 CSP：允许内联脚本（报告内容在落库时已由分析器生成，
# 无法回填 nonce）；同时把 frame-ancestors 收窄到 'self'、X-Frame-Options 收窄到
# SAMEORIGIN——只允许被本应用同源页面嵌入，仍禁止被外站 iframe。
_REPORT_DOC_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'none'"
    ),
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

class ReportSummary(BaseModel):
    pass
class ReportItem(BaseModel):
    id: int
    connection_id: str
    log_file_name: str
    log_type: str
    total_queries: int
    slow_queries: int
    max_time_ms: float
    avg_time_ms: float
    created_at: str


@router.post("/upload")
async def upload_log(
    connection_id: str = Form(...),
    log_type: str = Form("interf"),
    file: UploadFile = File(...)
):
    """上传网关日志并进行深度分析"""
    try:
        content = await file.read()
        res = gateway_log_service.analyze_log(
            connection_id=connection_id,
            file_name=file.filename,
            file_content=content,
            log_type=log_type
        )
        return {
            "status": "success",
            "report_id": res["id"],
            "total_queries": res["total_queries"],
            "slow_queries": res["slow_queries"],
            "max_time_ms": res["max_time_ms"],
            "avg_time_ms": res["avg_time_ms"]
        }
    except ValueError as e:
        # v1.6.2.2-UAT-O-11：零有效记录等业务输入错误返回 422（可读的失败语义），
        # 不得落入 500 让人误以为是系统故障，也不得返回 200 冒充成功
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports", response_model=List[ReportItem])
def get_reports(connection_id: Optional[str] = None):
    """获取历史网关日志分析列表"""
    try:
        return gateway_log_service.get_reports(connection_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports/{report_id}")
def get_report_detail(report_id: int):
    """获取特定报告的详细数据"""
    try:
        res = gateway_log_service.get_report_detail(report_id)
        if not res:
            raise HTTPException(status_code=404, detail="报告不存在")
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports/{report_id}/html", response_class=HTMLResponse)
def get_report_html(report_id: int):
    """获取特定报告的 HTML 内容进行页面渲染

    v1.6.2.2-UAT-O-10：作为独立文档响应返回（前端 iframe src 加载），
    携带报告专用 CSP（允许内联脚本）与 SAMEORIGIN 嵌入许可，
    覆盖全局 DENY/none 基线（middleware 用 setdefault，此处显式覆盖）。
    """
    try:
        res = gateway_log_service.get_report_detail(report_id)
        if not res or not res.get("report_html"):
            raise HTTPException(status_code=404, detail="报告或HTML内容不存在")
        return HTMLResponse(content=res["report_html"], headers=_REPORT_DOC_HEADERS)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
