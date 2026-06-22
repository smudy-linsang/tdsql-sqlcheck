"""
TDSQL SQL审核工具 - 质量门禁API (V1.0)
"""
from fastapi import APIRouter, HTTPException
from typing import Optional

from backend.models import GateRule, ApiResponse
from backend.services.gate_service import GateService

router = APIRouter(prefix="/api/v1/gate", tags=["质量门禁"])
_service = GateService()


@router.get("/rules/{project_id}", response_model=ApiResponse)
async def get_gate_rule(project_id: str = "default"):
    """获取门禁规则"""
    rule = _service.get_gate_rule(project_id)
    return ApiResponse(data=rule.model_dump())


@router.post("/rules", response_model=ApiResponse)
async def set_gate_rule(rule: GateRule):
    """设置门禁规则"""
    _service.set_gate_rule(rule)
    return ApiResponse(message="门禁规则已更新")


@router.post("/strategy/{project_id}", response_model=ApiResponse)
async def apply_strategy(project_id: str, strategy: str):
    """应用预设门禁策略(strict/normal/loose)"""
    if not _service.apply_strategy(project_id, strategy):
        raise HTTPException(status_code=400, detail=f"未知策略: {strategy}")
    return ApiResponse(message=f"门禁策略已设置为: {strategy}")


@router.get("/strategies", response_model=ApiResponse)
async def list_strategies():
    """列出可用门禁策略"""
    from backend.services.gate_service import GATE_STRATEGIES
    return ApiResponse(data=GATE_STRATEGIES)
