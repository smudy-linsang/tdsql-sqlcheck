"""M3 · G6 表结构比对 API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from backend.services import schema_diff_service as svc
from backend.services.connection_registry import registry, ConnectionNotFoundError

router = APIRouter(prefix="/api/v1/schema-diff", tags=["表结构比对"])


class DiffRequest(BaseModel):
    left_conn: str = Field(..., description="基准实例连接ID(如生产)")
    right_conn: str = Field(..., description="对比实例连接ID(如测试)")
    databases: Optional[list] = Field(None, description="库名列表；空/ALL则全部业务库")


def _pool(cid, side):
    try:
        return registry.get(cid)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=400, detail=f"{side}实例未连接或不存在: {cid}")


@router.post("/run", summary="发起表结构比对")
def run(body: DiffRequest):
    lp = _pool(body.left_conn, "基准")
    rp = _pool(body.right_conn, "对比")
    try:
        return svc.run_diff(lp, rp, databases=body.databases,
                            left_conn=body.left_conn, right_conn=body.right_conn)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/items/{diff_id}", summary="结构差异明细")
def items(diff_id: int, severity: str = ""):
    return {"items": svc.get_items(diff_id, severity)}
