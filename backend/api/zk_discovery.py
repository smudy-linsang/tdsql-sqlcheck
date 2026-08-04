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
from backend.services.zk_discovery_config_service import (
    ZKDiscoveryConfigError,
    zk_discovery_config_service,
)
from backend.services.zk_connection_import_service import (
    ImportCredentials,
    MonitorCredentials,
    ZKImportCommitError,
    zk_connection_import_service,
)


logger = logging.getLogger("tdsql.zk_discovery")
router = APIRouter(prefix="/api/v1/tdsql/discover", tags=["ZK Discovery"])

_SESSION_TTL_SECONDS = 10 * 60
_PREVIEW_TTL_SECONDS = 5 * 60
_MAX_PREVIEW_INSTANCES = 200
_sessions: dict[str, dict] = {}
_previews: dict[str, dict] = {}
_sessions_lock = threading.Lock()


class DiscoveredInstance(BaseModel):
    """浏览器可见的发现预览。绝不包含数据库密码。"""

    item_token: str
    service_name: str
    host: str
    port: int
    status_code: str
    status_text: str
    instance_kind: str = ""
    instance_id: str = ""
    instance_type: Optional[str] = None
    proxy_list: str = ""
    set_ids: list[str] = Field(default_factory=list)
    proxy_count: int = 0
    primary_proxy: str = ""
    # v1.6.0.3 扫描期富集（设计 DESIGN-v1.6.0.3）
    original_host: str = ""
    resolved_name: str = ""
    name_source: str = ""
    business_dbs: list[str] = Field(default_factory=list)
    databases_source: str = ""
    enrich_status: str = ""


class ZKDiscoverResponse(BaseModel):
    discovery_id: str
    source: Literal["zk", "mock"]
    is_mock: bool
    items: list[DiscoveredInstance]


class ZKRegisterRequest(BaseModel):
    discovery_id: str
    item_token: str
    connection_id: str = Field(min_length=1, max_length=128)


class ZKBusinessCredentialsRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class ZKMonitorCredentialsRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)
    database: str = Field(min_length=1, max_length=128)


class ZKImportPreviewRequest(BaseModel):
    discovery_id: str = Field(min_length=1, max_length=64)
    item_tokens: list[str] = Field(min_length=1, max_length=_MAX_PREVIEW_INSTANCES)
    business: ZKBusinessCredentialsRequest
    monitor: ZKMonitorCredentialsRequest
    # v1.6.0.3：本次会话临时覆盖（不写配置）
    octet_rules: list[dict] = Field(default_factory=list)
    manual_databases: dict[str, list[str]] = Field(default_factory=dict)
    name_overrides: dict[str, str] = Field(default_factory=dict)


class ZKImportCommitRequest(BaseModel):
    discovery_id: str = Field(min_length=1, max_length=64)
    preview_id: str = Field(min_length=1, max_length=64)
    row_tokens: list[str] = Field(min_length=1, max_length=2000)


class ZKDiscoveryConfigRequest(BaseModel):
    """管理员提交的 ZK 运行配置；认证口令仅写入，绝不回显。"""

    servers: str = Field(min_length=1, max_length=4096)
    root_path: str = Field("/tdsqlzk", min_length=1, max_length=512)
    driver: Literal["kazoo", "shell"] = "kazoo"
    zkcli_path: str = Field("", max_length=1024)
    proxy_mode: Literal["first", "random"] = "first"
    default_database: str = Field("ALL", max_length=128)
    endpoint_map: dict[str, str] = Field(default_factory=dict)
    auth_username: str = Field(min_length=1, max_length=128)
    auth_password: str = Field("", max_length=1024)
    # v1.6.0.3：段替换 + 扫描富集配置
    octet_rules: list[dict] = Field(default_factory=list)
    monitor_host: str = Field("", max_length=255)
    monitor_port: int = Field(0, ge=0, le=65535)
    monitor_user: str = Field("", max_length=128)
    monitor_password: str = Field("", max_length=1024)
    monitor_db: str = Field("", max_length=128)
    business_username: str = Field("", max_length=128)
    business_password: str = Field("", max_length=1024)
    name_query_hint: str = Field("", max_length=64)
    enrich_enabled: int = Field(1)


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


def _require_admin(request: Request) -> None:
    if getattr(request.state, "role", "") != "admin":
        raise HTTPException(status_code=403, detail="仅系统管理员可维护 ZK 发现配置")


def _is_enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_environment_config(require_auth: bool = True) -> dict:
    """读取兼容旧部署的环境变量配置；数据库尚未配置时作为平滑回退。"""
    force_mock = _is_enabled(os.getenv("ZK_DISCOVERY_FORCE_MOCK"))
    servers = os.getenv("ZK_DISCOVERY_SERVERS", "").strip()
    root = os.getenv("ZK_DISCOVERY_ROOT", "/tdsqlzk").strip()
    zkcli_path = os.getenv("ZK_DISCOVERY_ZKCLI_PATH", "/data/application/zookeeper/bin/zkCli.sh").strip()
    database = os.getenv("ZK_DISCOVERY_DEFAULT_DATABASE", "ALL").strip() or "ALL"
    proxy_mode = os.getenv("ZK_DISCOVERY_PROXY_MODE", "first").strip() or "first"
    driver = os.getenv("ZK_DISCOVERY_DRIVER", "kazoo").strip().lower() or "kazoo"
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
            "auth_user": "", "auth_password": "", "root": root, "driver": driver,
            "zkcli_path": zkcli_path, "default_database": database,
            "proxy_mode": proxy_mode, "endpoint_map": endpoint_map, "source": "deployment_env",
        }

    auth_file_text = os.getenv("ZK_DISCOVERY_AUTH_FILE", "").strip()
    if require_auth and not servers:
        raise ZKDiscoveryUnavailableError("未配置 ZK 服务地址")
    if require_auth and not auth_file_text:
        raise ZKDiscoveryUnavailableError("未配置 ZK 认证秘密文件")
    auth_user = ""
    auth_password = ""
    if auth_file_text:
        try:
            raw_auth = json.loads(Path(auth_file_text).read_text(encoding="utf-8"))
            auth_user = str(raw_auth["username"]).strip()
            auth_password = str(raw_auth["password"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            if require_auth:
                raise ZKDiscoveryUnavailableError("ZK 认证秘密文件不可用") from exc
    if require_auth and (not auth_user or not auth_password):
        raise ZKDiscoveryUnavailableError("ZK 认证秘密文件内容无效")
    return {
        "force_mock": False, "servers": servers,
        "auth_user": auth_user, "auth_password": auth_password,
        "root": root, "driver": driver, "zkcli_path": zkcli_path,
        "default_database": database, "proxy_mode": proxy_mode,
        "endpoint_map": endpoint_map, "source": "deployment_env",
    }


def _read_deployment_config() -> dict:
    """优先读取管理员保存的加密配置；不存在时兼容旧环境变量部署。"""
    # 明确的 Mock 开关只服务开发联调，绝不来自浏览器或数据库配置。
    if _is_enabled(os.getenv("ZK_DISCOVERY_FORCE_MOCK")):
        return _read_environment_config(require_auth=False)
    try:
        database_config = zk_discovery_config_service.load_runtime_config()
    except ZKDiscoveryConfigError as exc:
        raise ZKDiscoveryUnavailableError(str(exc)) from exc
    return database_config or _read_environment_config(require_auth=True)


@router.get("/config", summary="读取 ZK 自动发现配置（管理员）")
def get_discovery_config(request: Request):
    _require_admin(request)
    try:
        database_config = zk_discovery_config_service.public_config()
        if database_config:
            return database_config
        env_config = _read_environment_config(require_auth=False)
        # P6：FORCE_MOCK 联调时的占位地址（mock.invalid:2181）不是真实配置，
        # 不得回显给配置页，避免管理员误把占位值保存进数据库配置源。
        servers = "" if env_config.get("force_mock") else env_config["servers"]
        return {
            "configured": bool(servers and env_config["auth_user"] and env_config["auth_password"]),
            "source": "deployment_env" if servers else "unconfigured",
            "servers": servers,
            "root_path": env_config["root"],
            "driver": env_config["driver"],
            "zkcli_path": env_config["zkcli_path"],
            "proxy_mode": env_config["proxy_mode"],
            "default_database": env_config["default_database"],
            "endpoint_map": env_config["endpoint_map"],
            "auth_username": env_config["auth_user"],
            "password_configured": bool(env_config["auth_password"]),
            "updated_by": "",
            "updated_at": "",
        }
    except (ZKDiscoveryConfigError, ZKDiscoveryUnavailableError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/config", summary="保存 ZK 自动发现配置（管理员）")
def save_discovery_config(body: ZKDiscoveryConfigRequest, request: Request):
    _require_admin(request)
    try:
        return zk_discovery_config_service.save(body.model_dump(), _operator(request))
    except ZKDiscoveryConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _store_session(results: list[dict], owner: str) -> tuple[str, list[dict]]:
    """只在进程内短时暂存原始结果；数据库密码永不进入 API 响应。"""
    now = time.monotonic()
    discovery_id = uuid.uuid4().hex
    visible_items: list[dict] = []
    raw_items: dict[str, dict] = {}
    is_mock = bool(results and results[0].get("is_mock"))
    for result in results:
        item_token = uuid.uuid4().hex
        # setrun 的 user/password 都是 ZK 发现内部数据，不能当作业务连接凭据
        # 进入浏览器或继续留在发现会话中。导入账号只接受操作者本次输入。
        raw = {key: value for key, value in result.items() if key not in {"password", "user", "database"}}
        raw_items[item_token] = raw
        visible = dict(raw)
        set_ids = sorted({str(value).strip() for value in (visible.get("set_ids") or []) if str(value).strip()})
        if not set_ids and visible.get("instance_kind") == "noshard" and visible.get("instance_id"):
            set_ids = [str(visible["instance_id"])]
        visible["set_ids"] = set_ids
        visible["proxy_count"] = len([value for value in str(visible.get("proxy_list") or "").split(";") if value.strip()])
        visible["primary_proxy"] = f"{visible.get('host', '')}:{visible.get('port', '')}"
        visible["item_token"] = item_token
        visible_items.append(visible)
    with _sessions_lock:
        expired = [key for key, item in _sessions.items() if item["expires_at"] <= now]
        for key in expired:
            _sessions.pop(key, None)
        expired_previews = [key for key, item in _previews.items() if item["expires_at"] <= now]
        for key in expired_previews:
            _previews.pop(key, None)
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


def _load_session_items(discovery_id: str, item_tokens: list[str], owner: str) -> list[dict]:
    """读取并校验本操作者的多个发现项；不允许 Mock 进入预检。"""
    unique_tokens = list(dict.fromkeys(item_tokens))
    if len(unique_tokens) != len(item_tokens):
        raise HTTPException(status_code=422, detail="发现记录不可重复选择")
    now = time.monotonic()
    with _sessions_lock:
        session = _sessions.get(discovery_id)
        if not session or session["expires_at"] <= now:
            _sessions.pop(discovery_id, None)
            raise HTTPException(status_code=410, detail="发现会话已过期，请重新扫描")
        if session["owner"] != owner:
            raise HTTPException(status_code=403, detail="无权使用其他操作者的发现会话")
        if session["is_mock"]:
            raise HTTPException(status_code=409, detail="Mock 发现结果禁止生成导入预览")
        items = []
        for token in unique_tokens:
            item = session["items"].get(token)
            if not item:
                raise HTTPException(status_code=404, detail="发现记录不存在")
            items.append(dict(item))
        return items


def _store_preview(discovery_id: str, owner: str, rows: list[dict], business: ImportCredentials,
                   monitor: MonitorCredentials) -> tuple[str, list[dict]]:
    """把凭据留在服务端短会话，返回仅含脱敏候选项的预览。"""
    now = time.monotonic()
    preview_id = uuid.uuid4().hex
    private_rows: dict[str, dict] = {}
    visible_rows: list[dict] = []
    for row in rows:
        row_token = uuid.uuid4().hex
        private_rows[row_token] = dict(row)
        visible = dict(row)
        visible["row_token"] = row_token
        visible_rows.append(visible)
    with _sessions_lock:
        _previews[preview_id] = {
            "owner": owner,
            "discovery_id": discovery_id,
            "expires_at": now + _PREVIEW_TTL_SECONDS,
            "rows": private_rows,
            "business": business,
            "monitor": monitor,
        }
    return preview_id, visible_rows


def _load_preview(body: ZKImportCommitRequest, owner: str) -> tuple[dict, list[dict]]:
    now = time.monotonic()
    selected_tokens = list(dict.fromkeys(body.row_tokens))
    if len(selected_tokens) != len(body.row_tokens):
        raise HTTPException(status_code=422, detail="导入候选项不可重复选择")
    with _sessions_lock:
        preview = _previews.get(body.preview_id)
        if not preview or preview["expires_at"] <= now:
            _previews.pop(body.preview_id, None)
            raise HTTPException(status_code=410, detail="导入预览已过期，请重新生成")
        if preview["owner"] != owner or preview["discovery_id"] != body.discovery_id:
            raise HTTPException(status_code=403, detail="无权使用该导入预览")
        selected = []
        for token in selected_tokens:
            row = preview["rows"].get(token)
            if not row:
                raise HTTPException(status_code=404, detail="导入候选项不存在")
            selected.append(dict(row))
        return preview, selected


@router.post("", response_model=ZKDiscoverResponse)
def discover_instances(request: Request):
    """基于部署端配置执行真实发现，失败时明确返回 503。"""
    try:
        config = _read_deployment_config()
        logger.info(
            "ZK_DISCOVERY_REQUEST operator=%s config_source=%s driver=%s candidate_count=%s root=%s endpoint_mapping_rules=%s",
            _operator(request), config.get("source", "unknown"), config.get("driver", "unknown"),
            len(zk_discovery_service._split_servers(config.get("servers", ""))), config.get("root", ""),
            len(config.get("endpoint_map", {})),
        )
        results = zk_discovery_service.discover(
            zk_server=config["servers"],
            zk_auth_user=config["auth_user"],
            zk_auth_password=config["auth_password"],
            zk_root=config["root"],
            zkcli_path=config["zkcli_path"],
            proxy_mode=config["proxy_mode"],
            default_database=config["default_database"],
            force_mock=config["force_mock"],
            driver=config["driver"],
        )
        is_mock = bool(results and results[0].get("is_mock"))
        if not is_mock:
            results = zk_discovery_service.apply_endpoint_mapping(
                results, config["endpoint_map"], config.get("octet_rules") or [])
            synced = zk_discovery_service.sync_instance_kinds(results)
            logger.info(
                "ZK_DISCOVERY_COMPLETED source=zk records=%s kind_synced=%s",
                len(results), synced,
            )
            # v1.6.0.3 扫描期富集：名称（五级解析链）+ 业务库（适配后 Proxy 枚举）
            if config.get("enrich_enabled", 1):
                from backend.services.zk_scan_enrich_service import enrich_discovered_items
                try:
                    enrich_discovered_items(
                        results, config.get("monitor") or None,
                        config.get("business") or None,
                        hint=config.get("name_query_hint") or "")
                except Exception as enrich_exc:
                    logger.warning("ZK_ENRICH_ABORTED error_type=%s", type(enrich_exc).__name__)
        else:
            logger.warning("ZK_DISCOVERY_MOCK_COMPLETED records=%s kind_synchronization=skipped", len(results))
        discovery_id, visible_items = _store_session(results, _operator(request))
        return {
            "discovery_id": discovery_id,
            "source": "mock" if is_mock else "zk",
            "is_mock": is_mock,
            "items": visible_items,
        }
    except ZKDiscoveryUnavailableError as exc:
        logger.warning("ZK_DISCOVERY_UNAVAILABLE operator=%s reason=%s", _operator(request), str(exc))
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ZK discovery or instance-kind synchronization failed")
        raise HTTPException(status_code=500, detail="ZK 发现后的实例形态同步失败") from exc


@router.post("/import-preview")
def create_import_preview(body: ZKImportPreviewRequest, request: Request):
    """使用操作者本次输入的业务/MonitorDB 凭据生成只读导入预览。"""
    owner = _operator(request)
    instances = _load_session_items(body.discovery_id, body.item_tokens, owner)
    business = ImportCredentials(body.business.username.strip(), body.business.password)
    monitor = MonitorCredentials(
        body.monitor.host.strip(), body.monitor.port, body.monitor.username.strip(),
        body.monitor.password, body.monitor.database.strip(),
    )
    # v1.6.0.3：临时段替换规则（从原始地址重建后应用）；缺省沿用配置
    hint = ""
    try:
        saved = zk_discovery_config_service.load_runtime_config() or {}
        hint = str(saved.get("name_query_hint") or "")
    except Exception:
        saved = {}
    if body.octet_rules:
        for inst in instances:
            if inst.get("original_host"):
                inst["host"] = inst["original_host"]
        instances = zk_discovery_service.apply_endpoint_mapping(
            instances, saved.get("endpoint_map") or {}, body.octet_rules)
    logger.info("ZK_IMPORT_PREVIEW_START operator=%s selected_instances=%s", owner, len(instances))
    try:
        rows = zk_connection_import_service.build_preview(
            instances, business, monitor,
            name_overrides=body.name_overrides,
            manual_databases=body.manual_databases,
            hint=hint)
    except Exception as exc:
        # 服务级异常不将数据库驱动文本返给浏览器，避免泄漏连接上下文。
        logger.exception("ZK_IMPORT_PREVIEW_ABORTED operator=%s error_type=%s", owner, type(exc).__name__)
        raise HTTPException(status_code=500, detail="生成 ZK 导入预览失败") from exc
    preview_id, visible_rows = _store_preview(body.discovery_id, owner, rows, business, monitor)
    summary = {
        "selected_instances": len(instances),
        "ready": sum(row.get("status") == "ready" for row in visible_rows),
        "conflict": sum(row.get("status") == "conflict" for row in visible_rows),
        "error": sum(row.get("status") == "error" for row in visible_rows),
    }
    logger.info("ZK_IMPORT_PREVIEW_COMPLETED operator=%s preview=%s ready=%s conflict=%s error=%s",
                owner, preview_id, summary["ready"], summary["conflict"], summary["error"])
    return {
        "preview_id": preview_id,
        "expires_in_seconds": _PREVIEW_TTL_SECONDS,
        "summary": summary,
        "rows": visible_rows,
    }


class ZKNameDiagnoseRequest(BaseModel):
    """v1.6.0.3 名称解析诊断：返回实例在 monitordb 的形态样本与各级命中。"""
    instance_ids: list[str] = Field(min_length=1, max_length=10)
    discovery_id: str = Field("", max_length=64)
    monitor: Optional[ZKMonitorCredentialsRequest] = None


@router.post("/name-diagnose")
def name_diagnose(body: ZKNameDiagnoseRequest, request: Request):
    """对若干实例跑名称解析诊断（admin/dba），用于固化 name_query_hint。响应不含口令。"""
    if getattr(request.state, "role", "") not in ("admin", "dba"):
        raise HTTPException(status_code=403, detail={"detail": "仅管理员/DBA 可执行名称解析诊断", "code": "E403"})
    from backend.services.zk_name_resolution_service import zk_name_resolution_service

    monitor = body.monitor
    saved: dict = {}
    try:
        saved = zk_discovery_config_service.load_runtime_config() or {}
    except Exception:
        saved = {}
    if monitor is None and saved.get("monitor") and saved["monitor"].get("host"):
        m = saved["monitor"]
        monitor = ZKMonitorCredentialsRequest(
            host=m["host"], port=m["port"], username=m["username"],
            password=m["password"], database=m["database"])
    session_items: dict[str, dict] = {}
    if body.discovery_id:
        with _sessions_lock:
            session = _sessions.get(body.discovery_id)
            if session:
                for raw in session["items"].values():
                    session_items[str(raw.get("instance_id") or "")] = raw
    monitor_conn = None
    items_out = []
    try:
        if monitor is not None:
            import pymysql
            import pymysql.cursors
            try:
                monitor_conn = pymysql.connect(
                    host=monitor.host, port=monitor.port, user=monitor.username,
                    password=monitor.password, database=monitor.database,
                    charset="utf8mb4", connect_timeout=3, read_timeout=10,
                    cursorclass=pymysql.cursors.DictCursor, autocommit=True)
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"无法连接 MonitorDB: {type(exc).__name__}")
        for instance_id in body.instance_ids:
            sess = session_items.get(instance_id, {})
            items_out.append(zk_name_resolution_service.diagnose(
                monitor_conn, instance_id, sess.get("set_ids", []),
                sess.get("instance_kind", ""), zk_name_fields=sess.get("zk_name_fields")))
    finally:
        if monitor_conn is not None:
            try:
                monitor_conn.close()
            except Exception:
                pass
    logger.info("ZK_NAME_DIAGNOSE operator=%s instances=%s", _operator(request), len(items_out))
    return {"items": items_out}


@router.post("/import-commit")
def commit_import_preview(body: ZKImportCommitRequest, request: Request):
    """一次性提交已审核的候选连接，失败时不产生部分连接。"""
    owner = _operator(request)
    preview, selected = _load_preview(body, owner)
    preview_total = len(preview["rows"])
    try:
        result = zk_connection_import_service.commit(
            selected, preview["business"], preview["monitor"], owner, body.discovery_id,
            preview_total=preview_total)
    except ZKImportCommitError as exc:
        status = 409 if exc.code in {"IMPORT_CONFLICT", "ROW_NOT_READY", "NO_READY_ROWS"} else 500
        logger.warning("ZK_IMPORT_COMMIT_ABORTED operator=%s code=%s", owner, exc.code)
        # 主事务已回滚；失败批次另起短事务登记，保证合规审计可追溯（P4）
        zk_connection_import_service.record_failed_batch(
            selected, owner, body.discovery_id, exc.code, preview_total=preview_total)
        raise HTTPException(status_code=status, detail=exc.detail) from exc
    finally:
        # 无论成功还是失败都销毁凭据和一次性预览，避免重放；失败可重新预检。
        with _sessions_lock:
            _previews.pop(body.preview_id, None)
    return {
        "status": "success",
        "batch_id": result["batch_id"],
        "created_count": len(result["created"]),
        "connections": result["created"],
    }


@router.get("/import-batches/{batch_id}")
def get_import_batch(batch_id: str, request: Request):
    """查询非敏感的标准化导入审计结果。"""
    from backend.services.database import _get_connection, ensure_db

    ensure_db()
    conn = _get_connection()
    try:
        batch = conn.execute(
            "SELECT id, discovery_id, operator_username, selected_instance_count, candidate_count, "
            "created_count, skipped_count, failed_count, status, failure_summary, created_at, completed_at "
            "FROM zk_discovery_import_batches WHERE id=?", (batch_id,)
        ).fetchone()
        if not batch:
            raise HTTPException(status_code=404, detail="ZK 导入批次不存在")
        # 仅管理员或批次创建者可读取审计明细。
        if getattr(request.state, "role", "") != "admin" and batch.get("operator_username") != _operator(request):
            raise HTTPException(status_code=403, detail="无权查看该 ZK 导入批次")
        items = conn.execute(
            "SELECT source_instance_id, instance_kind, instance_type, primary_proxy_host, primary_proxy_port, "
            "set_list, resolved_instance_name, database_name, generated_connection_name, connection_id, "
            "result_status, failure_code, created_at FROM zk_discovery_import_items WHERE batch_id=? ORDER BY id",
            (batch_id,),
        ).fetchall()
        return {"batch": dict(batch), "items": [dict(item) for item in items]}
    finally:
        conn.close()


@router.post("/register", status_code=410)
def register_instance(_body: ZKRegisterRequest):
    """禁止旧的直写导入路径，避免写入 ALL 库或默认错误类型。"""
    raise HTTPException(status_code=410, detail="旧 ZK 直接导入已废弃，请使用导入预览后确认提交")
