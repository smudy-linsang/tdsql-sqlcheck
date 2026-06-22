"""
TDSQL SQL审核工具 - 监控告警API (V1.0)
"""
from fastapi import APIRouter, HTTPException
from typing import Optional

from backend.models import AlertInfo, AlertRuleConfig, ApiResponse
from backend.services.monitor_service import MonitorService

router = APIRouter(prefix="/api/v1/monitor", tags=["监控告警"])
_service = MonitorService()


@router.get("/alerts", response_model=ApiResponse)
async def get_active_alerts(connection_id: str = ""):
    """获取活跃告警"""
    alerts = _service.get_active_alerts(connection_id)
    return ApiResponse(data=alerts)


@router.post("/alerts/{alert_id}/acknowledge", response_model=ApiResponse)
async def acknowledge_alert(alert_id: int, acknowledged_by: str = "system"):
    """确认告警"""
    if not _service.acknowledge_alert(alert_id, acknowledged_by):
        raise HTTPException(status_code=404, detail="告警不存在")
    return ApiResponse(message="告警已确认")


@router.get("/rules", response_model=ApiResponse)
async def get_alert_rules():
    """获取告警规则"""
    rules = _service.get_alert_rules()
    return ApiResponse(data=rules)


@router.post("/rules", response_model=ApiResponse)
async def set_alert_rule(rule: AlertRuleConfig):
    """设置告警规则"""
    _service.set_alert_rule(rule)
    return ApiResponse(message="告警规则已更新")


@router.post("/evaluate", response_model=ApiResponse)
async def evaluate_metric(connection_id: str, metric_name: str, value: float):
    """评估指标是否触发告警"""
    alert = _service.evaluate_metric(connection_id, metric_name, value)
    if alert:
        return ApiResponse(data=alert.model_dump())
    return ApiResponse(message="指标正常，未触发告警")
