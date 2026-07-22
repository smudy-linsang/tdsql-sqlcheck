"""M3 · G5 索引健康审计 API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.services import index_audit_service as svc
from backend.services.connection_registry import registry, ConnectionNotFoundError

router = APIRouter(prefix="/api/v1/index-audit", tags=["索引健康审计"])


class AuditRequest(BaseModel):
    connection_id: str = Field("", description="目标连接ID")
    database: str = Field("", description="仅审计指定库；空则全部业务库")


def _pool(cid):
    try:
        return registry.get(cid)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=400, detail="未连接TDSQL实例或连接不存在")


@router.post("/run", summary="发起索引体检")
def run(body: AuditRequest):
    pool = _pool(body.connection_id)
    try:
        return svc.run_audit(pool, connection_id=body.connection_id,
                             database=body.database)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/findings/{audit_id}", summary="索引体检明细")
def findings(audit_id: int, severity: str = ""):
    return {"items": svc.get_findings(audit_id, severity)}
