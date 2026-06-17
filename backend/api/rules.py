"""
TDSQL SQL审核工具 - 规则管理API

提供规则列表查询接口，动态获取系统中所有审核规则。
"""
from typing import Optional

from fastapi import APIRouter

from backend.engine.checker import RuleChecker

router = APIRouter(prefix="/api/v1/rules", tags=["规则管理"])


@router.get("")
async def list_rules() -> dict:
    """
    获取所有审核规则列表。
    
    返回系统中所有22条审核规则的详细信息，包括规则ID、类别、严重级别、描述等。
    新增规则后此接口会自动同步更新。
    """
    checker = RuleChecker()
    rules_info = checker.get_rules_info()
    return {
        "total": len(rules_info),
        "rules": rules_info,
    }


@router.get("/categories")
async def list_categories() -> dict:
    """获取规则分类统计"""
    checker = RuleChecker()
    categories = checker.get_rules_by_category()
    return {
        "categories": categories,
    }
