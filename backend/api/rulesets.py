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


def _role(request: Request) -> str:
    return getattr(request.state, "role", "")


def _raise_err(err: dict):
    """将 service 返回的 {message,code,status} 映射为 HTTP 错误（遵循平台 {detail,code} 约定）"""
    raise HTTPException(status_code=err["status"],
                        detail={"detail": err["message"], "code": err["code"]})


@router.get("/active", summary="查询当前生效规则集")
def get_active_ruleset():
    """返回全局生效规则集详情；兜底链保证恒有结果（《ARCHITECTURE-v1.4》§3.2）。"""
    return ruleset_service.get_active_detail()


@router.post("/{rule_set_id}/activate", summary="切换全局生效规则集（admin）")
def activate_ruleset(rule_set_id: str, request: Request):
    """切换全局生效规则集。改变全系统评估尺度，属高影响操作，admin 独占。

    不提供“停用”接口：系统必须始终有一个生效规则集（INV-2）。
    """
    if _role(request) != "admin":
        raise HTTPException(status_code=403,
                            detail={"detail": "仅系统管理员可切换全局生效规则集", "code": "E403"})
    err = ruleset_service.set_active_rule_set(rule_set_id, operator=_operator(request))
    if err:
        _raise_err(err)
    detail = ruleset_service.get_active_detail()
    return {
        "status": "SUCCESS",
        "rule_set_id": rule_set_id,
        "name": detail.get("name", rule_set_id),
        "effective_within_seconds": detail.get("cache_ttl_seconds", 30),
        "message": "已切换全局生效规则集，最长 30 秒内全量生效",
    }


@router.get("", summary="规则集列表")
def list_rulesets():
    rulesets = ruleset_service.list_rulesets()
    return {
        "rulesets": rulesets,
        "active_rule_set_id": ruleset_service.get_active_rule_set_id(),
    }


@router.get("/{rule_set_id}", summary="规则集详情")
def get_ruleset(rule_set_id: str):
    result = ruleset_service.get_ruleset(rule_set_id)
    if not result:
        raise HTTPException(status_code=404, detail="规则集不存在")
    # V1.5：补充按实例类型的实跑条数（规则集页面显示"启用N条（分布式119/集中式92）"）
    from backend.engine.checker import RuleChecker
    checker = RuleChecker()
    overrides = {it["rule_id"]: {"enabled": bool(it.get("enabled", True)),
                                  "severity_override": it.get("severity_override")}
                 for it in result.get("items", [])}
    result["effective_counts"] = {
        "distributed": len(checker.get_enabled_rules(overrides, "distributed")),
        "centralized": len(checker.get_enabled_rules(overrides, "centralized")),
    }
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
        _raise_err(err)
    return {"message": "规则集已删除"}
