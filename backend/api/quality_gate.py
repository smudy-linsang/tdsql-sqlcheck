"""
TDSQL SQL审核工具 - 质量门禁API

V1.4：门禁绑定对象由「项目」改为「实例」。新增 /gate/instances 系列端点；
旧的 /gate/rules、/gate/strategy 进入兼容期（可调用但不再影响实际判定）。
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from backend.models import GateRule, ApiResponse
from backend.services.gate_service import GateService
from backend.services.instance_gate_service import instance_gate_service

router = APIRouter(prefix="/api/v1/gate", tags=["质量门禁"])
_service = GateService()


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


def _require_admin(request: Request):
    """门禁阈值是治理动作，必须 admin 独占（实例配置本身 dba 可编辑）。"""
    if getattr(request.state, "role", "") != "admin":
        raise HTTPException(status_code=403,
                            detail={"detail": "仅系统管理员可配置实例门禁", "code": "E403"})


def _raise_err(err: dict):
    raise HTTPException(status_code=err["status"],
                        detail={"detail": err["message"], "code": err["code"]})


# ── V1.4 实例级质量门禁 ──

class InstanceGateRequest(BaseModel):
    max_error_count: int = Field(0, description="ERROR 上限；-1=不限，其余须>=0")
    max_warning_count: int = Field(-1, description="WARNING 上限；-1=不限（默认），其余须>=0")
    mode: str = Field("enforce", description="判定模式：enforce / observe")
    description: str = Field("", description="备注")


@router.get("/instances", summary="实例门禁配置列表")
def list_instance_gates():
    """返回全部实例及其门禁配置；未配置实例返回系统默认并标记 is_default。"""
    return instance_gate_service.list_rules()


@router.get("/instances/{connection_id}", summary="查询单个实例门禁")
def get_instance_gate(connection_id: str):
    rule = instance_gate_service.get_rule(connection_id)
    # 实例不存在时 get_rule 返回默认值；此处严格校验存在性
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    conn = _get_connection()
    try:
        exists = conn.execute(
            "SELECT id FROM tdsql_connections WHERE id = ?", (connection_id,)).fetchone()
    finally:
        conn.close()
    if not exists:
        _raise_err({"message": f"实例不存在: {connection_id}", "code": "E5012", "status": 404})
    return rule


@router.put("/instances/{connection_id}", summary="保存实例门禁（admin）")
def save_instance_gate(connection_id: str, body: InstanceGateRequest, request: Request):
    """保存实例门禁阈值。由实例表单在实例保存成功后单独发起，不混入实例保存报文。"""
    _require_admin(request)
    err = instance_gate_service.save_rule(
        connection_id=connection_id,
        max_error_count=body.max_error_count,
        max_warning_count=body.max_warning_count,
        mode=body.mode,
        description=body.description,
        operator=_operator(request))
    if err:
        _raise_err(err)
    return {"status": "SUCCESS", "connection_id": connection_id}


@router.delete("/instances/{connection_id}", summary="删除实例门禁配置（admin）")
def delete_instance_gate(connection_id: str, request: Request):
    """删除后该实例回落系统默认（0 / -1 / enforce）。"""
    _require_admin(request)
    err = instance_gate_service.delete_rule(connection_id, operator=_operator(request))
    if err:
        _raise_err(err)
    return {"status": "SUCCESS", "connection_id": connection_id}


# ── 旧门禁接口（V1.4 DEPRECATED：可调用但不再影响实际判定） ──

@router.get("/rules/{project_id}", response_model=ApiResponse)
def get_gate_rule(project_id: str = "default"):
    """DEPRECATED(V1.4)：门禁已改绑实例，见 /gate/instances。兼容期返回旧数据。"""
    rule = _service.get_gate_rule(project_id)
    return ApiResponse(data=rule.model_dump(),
                       message="DEPRECATED: 门禁已改为绑定实例，本接口仅作兼容保留")


@router.post("/rules", response_model=ApiResponse)
def set_gate_rule(rule: GateRule):
    """DEPRECATED(V1.4)：可写入旧表但不再影响实际判定。"""
    _service.set_gate_rule(rule)
    return ApiResponse(message="DEPRECATED: 写入成功但不再生效，门禁请改用 /gate/instances")


@router.post("/strategy/{project_id}", response_model=ApiResponse)
def apply_strategy(project_id: str, strategy: str):
    """DEPRECATED(V1.4)：可调用但不再影响实际判定。"""
    if not _service.apply_strategy(project_id, strategy):
        raise HTTPException(status_code=400, detail=f"未知策略: {strategy}")
    return ApiResponse(message=f"DEPRECATED: 策略已写入但不再生效（{strategy}）")


@router.get("/strategies", response_model=ApiResponse)
def list_strategies():
    """列出可用门禁策略"""
    from backend.services.gate_service import GATE_STRATEGIES
    return ApiResponse(data=GATE_STRATEGIES)
