"""ZK 实例自动发现 API。

认证资料由部署端秘密文件提供。浏览器只获得脱敏的发现预览和短时会话令牌，不能取得
ZK 中的数据库口令，也不能将 Mock 记录写入实例管理或实例形态权威源。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.services.zk_discovery_service import (
    ZKDiscoveryUnavailableError,
    zk_discovery_service,
)


logger = logging.getLogger("tdsql.zk_discovery")
router = APIRouter(prefix="/api/v1/tdsql/discover", tags=["ZK Discovery"])

_SESSION_TTL_SECONDS = 10 * 60
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()


class DiscoveredInstance(BaseModel):
    """浏览器可见的发现预览。绝不包含数据库密码。"""

    item_token: str
    service_name: str
    host: str
    port: int
    user: str
    database: str
    status_code: str
    status_text: str
    instance_kind: str = ""
    instance_id: str = ""
    instance_type: Optional[str] = None
    proxy_list: str = ""


class ZKDiscoverResponse(BaseModel):
    discovery_id: str
    source: Literal["zk", "mock"]
    is_mock: bool
    items: list[DiscoveredInstance]


class ZKRegisterRequest(BaseModel):
    discovery_id: str
    item_token: str
    connection_id: str = Field(min_length=1, max_length=128)


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


def _is_enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_deployment_config() -> dict:
    """读取不含浏览器输入的部署配置和本地秘密文件。"""
    force_mock = _is_enabled(os.getenv("ZK_DISCOVERY_FORCE_MOCK"))
    servers = os.getenv("ZK_DISCOVERY_SERVERS", "").strip()
    root = os.getenv("ZK_DISCOVERY_ROOT", "/tdsqlzk").strip()
    zkcli_path = os.getenv("ZK_DISCOVERY_ZKCLI_PATH", "/data/application/zookeeper/bin/zkCli.sh").strip()
    database = os.getenv("ZK_DISCOVERY_DEFAULT_DATABASE", "ALL").strip() or "ALL"
    proxy_mode = os.getenv("ZK_DISCOVERY_PROXY_MODE", "random").strip() or "random"

    map_text = os.getenv("ZK_DISCOVERY_ENDPOINT_MAP", "{}").strip() or "{}"
    try:
        endpoint_map = json.loads(map_text)
    except json.JSONDecodeError as exc:
        raise ZKDiscoveryUnavailableError("ZK 地址映射配置格式无效") from exc
    if not isinstance(endpoint_map, dict) or not all(
        isinstance(source, str) and isinstance(target, str)
        for source, target in endpoint_map.items()
    ):
        raise ZKDiscoveryUnavailableError("ZK 地址映射配置格式无效")

    if force_mock:
        return {
            "force_mock": True, "servers": servers or "mock.invalid:2181",
            "auth_user": "", "auth_password": "", "root": root,
            "zkcli_path": zkcli_path, "default_database": database,
            "proxy_mode": proxy_mode, "endpoint_map": endpoint_map,
        }

    auth_file_text = os.getenv("ZK_DISCOVERY_AUTH_FILE", "").strip()
    if not servers:
        raise ZKDiscoveryUnavailableError("未配置 ZK 服务地址")
    if not auth_file_text:
        raise ZKDiscoveryUnavailableError("未配置 ZK 认证秘密文件")
    try:
        auth_file = Path(auth_file_text)
        raw_auth = json.loads(auth_file.read_text(encoding="utf-8"))
        auth_user = str(raw_auth["username"]).strip()
        auth_password = str(raw_auth["password"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ZKDiscoveryUnavailableError("ZK 认证秘密文件不可用") from exc
    if not auth_user or not auth_password:
        raise ZKDiscoveryUnavailableError("ZK 认证秘密文件内容无效")

    return {
        "force_mock": False, "servers": servers,
        "auth_user": auth_user, "auth_password": auth_password,
        "root": root, "zkcli_path": zkcli_path,
        "default_database": database, "proxy_mode": proxy_mode,
        "endpoint_map": endpoint_map,
    }


def _store_session(results: list[dict], owner: str) -> tuple[str, list[dict]]:
    """只在进程内短时暂存原始结果；数据库密码永不进入 API 响应。"""
    now = time.monotonic()
    discovery_id = uuid.uuid4().hex
    visible_items: list[dict] = []
    raw_items: dict[str, dict] = {}
    is_mock = bool(results and results[0].get("is_mock"))
    for result in results:
        item_token = uuid.uuid4().hex
        raw_items[item_token] = dict(result)
        visible = {key: value for key, value in result.items() if key != "password"}
        visible["item_token"] = item_token
        visible_items.append(visible)
    with _sessions_lock:
        expired = [key for key, item in _sessions.items() if item["expires_at"] <= now]
        for key in expired:
            _sessions.pop(key, None)
        _sessions[discovery_id] = {
            "owner": owner,
            "expires_at": now + _SESSION_TTL_SECONDS,
            "is_mock": is_mock,
            "items": raw_items,
        }
    return discovery_id, visible_items


def _load_session_item(discovery_id: str, item_token: str, owner: str) -> dict:
    now = time.monotonic()
    with _sessions_lock:
        session = _sessions.get(discovery_id)
        if not session or session["expires_at"] <= now:
            _sessions.pop(discovery_id, None)
            raise HTTPException(status_code=410, detail="发现会话已过期，请重新扫描")
        if session["owner"] != owner:
            raise HTTPException(status_code=403, detail="无权使用其他操作者的发现会话")
        if session["is_mock"]:
            raise HTTPException(status_code=409, detail="Mock 发现结果禁止导入或同步")
        item = session["items"].get(item_token)
        if not item:
            raise HTTPException(status_code=404, detail="发现记录不存在")
        return dict(item)


@router.post("", response_model=ZKDiscoverResponse)
def discover_instances(request: Request):
    """基于部署端配置执行真实发现，失败时明确返回 503。"""
    try:
        config = _read_deployment_config()
        results = zk_discovery_service.discover(
            zk_server=config["servers"],
            zk_auth_user=config["auth_user"],
            zk_auth_password=config["auth_password"],
            zk_root=config["root"],
            zkcli_path=config["zkcli_path"],
            proxy_mode=config["proxy_mode"],
            default_database=config["default_database"],
            force_mock=config["force_mock"],
        )
        is_mock = bool(results and results[0].get("is_mock"))
        if not is_mock:
            results = zk_discovery_service.apply_endpoint_mapping(results, config["endpoint_map"])
            synced = zk_discovery_service.sync_instance_kinds(results)
            logger.info("real ZK discovery completed: records=%s synced=%s", len(results), synced)
        else:
            logger.warning("Mock discovery completed without instance-kind synchronization")
        discovery_id, visible_items = _store_session(results, _operator(request))
        return {
            "discovery_id": discovery_id,
            "source": "mock" if is_mock else "zk",
            "is_mock": is_mock,
            "items": visible_items,
        }
    except ZKDiscoveryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ZK discovery or instance-kind synchronization failed")
        raise HTTPException(status_code=500, detail="ZK 发现后的实例形态同步失败") from exc


@router.post("/register")
def register_instance(body: ZKRegisterRequest, request: Request):
    """从服务端短时发现会话导入一个真实实例，浏览器不传递连接密码。"""
    inst = _load_session_item(body.discovery_id, body.item_token, _operator(request))
    try:
        conn_id = zk_discovery_service.register_discovered(body.connection_id, inst)
        synced = zk_discovery_service.sync_instance_kinds([inst])
        return {"status": "success", "connection_id": conn_id, "kind_synced": synced > 0}
    except Exception as exc:
        logger.exception("ZK discovered instance registration failed")
        raise HTTPException(status_code=500, detail="ZK 发现实例导入失败") from exc
