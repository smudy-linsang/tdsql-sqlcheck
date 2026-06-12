"""
TDSQL SQL审核工具 - 慢SQL API

提供慢SQL的查询、分析和管理接口。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.engine.slow_analyzer import SlowQueryRecord
from backend.services.slow_query_service import SlowQueryService

router = APIRouter(prefix="/api/v1/slow-queries", tags=["慢SQL分析"])
service = SlowQueryService()


# ============ 请求/响应模型 ============

class SlowQueryCreateRequest(BaseModel):
    """创建慢SQL记录请求"""
    fingerprint: str = Field(..., description="SQL指纹（去除参数值后的SQL模板）")
    sql_text: str = Field(..., description="原始SQL文本")
    db_name: str = Field("", description="数据库名")
    exec_count: int = Field(0, description="执行次数")
    total_time_ms: float = Field(0, description="总耗时(ms)")
    avg_time_ms: float = Field(0, description="平均耗时(ms)")
    max_time_ms: float = Field(0, description="最大耗时(ms)")
    rows_examined: int = Field(0, description="扫描行数")
    rows_sent: int = Field(0, description="返回行数")
    lock_time_ms: float = Field(0, description="锁等待时间(ms)")


class ExplainRowRequest(BaseModel):
    """EXPLAIN行数据"""
    id: int = Field(0)
    select_type: str = Field("")
    table: str = Field("")
    type: str = Field("")
    possible_keys: Optional[str] = Field("")
    key: Optional[str] = Field("")
    key_len: Optional[str] = Field("")
    ref: Optional[str] = Field("")
    rows: int = Field(0)
    filtered: float = Field(100.0)
    extra: str = Field("")


class ExplainAnalyzeRequest(BaseModel):
    """EXPLAIN分析请求"""
    explain_data: list[ExplainRowRequest] = Field(..., description="EXPLAIN输出数据")


class StatusUpdateRequest(BaseModel):
    """状态更新请求"""
    status: str = Field(..., description="新状态: pending/optimized/ignored")


# ============ API路由 ============

@router.post("", summary="添加慢SQL记录并自动分析")
async def create_slow_query(request: SlowQueryCreateRequest):
    """
    添加一条慢SQL记录，系统会自动进行分析并给出优化建议。
    """
    record = SlowQueryRecord(
        fingerprint=request.fingerprint,
        sql_text=request.sql_text,
        db_name=request.db_name,
        exec_count=request.exec_count,
        total_time_ms=request.total_time_ms,
        avg_time_ms=request.avg_time_ms,
        max_time_ms=request.max_time_ms,
        rows_examined=request.rows_examined,
        rows_sent=request.rows_sent,
        lock_time_ms=request.lock_time_ms,
    )
    result = service.add_slow_query(record)
    return result


@router.get("", summary="获取慢SQL列表")
async def list_slow_queries(
    db_name: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """
    获取慢SQL列表，支持按数据库名、状态、严重程度筛选。
    """
    return service.get_slow_queries(
        db_name=db_name,
        status=status,
        severity=severity,
        limit=limit,
        offset=offset,
    )


@router.get("/statistics", summary="获取慢SQL统计信息")
async def get_statistics():
    """
    获取慢SQL的统计概览，包括Top高耗时和高频次SQL。
    """
    return service.get_statistics()


@router.get("/{slow_id}", summary="获取慢SQL详情")
async def get_slow_query_detail(slow_id: int):
    """获取指定慢SQL的详细分析结果"""
    detail = service.get_slow_query_detail(slow_id)
    if not detail:
        raise HTTPException(status_code=404, detail="慢SQL记录不存在")
    return detail


@router.put("/{slow_id}/status", summary="更新慢SQL状态")
async def update_status(slow_id: int, request: StatusUpdateRequest):
    """更新慢SQL的处理状态"""
    if request.status not in ("pending", "optimized", "ignored"):
        raise HTTPException(status_code=400, detail="状态值无效，可选: pending/optimized/ignored")
    success = service.update_status(slow_id, request.status)
    if not success:
        raise HTTPException(status_code=404, detail="慢SQL记录不存在")
    return {"message": "状态更新成功", "status": request.status}


@router.post("/analyze-explain", summary="分析EXPLAIN执行计划")
async def analyze_explain(request: ExplainAnalyzeRequest):
    """
    传入EXPLAIN输出数据，自动分析执行计划并给出优化建议。

    使用方式：
    1. 在MySQL中执行 EXPLAIN SELECT ... 
    2. 将结果填入explain_data数组
    3. 调用此接口获取分析报告
    """
    explain_data = [row.model_dump() for row in request.explain_data]
    return service.analyze_explain(explain_data)
