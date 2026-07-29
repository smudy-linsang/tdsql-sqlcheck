"""G10 ZK 发现 API 路由"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from backend.services.zk_discovery_service import zk_discovery_service

logger = logging.getLogger("tdsql.zk_discovery")

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
    # V1.5.1：实例形态（规则适用域判定的权威依据）。旧脚本/无 --with-type 时缺省空
    instance_kind: str = ""
    instance_id: str = ""
    instance_type: Optional[str] = None
    proxy_list: str = ""


class ZKRegisterRequest(BaseModel):
    connection_id: str
    service_name: str
    host: str
    port: int
    user: str
    password: str
    database: str = "ALL"
    # V1.5.1：发现结果里的实例形态，随注册一并落库（S1 权威源）
    instance_kind: str = ""
    instance_id: str = ""
    proxy_list: str = ""


@router.post("", response_model=List[DiscoveredInstance])
def discover_instances(req: ZKDiscoverRequest):
    """从 ZK 自动扫描并发现 TDSQL 实例（V1.5.1：含实例形态，并回写已注册实例）"""
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
        # V1.5.1：把 ZK 形态同步到已注册实例（S1 权威源落库）。
        # 同步失败仅告警，不影响发现结果返回。
        try:
            synced = zk_discovery_service.sync_instance_kinds(results)
            if synced:
                logger.info(f"ZK 发现后已同步 {synced} 个已注册实例的实例形态")
        except Exception as e:
            logger.warning(f"ZK 实例形态同步失败（不影响发现结果）: {e}")
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/register")
def register_instance(req: ZKRegisterRequest):
    """注册发现的 TDSQL 实例（V1.5.1：同步写入 ZK 实例形态）"""
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
        # V1.5.1：注册后立即回写形态（新注册实例不必等下一次发现才获得权威判定）
        kind_synced = False
        if req.instance_kind:
            try:
                synced = zk_discovery_service.sync_instance_kinds([{
                    "instance_kind": req.instance_kind,
                    "instance_id": req.instance_id,
                    "proxy_list": req.proxy_list,
                    "host": req.host,
                    "port": req.port,
                }])
                kind_synced = synced > 0
            except Exception as e:
                logger.warning(f"注册后实例形态同步失败（不影响注册）: {e}")
        return {"status": "success", "connection_id": conn_id,
                "kind_synced": kind_synced}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
