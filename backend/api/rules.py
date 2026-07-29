"""
TDSQL SQL审核工具 - 规则管理API

提供规则列表查询接口，动态获取系统中所有审核规则。
"""
from typing import Optional

from fastapi import APIRouter

from backend.engine.checker import RuleChecker

router = APIRouter(prefix="/api/v1/rules", tags=["规则管理"])


@router.get("")
def list_rules(instance_type: Optional[str] = None) -> dict:
    """
    获取所有审核规则列表。

    返回系统中所有审核规则的详细信息（动态计数，当前119条）。
    V1.5：instance_scope 无条件返回（规则固有属性）；传 instance_type 时
    额外返回 effective_total/skipped_total 与逐条 effective 标记（按实例类型口径）。
    """
    checker = RuleChecker()
    rules_info = checker.get_rules_info()
    resp = {
        "total": len(rules_info),
        "rules": rules_info,
    }
    if instance_type in ("distributed", "centralized"):
        effective_total = len(checker.get_enabled_rules(None, instance_type))
        resp["instance_type"] = instance_type
        resp["effective_total"] = effective_total
        resp["skipped_total"] = checker.count_skipped_by_scope(instance_type)
        for r in rules_info:
            scope = r.get("instance_scope", "all")
            r["effective"] = (scope == "all" or scope == instance_type)
    return resp


@router.get("/categories")
def list_categories(instance_type: Optional[str] = None) -> dict:
    """获取规则分类统计（V1.5：传 instance_type 时只统计该口径下生效的规则）"""
    checker = RuleChecker()
    if instance_type in ("distributed", "centralized"):
        categories: dict = {}
        for r in checker.get_enabled_rules(None, instance_type):
            cat = r.category.value if hasattr(r.category, "value") else str(r.category)
            categories.setdefault(cat, []).append({
                "rule_id": r.rule_id,
                "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
                "description": r.description,
            })
        return {"categories": categories, "instance_type": instance_type}
    return {
        "categories": checker.get_rules_by_category(),
    }
