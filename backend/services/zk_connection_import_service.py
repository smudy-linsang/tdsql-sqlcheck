"""ZK 自动发现的标准化连接导入服务。

该服务刻意将“读取 ZK 拓扑”与“使用业务账号/MonitorDB 预检并落库”分开：
ZK setrun 中的内部账号永远不是业务连接凭据，也不会进入浏览器响应。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger("tdsql.zk_import")


SYSTEM_DATABASES = {
    "information_schema",
    "mysql",
    "performance_schema",
    "sys",
}


class ZKImportPreparationError(RuntimeError):
    """单个实例预检失败；文本不应包含任何口令。"""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


class ZKImportCommitError(RuntimeError):
    """提交时发生冲突或事务错误。"""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class ImportCredentials:
    username: str
    password: str


@dataclass(frozen=True)
class MonitorCredentials:
    host: str
    port: int
    username: str
    password: str
    database: str


class ZKConnectionImportService:
    """生成预览并事务性创建一库一连接的连接记录。"""

    def _connect(self, host: str, port: int, username: str, password: str, database: str):
        """只创建短连接。调用方负责关闭；异常统一在上层脱敏。"""
        try:
            import pymysql
            import pymysql.cursors
        except ImportError as exc:  # pragma: no cover - 生产依赖，保留明确信息
            raise ZKImportPreparationError("PYMYSQL_UNAVAILABLE", "数据库客户端不可用") from exc
        return pymysql.connect(
            host=host,
            port=int(port),
            user=username,
            password=password,
            database=database or None,
            charset="utf8mb4",
            connect_timeout=5,
            read_timeout=10,
            write_timeout=10,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    @staticmethod
    def _safe_set_ids(instance: dict) -> list[str]:
        values = instance.get("set_ids") or []
        if isinstance(values, str):
            values = values.replace(";", ",").split(",")
        set_ids = sorted({str(value).strip() for value in values if str(value).strip()})
        if not set_ids and instance.get("instance_kind") == "noshard":
            fallback = str(instance.get("instance_id") or "").strip()
            if fallback:
                set_ids = [fallback]
        return set_ids

    @staticmethod
    def _proxy_endpoints(instance: dict) -> list[tuple[str, int]]:
        raw = [item.strip() for item in str(instance.get("proxy_list") or "").split(";") if item.strip()]
        primary = f"{instance.get('host', '')}:{instance.get('port', '')}"
        if primary and primary not in raw:
            raw.insert(0, primary)
        endpoints: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for text in raw:
            try:
                host, port_text = text.rsplit(":", 1)
                endpoint = (host.strip(), int(port_text))
            except (AttributeError, TypeError, ValueError):
                continue
            if endpoint[0] and 1 <= endpoint[1] <= 65535 and endpoint not in seen:
                endpoints.append(endpoint)
                seen.add(endpoint)
        return endpoints

    def _resolve_instance_name(self, monitor: MonitorCredentials, instance: dict) -> tuple[str, str]:
        """查询与赤兔同源的 MonitorDB 实例元数据。

        group 先按 group ID 查；若该集群没有 group 级记录，才回退代表 SET。
        不使用 group/set ID 充当名称，确保连接名具有真正的业务可读性。
        """
        instance_id = str(instance.get("instance_id") or "").strip()
        set_ids = self._safe_set_ids(instance)
        candidates: list[tuple[str, str]] = [(instance_id, "instance")]
        if instance.get("instance_kind") == "groupshard" and set_ids:
            candidates.append((set_ids[0], "representative_set"))
        if not instance_id:
            raise ZKImportPreparationError("INSTANCE_ID_MISSING", "发现记录缺少实例 ID")
        try:
            connection = self._connect(
                monitor.host, monitor.port, monitor.username, monitor.password, monitor.database)
        except Exception as exc:
            logger.warning("ZK_IMPORT_MONITOR_CONNECT_FAILED instance=%s error_type=%s", instance_id, type(exc).__name__)
            raise ZKImportPreparationError("MONITOR_CONNECT_FAILED", "无法连接 MonitorDB") from exc
        try:
            with connection.cursor() as cursor:
                for candidate_id, source in candidates:
                    cursor.execute(
                        "SELECT f_key, f_val FROM m_data_cur "
                        "WHERE f_type = 1 AND f_mid = %s AND f_key IN ('instance_name', 'clientName') "
                        "ORDER BY CASE f_key WHEN 'instance_name' THEN 0 ELSE 1 END",
                        (f"/tdsqlzk/{candidate_id}",),
                    )
                    for row in cursor.fetchall() or []:
                        name = str((row or {}).get("f_val") or "").strip()
                        if name:
                            logger.info("ZK_IMPORT_METADATA_RESOLVED instance=%s source=%s", instance_id, source)
                            return name, source
        except Exception as exc:
            logger.warning("ZK_IMPORT_METADATA_QUERY_FAILED instance=%s error_type=%s", instance_id, type(exc).__name__)
            raise ZKImportPreparationError("MONITOR_METADATA_QUERY_FAILED", "查询 MonitorDB 实例元数据失败") from exc
        finally:
            connection.close()
        raise ZKImportPreparationError("INSTANCE_NAME_UNRESOLVED", "MonitorDB 未解析到实例名称")

    def _list_business_databases(
        self, instance: dict, business: ImportCredentials, monitor_db: str
    ) -> list[str]:
        """对发现到的每个 Proxy 做只读目录检查，拒绝目录不一致。"""
        instance_id = str(instance.get("instance_id") or "").strip()
        endpoints = self._proxy_endpoints(instance)
        if not endpoints:
            raise ZKImportPreparationError("NO_AVAILABLE_PROXY", "未发现可用 Proxy 地址")
        excluded = set(SYSTEM_DATABASES)
        if monitor_db:
            excluded.add(monitor_db.strip().lower())
        catalogues: list[set[str]] = []
        for host, port in endpoints:
            try:
                connection = self._connect(host, port, business.username, business.password, "")
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SHOW DATABASES")
                        values = {
                            str((row or {}).get("Database") or (row or {}).get("database") or "").strip()
                            for row in (cursor.fetchall() or [])
                        }
                finally:
                    connection.close()
            except Exception as exc:
                logger.warning(
                    "ZK_IMPORT_BUSINESS_PROXY_FAILED instance=%s endpoint=%s:%s error_type=%s",
                    instance_id, host, port, type(exc).__name__,
                )
                raise ZKImportPreparationError("BUSINESS_PROXY_INCOMPLETE", "无法通过全部发现 Proxy 枚举业务库") from exc
            catalogues.append({name for name in values if name and name.lower() not in excluded})
        canonical = catalogues[0]
        if any(values != canonical for values in catalogues[1:]):
            logger.warning("ZK_IMPORT_DATABASES_INCONSISTENT instance=%s proxy_count=%s", instance_id, len(endpoints))
            raise ZKImportPreparationError("DATABASE_LIST_INCONSISTENT", "多个 Proxy 返回的业务库列表不一致")
        if not canonical:
            raise ZKImportPreparationError("NO_BUSINESS_DATABASE", "未发现可导入的业务库")
        return sorted(canonical, key=lambda item: (item.lower(), item))

    @staticmethod
    def _validate_instance(instance: dict) -> tuple[str, list[str]]:
        kind = str(instance.get("instance_kind") or "").strip()
        expected_type = {"noshard": "centralized", "groupshard": "distributed"}.get(kind)
        if not expected_type or instance.get("instance_type") != expected_type:
            raise ZKImportPreparationError("UNKNOWN_INSTANCE_KIND", "发现记录的实例形态无效")
        set_ids = ZKConnectionImportService._safe_set_ids(instance)
        if not set_ids:
            raise ZKImportPreparationError("GROUP_WITHOUT_SETS", "实例未读取到完整 SET 列表")
        if not ZKConnectionImportService._proxy_endpoints(instance):
            raise ZKImportPreparationError("NO_AVAILABLE_PROXY", "未发现可用 Proxy 地址")
        return expected_type, set_ids

    def build_preview(
        self,
        instances: Iterable[dict],
        business: ImportCredentials,
        monitor: MonitorCredentials,
    ) -> list[dict]:
        """只读预检，返回可展示的候选项或逐实例失败项。"""
        rows: list[dict] = []
        for instance in instances:
            instance_id = str(instance.get("instance_id") or "").strip()
            primary_host = str(instance.get("host") or "").strip()
            primary_port = int(instance.get("port") or 0)
            base = {
                "source_instance_id": instance_id,
                "instance_kind": str(instance.get("instance_kind") or ""),
                "instance_type": str(instance.get("instance_type") or ""),
                "primary_proxy": f"{primary_host}:{primary_port}" if primary_host and primary_port else "",
                "primary_proxy_host": primary_host,
                "primary_proxy_port": primary_port,
                "set_ids": self._safe_set_ids(instance),
                "monitor_host": monitor.host,
                "monitor_port": monitor.port,
                "monitor_user": monitor.username,
                "monitor_db": monitor.database,
            }
            try:
                instance_type, set_ids = self._validate_instance(instance)
                instance_name, name_source = self._resolve_instance_name(monitor, instance)
                databases = self._list_business_databases(instance, business, monitor.database)
                for database in databases:
                    generated_name = f"{instance_name}-{primary_port}-{database}"
                    if len(generated_name) > 255:
                        raise ZKImportPreparationError("CONNECTION_NAME_TOO_LONG", "生成的连接名称超过 255 个字符")
                    rows.append({
                        **base,
                        "instance_type": instance_type,
                        "set_ids": set_ids,
                        "resolved_instance_name": instance_name,
                        "name_source": name_source,
                        "database": database,
                        "generated_connection_name": generated_name,
                        "status": "ready",
                        "failure_code": "",
                        "failure_detail": "",
                    })
            except ZKImportPreparationError as exc:
                logger.info("ZK_IMPORT_PREVIEW_ITEM_FAILED instance=%s code=%s", instance_id, exc.code)
                rows.append({
                    **base,
                    "resolved_instance_name": "",
                    "name_source": "",
                    "database": "",
                    "generated_connection_name": "",
                    "status": "error",
                    "failure_code": exc.code,
                    "failure_detail": exc.detail,
                })
        self._mark_existing(rows)
        return rows

    @staticmethod
    def _mark_existing(rows: list[dict]) -> None:
        """预检元库冲突；提交前仍会在事务中再次检查。"""
        from backend.services.database import _get_connection, ensure_db

        ensure_db()
        conn = _get_connection()
        try:
            for row in rows:
                if row.get("status") != "ready":
                    continue
                existing = conn.execute(
                    "SELECT id FROM tdsql_connections WHERE host=? AND port=? AND `database`=? LIMIT 1",
                    (row["primary_proxy_host"], row["primary_proxy_port"], row["database"]),
                ).fetchone()
                same_name = conn.execute(
                    "SELECT id FROM tdsql_connections WHERE name=? LIMIT 1",
                    (row["generated_connection_name"],),
                ).fetchone()
                if existing or same_name:
                    row.update({
                        "status": "conflict",
                        "failure_code": "EXISTING_CONNECTION",
                        "failure_detail": "同地址端口库或连接名称已存在，不会覆盖既有连接",
                    })
        finally:
            conn.close()

    def commit(
        self,
        candidates: Iterable[dict],
        business: ImportCredentials,
        monitor: MonitorCredentials,
        operator: str,
        discovery_id: str,
    ) -> dict:
        """在一个元库事务中创建连接和导入审计；有冲突时零连接写入。"""
        selected = list(candidates)
        if not selected:
            raise ZKImportCommitError("NO_READY_ROWS", "没有可提交的候选连接")
        if any(row.get("status") != "ready" for row in selected):
            raise ZKImportCommitError("ROW_NOT_READY", "选中项包含不可导入的候选连接")

        from backend.services.database import _get_connection, ensure_db
        from backend.services.security_service import encrypt_password

        ensure_db()
        batch_id = uuid.uuid4().hex
        created: list[dict] = []
        conn = _get_connection()
        try:
            # 事务内再检查，阻止两位操作者的预览并发覆盖同一连接。
            for row in selected:
                by_endpoint = conn.execute(
                    "SELECT id FROM tdsql_connections WHERE host=? AND port=? AND `database`=? LIMIT 1",
                    (row["primary_proxy_host"], row["primary_proxy_port"], row["database"]),
                ).fetchone()
                by_name = conn.execute(
                    "SELECT id FROM tdsql_connections WHERE name=? LIMIT 1",
                    (row["generated_connection_name"],),
                ).fetchone()
                if by_endpoint or by_name:
                    raise ZKImportCommitError("IMPORT_CONFLICT", "提交期间发现既有连接，未创建任何连接")

            conn.execute(
                "INSERT INTO zk_discovery_import_batches "
                "(id, discovery_id, operator_username, selected_instance_count, candidate_count, created_count, status, created_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'completed', NOW(), NOW())",
                (batch_id, discovery_id, operator, len({row["source_instance_id"] for row in selected}),
                 len(selected), len(selected)),
            )
            business_password = encrypt_password(business.password)
            monitor_password = encrypt_password(monitor.password)
            for row in selected:
                connection_id = uuid.uuid4().hex
                description = (
                    f"ZK 自动发现导入；实例ID={row['source_instance_id']}；批次={batch_id}"
                )
                conn.execute(
                    "INSERT INTO tdsql_connections "
                    "(id, name, host, port, username, password_encrypted, `database`, charset, "
                    "is_default, is_distributed, description, set_list, monitor_host, monitor_port, "
                    "monitor_user, monitor_password_encrypted, monitor_db, zk_instance_kind, "
                    "zk_instance_id, zk_synced_at, zk_import_batch_id, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'utf8mb4', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), ?, 'disconnected', NOW(), NOW())",
                    (
                        connection_id, row["generated_connection_name"], row["primary_proxy_host"],
                        row["primary_proxy_port"], business.username, business_password, row["database"],
                        1 if row["instance_type"] == "distributed" else 0, description,
                        ",".join(row["set_ids"]), monitor.host, monitor.port, monitor.username,
                        monitor_password, monitor.database, row["instance_kind"], row["source_instance_id"],
                        batch_id,
                    ),
                )
                conn.execute(
                    "INSERT INTO zk_discovery_import_items "
                    "(batch_id, source_instance_id, instance_kind, instance_type, primary_proxy_host, "
                    "primary_proxy_port, set_list, resolved_instance_name, database_name, "
                    "generated_connection_name, connection_id, result_status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', NOW())",
                    (
                        batch_id, row["source_instance_id"], row["instance_kind"], row["instance_type"],
                        row["primary_proxy_host"], row["primary_proxy_port"], ",".join(row["set_ids"]),
                        row["resolved_instance_name"], row["database"], row["generated_connection_name"],
                        connection_id,
                    ),
                )
                created.append({"id": connection_id, "name": row["generated_connection_name"], "database": row["database"]})
            conn.commit()
        except ZKImportCommitError:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            logger.exception("ZK_IMPORT_COMMIT_ABORTED error_type=%s", type(exc).__name__)
            raise ZKImportCommitError("IMPORT_TRANSACTION_FAILED", "导入事务失败，未创建任何连接") from exc
        finally:
            conn.close()

        from backend.services.instance_type_service import instance_type_service
        instance_type_service.invalidate()
        logger.info("ZK_IMPORT_COMMIT_SUCCEEDED batch=%s operator=%s created=%s", batch_id, operator, len(created))
        return {"batch_id": batch_id, "created": created}


zk_connection_import_service = ZKConnectionImportService()
