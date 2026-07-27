"""
TDSQL SQL审核工具 - 大表治理API (V1.0)
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from backend.models import ApiResponse
from backend.services.bigtable_service import BigTableService

router = APIRouter(prefix="/api/v1/bigtable", tags=["大表治理"])
_service = BigTableService()


def _assert_connection_exists(connection_id: str):
    """实例不存在时返回 404。

    此前对不存在的实例返回 code:0 + 全 0 空报告，调用方无法区分
    "实例不存在"与"实例无大表"，巡检脚本会把配置错误当成健康结果漏报。
    """
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM tdsql_connections WHERE id = ?", (connection_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"数据库实例不存在: {connection_id}")


@router.get("/inventory/{connection_id}", response_model=ApiResponse)
def get_inventory(connection_id: str, level: str = "", inspection_date: str = ""):
    """获取大表清单

    V1.3(D2): inspection_date 为空时返回最近一次盘点结果（此前返回所有历史日期，
    导致同一张表出现多行、清单虚高）。传入具体日期可查看历史批次。
    """
    _assert_connection_exists(connection_id)
    items = _service.get_inventory(connection_id, level, inspection_date)
    return ApiResponse(data=items)


@router.post("/inventory/{connection_id}", response_model=ApiResponse)
def save_inventory(connection_id: str, tables_info: list[dict]):
    """保存大表盘点结果"""
    _assert_connection_exists(connection_id)
    report = _service.save_inventory(connection_id, tables_info)
    return ApiResponse(data=report)


@router.get("/report/{connection_id}", response_model=ApiResponse)
def get_governance_report(connection_id: str):
    """获取大表治理报告"""
    _assert_connection_exists(connection_id)
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
    _assert_connection_exists(connection_id)
    _service.save_classification(connection_id, schema, table, table_type, retention_days)
    return ApiResponse(message="分类已保存")
