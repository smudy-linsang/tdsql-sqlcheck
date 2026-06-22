"""
TDSQL SQL审核工具 - 巡检API (V1.0)
"""
from fastapi import APIRouter, HTTPException
from typing import Optional

from backend.models import InspectionResultInfo, ApiResponse
from backend.services.inspection_service import InspectionService

router = APIRouter(prefix="/api/v1/inspection", tags=["巡检管理"])
_service = InspectionService()


@router.post("/tasks", response_model=ApiResponse)
async def create_task(connection_id: str, inspection_type: str):
    """创建巡检任务"""
    task_id = _service.create_task(connection_id, inspection_type)
    return ApiResponse(data={"task_id": task_id})


@router.get("/tasks", response_model=ApiResponse)
async def list_tasks(connection_id: str = "", limit: int = 20):
    """列出巡检任务"""
    tasks = _service.list_tasks(connection_id, limit)
    return ApiResponse(data=tasks)


@router.get("/tasks/{task_id}", response_model=ApiResponse)
async def get_task(task_id: int):
    """获取巡检任务详情"""
    task = _service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="巡检任务不存在")
    return ApiResponse(data=task)


@router.post("/tasks/{task_id}/status", response_model=ApiResponse)
async def update_task_status(task_id: int, status: str, error_message: str = ""):
    """更新巡检任务状态"""
    _service.update_task_status(task_id, status, error_message)
    return ApiResponse(message="任务状态已更新")


@router.post("/tasks/{task_id}/results", response_model=ApiResponse)
async def save_result(task_id: int, result: InspectionResultInfo):
    """保存巡检结果"""
    _service.save_result(task_id, result)
    return ApiResponse(message="巡检结果已保存")
