"""
TDSQL SQL审核工具 - 大表治理API (V1.0)
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from backend.models import ApiResponse
from backend.services.bigtable_service import BigTableService

router = APIRouter(prefix="/api/v1/bigtable", tags=["大表治理"])
_service = BigTableService()


@router.get("/inventory/{connection_id}", response_model=ApiResponse)
def get_inventory(connection_id: str, level: str = ""):
    """获取大表清单"""
    items = _service.get_inventory(connection_id, level)
    return ApiResponse(data=items)


@router.post("/inventory/{connection_id}", response_model=ApiResponse)
def save_inventory(connection_id: str, tables_info: list[dict]):
    """保存大表盘点结果"""
    report = _service.save_inventory(connection_id, tables_info)
    return ApiResponse(data=report)


@router.get("/report/{connection_id}", response_model=ApiResponse)
def get_governance_report(connection_id: str):
    """获取大表治理报告"""
    report = _service.get_governance_report(connection_id)
    return ApiResponse(data=report)


@router.get("/classify/{table_name}", response_model=ApiResponse)
def classify_table(table_name: str):
    """分类表类型"""
    classification = _service.classify_table(table_name)
    return ApiResponse(data=classification.model_dump())


@router.post("/classification/{connection_id}", response_model=ApiResponse)
def save_classification(connection_id: str, schema: str, table: str,
                               table_type: str, retention_days: int = 0):
    """保存表分类"""
    _service.save_classification(connection_id, schema, table, table_type, retention_days)
    return ApiResponse(message="分类已保存")
