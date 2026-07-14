"""G10 ZK 发现 API 路由"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from backend.services.zk_discovery_service import zk_discovery_service

router = APIRouter(prefix="/api/v1/tdsql/discover", tags=["ZK Discovery"])


class ZKDiscoverRequest(BaseModel):
    zk_server: str = "127.0.0.1:2118"
    zk_auth_user: str = "tdsqlsys_zk"
    zk_auth_password: str = ""
    zk_root: str = "/tdsqlzk"
    zkcli_path: str = "/data/application/zookeeper/bin/zkCli.sh"
    proxy_mode: str = "random"
    default_database: str = "ALL"
    force_mock: bool = False


class DiscoveredInstance(BaseModel):
    service_name: str
    host: str
    port: int
    user: str
    password: str
    database: str
    status_code: str
    status_text: str


class ZKRegisterRequest(BaseModel):
    connection_id: str
    service_name: str
    host: str
    port: int
    user: str
    password: str
    database: str = "ALL"


@router.post("", response_model=List[DiscoveredInstance])
def discover_instances(req: ZKDiscoverRequest):
    """从 ZK 自动扫描并发现 TDSQL 实例"""
    try:
        results = zk_discovery_service.discover(
            zk_server=req.zk_server,
            zk_auth_user=req.zk_auth_user,
            zk_auth_password=req.zk_auth_password,
            zk_root=req.zk_root,
            zkcli_path=req.zkcli_path,
            proxy_mode=req.proxy_mode,
            default_database=req.default_database,
            force_mock=req.force_mock
        )
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/register")
def register_instance(req: ZKRegisterRequest):
    """注册发现的 TDSQL 实例"""
    try:
        inst = {
            "service_name": req.service_name,
            "host": req.host,
            "port": req.port,
            "user": req.user,
            "password": req.password,
            "database": req.database
        }
        conn_id = zk_discovery_service.register_discovered(req.connection_id, inst)
        return {"status": "success", "connection_id": conn_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
