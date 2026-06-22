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
    return ApiResponse(data=project.model_dump())


@router.get("", response_model=ApiResponse)
async def list_projects():
    """列出所有项目"""
    projects = _service.list_projects()
    return ApiResponse(data=[p.model_dump() for p in projects])


@router.get("/{project_id}", response_model=ApiResponse)
async def get_project(project_id: str):
    """获取项目详情"""
    project = _service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return ApiResponse(data=project.model_dump())


@router.delete("/{project_id}", response_model=ApiResponse)
async def delete_project(project_id: str):
    """删除项目（标记为inactive）"""
    if not _service.delete_project(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    return ApiResponse(message="项目已删除")
