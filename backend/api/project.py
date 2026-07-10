"""
TDSQL SQL审核工具 - 项目管理API (V1.0)
"""
from fastapi import APIRouter, HTTPException

from backend.models import ProjectCreate, ApiResponse
from backend.services.project_service import ProjectService

router = APIRouter(prefix="/api/v1/projects", tags=["项目管理"])
_service = ProjectService()


@router.post("", response_model=ApiResponse)
async def create_project(req: ProjectCreate):
    """创建项目"""
    project = _service.create_project(req)
    data = project.model_dump()
    data["id"] = data.get("project_id")  # project_id 即唯一标识
    return ApiResponse(data=data)


@router.get("", response_model=ApiResponse)
async def list_projects():
    """列出所有项目"""
    projects = _service.list_projects()
    return ApiResponse(data=[{**p.model_dump(), "id": p.project_id} for p in projects])


@router.get("/{project_id}", response_model=ApiResponse)
async def get_project(project_id: str):
    """获取项目详情"""
    project = _service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    data = project.model_dump()
    data["id"] = data.get("project_id")
    return ApiResponse(data=data)


@router.delete("/{project_id}", response_model=ApiResponse)
async def delete_project(project_id: str):
    """真正删除项目（物理删除）"""
    if not _service.delete_project(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    return ApiResponse(message="项目已删除")


@router.put("/{project_id}/toggle-status", response_model=ApiResponse)
async def toggle_project_status(project_id: str):
    """切换项目状态（启用 ↔ 停用）"""
    new_status = _service.toggle_project_status(project_id)
    if new_status is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    label = "已启用" if new_status == "active" else "已停用"
    return ApiResponse(message=f"项目{label}", data={"status": new_status})
