"""
TDSQL SQL审核工具 - 系统管理 API (V2.0)

数据保留策略、操作审计日志查询、系统信息。
写操作需要 dba/admin（中间件RBAC强制）；操作日志仅审计相关角色可读。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend import config
from backend.services.database import _get_connection, ensure_db
from backend.services.retention_service import retention_service

router = APIRouter(prefix="/api/v1/admin", tags=["系统管理"])


class RetentionPolicyRequest(BaseModel):
    table_name: str = Field(..., description="表名")
    retention_days: int = Field(..., ge=7, description="保留天数(≥7)")
    enabled: bool = Field(True, description="是否启用")


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


@router.get("/info", summary="系统信息")
async def system_info():
    """系统版本与关键安全配置状态（不暴露敏感值）"""
    return {
        "version": config.APP_VERSION,
        "auth_enabled": config.auth_enabled(),
        "data_masking_enabled": config.data_masking_enabled(),
        "metrics_enabled": config.metrics_enabled(),
        "scan_limits": {
            "per_connection": config.max_concurrent_scans_per_connection(),
            "global": config.max_concurrent_scans_global(),
        },
        "connection_pool": {
            "max_instances": config.connection_pool_max_instances(),
            "idle_seconds": config.connection_pool_idle_seconds(),
        },
    }


@router.get("/retention", summary="获取数据保留策略")
async def get_retention_policies():
    return {"policies": retention_service.get_policies()}


@router.put("/retention", summary="设置数据保留策略")
async def set_retention_policy(body: RetentionPolicyRequest, request: Request):
    err = retention_service.set_policy(
        body.table_name, body.retention_days, body.enabled,
        operator=_operator(request))
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"message": "保留策略已更新"}


@router.post("/retention/run", summary="手动执行数据清理")
async def run_retention_cleanup(request: Request):
    deleted = retention_service.run_cleanup(operator=_operator(request))
    return {"message": "数据清理完成", "deleted": deleted,
            "total": sum(deleted.values())}


@router.get("/operation-logs", summary="查询操作审计日志")
async def get_operation_logs(operator: Optional[str] = None,
                             operation_type: Optional[str] = None,
                             limit: int = 50, offset: int = 0):
    """操作审计日志查询（谁、何时、做了什么）"""
    limit = min(max(limit, 1), 500)
    ensure_db()
    conn = _get_connection()
    try:
        conditions, params = [], []
        if operator:
            conditions.append("operator = ?")
            params.append(operator)
        if operation_type:
            conditions.append("operation_type LIKE ?")
            params.append(f"%{operation_type}%")
        where = " AND ".join(conditions) if conditions else "1=1"
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM operation_logs WHERE {where}",
            params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM operation_logs WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset]).fetchall()
        return {"total": total, "logs": [dict(r) for r in rows]}
    finally:
        conn.close()
