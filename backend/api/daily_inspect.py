"""M3 · G4 每日巡检 + 趋势与对比分析 API"""
import asyncio
from fastapi import APIRouter, HTTPException, Response, Query
from pydantic import BaseModel, Field
from typing import Optional, List as TypedList

from backend.services import daily_inspect_service as svc
from backend.services.connection_registry import registry, ConnectionNotFoundError

router = APIRouter(prefix="/api/v1/daily-inspect", tags=["每日巡检与趋势"])


class DailyRequest(BaseModel):
    connection_id: str = Field("", description="目标连接ID")
    inspect_date: str = Field("", description="巡检日期 YYYY-MM-DD；空则今天")
    nodes: Optional[list] = Field(None, description="指定监控对象；空则自动发现")
    time_range: Optional[str] = Field("", description="指定时间段，例如 09:00-18:00")


@router.post("/run", summary="采集当日巡检指标")
async def run(body: DailyRequest):
    try:
        pool = registry.get(body.connection_id)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=400, detail="未连接TDSQL实例或连接不存在")
    probe = pool.monitor_probe()
    if not probe["ok"]:
        # 如果是 Mock 实例，我们不阻断巡检，直接跑 Mock 流程
        if "mock" not in body.connection_id.lower() and "test" not in body.connection_id.lower():
            raise HTTPException(status_code=400, detail=f"monitordb不可用: {probe['error']}")
    try:
        # 使用 asyncio.to_thread 调度至 Worker 线程池，释放 Event Loop
        return await asyncio.to_thread(
            svc.run_daily, pool, connection_id=body.connection_id,
            inspect_date=body.inspect_date, nodes=body.nodes
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/trend", summary="多日趋势")
async def trend(connection_id: str = "", date_from: str = "", date_to: str = "",
                metrics: str = ""):
    mlist = [m.strip() for m in metrics.split(",") if m.strip()] if metrics else None
    return svc.get_trend(connection_id, date_from, date_to, mlist)


@router.get("/compare", summary="巡检差异多维比对 (JSON)")
async def compare(connection_id: str = "",
                  date1: str = "",
                  date2: str = "",
                  dates: Optional[str] = None,
                  threshold_multiplier: float = 1.0):
    if not connection_id:
        raise HTTPException(status_code=400, detail="必须指定 connection_id")

    if dates:
        date_list = [d.strip() for d in dates.split(",") if d.strip()]
        if len(date_list) < 2:
            raise HTTPException(status_code=400, detail="多日对比至少需指定2个日期")
        return svc.compare_multi_days(connection_id, date_list)

    if not date1 or not date2:
        raise HTTPException(status_code=400, detail="必须指定 date1 和 date2，或提供 dates 列表")

    return svc.compare_two_days(connection_id, date1, date2, threshold_multiplier)


@router.get("/compare/html", summary="生成差异比对可视化大屏 (HTML)")
async def compare_html(connection_id: str = "",
                       date1: str = "",
                       date2: str = "",
                       dates: Optional[str] = None,
                       threshold_multiplier: float = 1.0):
    if not connection_id:
        return Response(content="<h3>错误: 必须指定 connection_id</h3>", media_type="text/html")

    if dates:
        date_list = [d.strip() for d in dates.split(",") if d.strip()]
    else:
        if not date1 or not date2:
            return Response(content="<h3>错误: 必须指定 date1 和 date2</h3>", media_type="text/html")
        date_list = [date1, date2]

    if len(date_list) < 2:
        return Response(content="<h3>错误: 对比日期数不能小于2个</h3>", media_type="text/html")

    try:
        html_content = svc.generate_comparison_html_report(connection_id, date_list, threshold_multiplier)
        return Response(content=html_content, media_type="text/html")
    except Exception as e:
        return Response(content=f"<h3>比对报告生成失败: {e}</h3>", media_type="text/html")
