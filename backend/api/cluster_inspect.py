"""M2 · G3 集群深度巡检 API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from backend.services import cluster_inspect_service as svc
from backend.services.connection_registry import registry, ConnectionNotFoundError

router = APIRouter(prefix="/api/v1/cluster-inspect", tags=["集群深度巡检"])


class InspectRequest(BaseModel):
    connection_id: str = Field("", description="目标连接ID")
    nodes: Optional[list] = Field(None, description="指定监控对象(f_mid)列表；空则自动发现")


def _pool(connection_id: str):
    try:
        return registry.get(connection_id)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=400, detail="未连接TDSQL实例或连接不存在")


@router.post("/run", summary="发起集群深度巡检")
async def run(body: InspectRequest):
    pool = _pool(body.connection_id)
    probe = pool.monitor_probe()
    if not probe["ok"]:
        raise HTTPException(status_code=400,
                            detail=f"monitordb(15001)不可用: {probe['error']}")
    try:
        return svc.run_inspection(pool, connection_id=body.connection_id, nodes=body.nodes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list/{connection_id}", summary="巡检历史列表")
async def list_history(connection_id: str):
    return {"items": svc.list_inspections(connection_id)}


@router.get("/issues/{inspection_id}", summary="巡检明细(可按severity过滤)")
async def issues(inspection_id: int, severity: str = ""):
    return {"items": svc.get_issues(inspection_id, severity)}
