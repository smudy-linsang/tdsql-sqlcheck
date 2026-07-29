"""
TDSQL SQL审核工具 - 系统配置 API（V1.5）

当前承载全局默认实例类型（B 类通道兜底口径）的读写。
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.services.instance_type_service import instance_type_service

router = APIRouter(prefix="/api/v1/config", tags=["系统配置"])


@router.get("/default-instance-type", summary="读取全局默认实例类型")
def get_default_instance_type():
    """登录即可（前端 B 类通道选择器需拿它作为默认选中项）。"""
    return {
        "default_instance_type": instance_type_service.get_default_instance_type().value,
        "options": [
            {"value": "distributed", "label": "分布式实例"},
            {"value": "centralized", "label": "集中式实例"},
        ],
        "description": ("用于无法确定目标实例的审核场景（文件上传、批量流式、GitLab MR、CLI）。"
                        "出厂值为「分布式」，即按全部规则评估，宁可多报不可漏报。"),
    }


class SetDefaultInstanceTypeRequest(BaseModel):
    default_instance_type: str


@router.put("/default-instance-type", summary="设置全局默认实例类型（admin）")
def set_default_instance_type(body: SetDefaultInstanceTypeRequest, request: Request):
    """仅 admin（全局配置，影响所有无实例上下文的审核）。

    双保险：_PATH_TO_MENU 已登记走中间件，此处再显式校验 role==admin。
    """
    if getattr(request.state, "role", "") != "admin":
        raise HTTPException(status_code=403,
                            detail={"detail": "仅系统管理员可修改全局默认实例类型", "code": "E403"})
    if body.default_instance_type not in ("distributed", "centralized"):
        raise HTTPException(status_code=400,
                            detail={"detail": "default_instance_type 仅支持 distributed 或 centralized",
                                    "code": "E5014"})
    instance_type_service.set_default_instance_type(body.default_instance_type)
    cn = "分布式" if body.default_instance_type == "distributed" else "集中式"
    return {
        "success": True,
        "default_instance_type": body.default_instance_type,
        # 生产 --workers 2，配置带 300s 进程内缓存，严禁写"即时生效"
        "message": f"已设置全局默认实例类型为「{cn}」。该配置最长 5 分钟后在全部服务进程生效。",
    }
