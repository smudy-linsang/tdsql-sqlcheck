"""G12 运维汇报 PPT 与大屏 API 路由"""
from fastapi import APIRouter, HTTPException, Response
from backend.services.ppt_report_service import ppt_report_service

router = APIRouter(prefix="/api/v1/ppt-report", tags=["PPT Report"])


@router.post("/generate")
@router.get("/generate")
def generate_pdf(connection_id: str):
    """一键生成并下载运维汇报 PDF"""
    try:
        pdf_bytes = ppt_report_service.generate_pdf(connection_id)
        
        filename = f"tdsql_ops_report_{connection_id}_{datetime_str()}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard")
def get_dashboard_data(connection_id: str):
    """获取大屏总览指标与汇总统计数据"""
    try:
        return ppt_report_service.get_dashboard_data(connection_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def datetime_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")
