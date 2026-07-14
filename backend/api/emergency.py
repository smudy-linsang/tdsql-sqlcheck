"""M3 · G7 应急诊断 API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from backend.services import emergency_diag_service as svc
from backend.services.connection_registry import registry, ConnectionNotFoundError

router = APIRouter(prefix="/api/v1/emergency", tags=["应急诊断"])


class DiagRequest(BaseModel):
    connection_id: str = Field("", description="目标连接ID")
    actions: Optional[list] = Field(None, description="status/session/bigtrx/lock/slow/innodb/all")
    tdsql: bool = Field(False, description="分布式实例(加 /*sets:allsets*/)")


def _pool(cid):
    try:
        return registry.get(cid)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=400, detail="未连接TDSQL实例或连接不存在")


@router.post("/run", summary="一键应急诊断(只读)")
async def run(body: DiagRequest):
    pool = _pool(body.connection_id)
    try:
        return svc.run(pool, connection_id=body.connection_id,
                       actions=body.actions, tdsql=body.tdsql)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
