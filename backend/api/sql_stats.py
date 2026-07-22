"""M4 · G8 SQL调用量分析 + G9 大表增长趋势 API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.services import sql_stats_service, bigtable_trend_service
from backend.services.connection_registry import registry, ConnectionNotFoundError

router = APIRouter(prefix="/api/v1/sql-stats", tags=["SQL调用量分析与大表趋势"])


def _pool(cid):
    try:
        return registry.get(cid)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=400, detail="未连接TDSQL实例或连接不存在")


class StatsRequest(BaseModel):
    connection_id: str = Field("", description="目标连接ID")
    time_start: str = Field("", description="时间窗开始")
    time_end: str = Field("", description="时间窗结束")
    top_n: int = Field(20, description="TOP N")
    database: str = Field("", description="仅统计指定库")


@router.post("/analyze", summary="SQL调用量多维分析(基于monitordb)")
def analyze(body: StatsRequest):
    pool = _pool(body.connection_id)
    if not pool.monitor_probe()["ok"]:
        raise HTTPException(status_code=400, detail="monitordb不可用")
    try:
        return sql_stats_service.analyze(
            pool, time_start=body.time_start or None, time_end=body.time_end or None,
            top_n=body.top_n, database=body.database or None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SnapRequest(BaseModel):
    connection_id: str = Field("", description="目标连接ID")
    database: str = Field("", description="仅快照指定库")
    threshold_gb: float = Field(1.0, description="大表阈值(GB)")
    snap_date: str = Field("", description="快照日期；空则今天")


@router.post("/bigtable/snapshot", summary="大表大小快照(增长趋势用)")
def bigtable_snapshot(body: SnapRequest):
    pool = _pool(body.connection_id)
    try:
        return bigtable_trend_service.snapshot(
            pool, connection_id=body.connection_id, database=body.database,
            threshold_gb=body.threshold_gb, snap_date=body.snap_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bigtable/growth", summary="大表增长趋势")
def bigtable_growth(connection_id: str = "", db_name: str = "", table_name: str = "",
                          date_from: str = "", date_to: str = ""):
    return bigtable_trend_service.get_growth(connection_id, db_name, table_name, date_from, date_to)
