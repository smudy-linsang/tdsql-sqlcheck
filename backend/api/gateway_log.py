"""G11 网关日志分析 API 路由"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from typing import List, Optional
from pydantic import BaseModel
from backend.services.gateway_log_service import gateway_log_service

router = APIRouter(prefix="/api/v1/gateway-log", tags=["Gateway Log"])

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
    """获取特定报告的 HTML 内容进行页面渲染"""
    try:
        res = gateway_log_service.get_report_detail(report_id)
        if not res or not res.get("report_html"):
            raise HTTPException(status_code=404, detail="报告或HTML内容不存在")
        return res["report_html"]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
