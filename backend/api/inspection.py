"""
TDSQL SQL审核工具 - 巡检API (V1.0)
"""
from fastapi import APIRouter, HTTPException, Request
from typing import Optional

from backend.models import InspectionResultInfo, ApiResponse, SchemaCheckRequest
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


@router.post("/schema-check", response_model=ApiResponse)
async def run_schema_check(request: SchemaCheckRequest, http_request: Request):
    """执行数据库上线前Schema检查（12项）

    对目标实例的全部非系统库执行上线前检查：
    字符集/排序规则、表名规范、索引数量、主键、字段长度、
    注释完整性、字段数量、timestamp类型等12项检查。

    替代原有的 tdsql_12.sh 脚本，实现Web界面一键检查。
    """
    from backend.services.connection_registry import registry
    from backend.engine.schema_inspector import SchemaInspector

    # 获取连接池
    pool = registry.get(request.connection_id)
    if not pool:
        raise HTTPException(status_code=400, detail="未找到指定实例连接，请先在实例管理中连接实例")

    # 创建巡检任务
    task_id = _service.create_task(request.connection_id, "schema_check")
    _service.update_task_status(task_id, "running")

    try:
        inspector = SchemaInspector()
        results = inspector.inspect(pool, request.database_filter)
        summary = inspector.get_summary(results)

        # 将结果保存到数据库
        for check_result in results:
            if check_result["count"] > 0:
                for row in check_result["rows"][:100]:  # 每项最多保存100条明细
                    msg_parts = []
                    for k, v in row.items():
                        msg_parts.append(f"{k}: {v}")
                    _service.save_result(task_id, InspectionResultInfo(
                        category=check_result["id"],
                        severity=check_result["severity"],
                        schema_name=str(row.get("数据库", "")),
                        table_name=str(row.get("表名", "")),
                        metric_name=check_result["name"],
                        metric_value=str(check_result["count"]),
                        message=" | ".join(msg_parts),
                        suggestion=check_result["suggestion"],
                    ))

        _service.update_task_status(task_id, "completed")
        return ApiResponse(data={
            "task_id": task_id,
            "summary": summary,
            "results": results,
        })
    except Exception as e:
        _service.update_task_status(task_id, "failed", str(e))
        raise HTTPException(status_code=500, detail=f"Schema检查执行失败: {e}")
