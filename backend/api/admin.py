"""
TDSQL SQL审核工具 - 系统管理 API (V3.0)

数据保留策略、操作审计日志查询、系统信息、Logo上传、系统配置。
写操作需要 dba/admin（中间件RBAC强制）；操作日志仅审计相关角色可读。
"""
import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel, Field

from backend import config
from backend.services.database import _get_connection, ensure_db
from backend.services.retention_service import retention_service

router = APIRouter(prefix="/api/v1/admin", tags=["系统管理"])

# Logo存储路径
_LOGO_DIR = Path(__file__).parent.parent.parent / "frontend" / "static" / "img"
_LOGO_PATH = _LOGO_DIR / "custom-logo.png"


class RetentionPolicyRequest(BaseModel):
    table_name: str = Field(..., description="表名")
    retention_days: int = Field(..., ge=7, description="保留天数(≥7)")
    enabled: bool = Field(True, description="是否启用")


class SystemConfigRequest(BaseModel):
    auth_enabled: Optional[bool] = None
    data_masking_enabled: Optional[bool] = None
    metrics_enabled: Optional[bool] = None


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


# ═══ 系统信息 + 配置 ═══

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


@router.get("/config", summary="获取系统配置")
async def get_system_config():
    """获取可配置的系统开关"""
    ensure_db()
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT config_key, config_value FROM system_config").fetchall()
        cfg = {r["config_key"]: r["config_value"] for r in rows}
        return {
            "auth_enabled": cfg.get("auth_enabled", str(config.auth_enabled()).lower()) == "true",
            "data_masking_enabled": cfg.get("data_masking_enabled", str(config.data_masking_enabled()).lower()) == "true",
            "metrics_enabled": cfg.get("metrics_enabled", str(config.metrics_enabled()).lower()) == "true",
        }
    finally:
        conn.close()


@router.put("/config", summary="更新系统配置")
async def set_system_config(body: SystemConfigRequest, request: Request):
    """更新系统开关配置"""
    ensure_db()
    conn = _get_connection()
    try:
        if body.auth_enabled is not None:
            conn.execute(
                "REPLACE INTO system_config(config_key, config_value) VALUES('auth_enabled', ?)",
                (str(body.auth_enabled).lower(),))
        if body.data_masking_enabled is not None:
            conn.execute(
                "REPLACE INTO system_config(config_key, config_value) VALUES('data_masking_enabled', ?)",
                (str(body.data_masking_enabled).lower(),))
        if body.metrics_enabled is not None:
            conn.execute(
                "REPLACE INTO system_config(config_key, config_value) VALUES('metrics_enabled', ?)",
                (str(body.metrics_enabled).lower(),))
        conn.commit()
        return {"message": "系统配置已更新"}
    finally:
        conn.close()


# ═══ Logo上传 ═══

@router.get("/logo", summary="获取Logo路径")
async def get_logo():
    """返回当前Logo URL（自定义或默认）"""
    if _LOGO_PATH.exists():
        return {"logo_url": "/static/img/custom-logo.png", "is_custom": True}
    return {"logo_url": "", "is_custom": False}


@router.post("/logo", summary="上传Logo")
async def upload_logo(file: UploadFile = File(...)):
    """上传自定义Logo图片"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名为空")
    allowed = (".png", ".jpg", ".jpeg", ".svg", ".ico")
    if not file.filename.lower().endswith(allowed):
        raise HTTPException(status_code=400, detail=f"仅支持: {', '.join(allowed)}")
    _LOGO_DIR.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小不能超过2MB")
    with open(_LOGO_PATH, "wb") as f:
        f.write(content)
    return {"message": "Logo上传成功", "logo_url": "/static/img/custom-logo.png"}


@router.delete("/logo", summary="恢复默认Logo")
async def reset_logo():
    """删除自定义Logo，恢复默认"""
    if _LOGO_PATH.exists():
        _LOGO_PATH.unlink()
    return {"message": "已恢复默认Logo"}


# ═══ 数据保留 ═══

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


# ═══ 操作审计日志 ═══

@router.get("/operation-logs", summary="查询操作审计日志")
async def get_operation_logs(
    operator: Optional[str] = None,
    operation_type: Optional[str] = None,
    target_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 50, offset: int = 0,
):
    """操作审计日志查询（支持操作人、操作类型、目标类型、时间范围筛选）"""
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
        if target_type:
            conditions.append("target_type = ?")
            params.append(target_type)
        if start_date:
            conditions.append("created_at >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("created_at <= ?")
            params.append(end_date + " 23:59:59" if len(end_date) == 10 else end_date)
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
