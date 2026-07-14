"""M3 · G4 每日巡检 + 趋势 API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from backend.services import daily_inspect_service as svc
from backend.services.connection_registry import registry, ConnectionNotFoundError

router = APIRouter(prefix="/api/v1/daily-inspect", tags=["每日巡检与趋势"])


class DailyRequest(BaseModel):
    connection_id: str = Field("", description="目标连接ID")
    inspect_date: str = Field("", description="巡检日期 YYYY-MM-DD；空则今天")
    nodes: Optional[list] = Field(None, description="指定监控对象；空则自动发现")


@router.post("/run", summary="采集当日巡检指标")
async def run(body: DailyRequest):
    try:
        pool = registry.get(body.connection_id)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=400, detail="未连接TDSQL实例或连接不存在")
    probe = pool.monitor_probe()
    if not probe["ok"]:
        raise HTTPException(status_code=400, detail=f"monitordb不可用: {probe['error']}")
    try:
        return svc.run_daily(pool, connection_id=body.connection_id,
                             inspect_date=body.inspect_date, nodes=body.nodes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trend", summary="多日趋势")
async def trend(connection_id: str = "", date_from: str = "", date_to: str = "",
                metrics: str = ""):
    mlist = [m.strip() for m in metrics.split(",") if m.strip()] if metrics else None
    return svc.get_trend(connection_id, date_from, date_to, mlist)
