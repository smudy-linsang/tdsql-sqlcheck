"""
TDSQL SQL审核工具 - 规则集管理 API (V2.0)

多租户规则集管理：不同项目/团队/环境绑定不同规则集。
写操作需要 dba/admin 角色（中间件RBAC强制）。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.services.ruleset_service import ruleset_service

router = APIRouter(prefix="/api/v1/rulesets", tags=["规则集管理"])


class RuleSetItem(BaseModel):
    rule_id: str = Field(..., description="规则ID，如 R012")
    enabled: bool = Field(True, description="是否启用")
    severity_override: Optional[str] = Field(
        None, description="级别覆盖: ERROR/WARNING/INFO，null=使用默认级别")


class RuleSetCreateRequest(BaseModel):
    id: str = Field(..., min_length=2, max_length=64, description="规则集ID")
    name: str = Field(..., description="规则集名称")
    description: str = Field("", description="描述")
    items: list[RuleSetItem] = Field(default_factory=list, description="规则覆盖条目")


class RuleSetUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    items: Optional[list[RuleSetItem]] = None


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


@router.get("", summary="规则集列表")
def list_rulesets():
    return {"rulesets": ruleset_service.list_rulesets()}


@router.get("/{rule_set_id}", summary="规则集详情")
def get_ruleset(rule_set_id: str):
    result = ruleset_service.get_ruleset(rule_set_id)
    if not result:
        raise HTTPException(status_code=404, detail="规则集不存在")
    return result


@router.post("", summary="创建规则集")
def create_ruleset(body: RuleSetCreateRequest, request: Request):
    result, err = ruleset_service.create_ruleset(
        rule_set_id=body.id, name=body.name, description=body.description,
        items=[i.model_dump() for i in body.items], operator=_operator(request))
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"message": "规则集已创建", "ruleset": result}


@router.put("/{rule_set_id}", summary="更新规则集")
def update_ruleset(rule_set_id: str, body: RuleSetUpdateRequest, request: Request):
    err = ruleset_service.update_ruleset(
        rule_set_id, name=body.name, description=body.description,
        items=[i.model_dump() for i in body.items] if body.items is not None else None,
        operator=_operator(request))
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"message": "规则集已更新"}


@router.delete("/{rule_set_id}", summary="删除规则集")
def delete_ruleset(rule_set_id: str, request: Request):
    err = ruleset_service.delete_ruleset(rule_set_id, operator=_operator(request))
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"message": "规则集已删除"}
