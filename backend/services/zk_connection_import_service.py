"""ZK 自动发现的标准化连接导入服务。

该服务刻意将“读取 ZK 拓扑”与“使用业务账号/MonitorDB 预检并落库”分开：
ZK setrun 中的内部账号永远不是业务连接凭据，也不会进入浏览器响应。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Iterable, Optional

import pymysql

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
        """只创建短连接。调用方负责关闭；异常统一在上层脱敏。

        v1.6.0.6（A-P2-02）：连接类异常必须转成逐实例可读的失败，
        不能裸抛出去把整批预检掀翻成无指向的 500（填错端口/口令是
        真实会发生的场景）。错误文本只带 host:port，不带账号口令。
        """
        try:
            import pymysql
            import pymysql.cursors
        except ImportError as exc:  # pragma: no cover - 生产依赖，保留明确信息
            raise ZKImportPreparationError("PYMYSQL_UNAVAILABLE", "数据库客户端不可用") from exc
        try:
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
        except ZKImportPreparationError:
            raise
        except Exception as exc:
            logger.warning(
                "ZK_IMPORT_DB_CONNECT_FAILED endpoint=%s:%s error_type=%s",
                host, port, type(exc).__name__,
            )
            raise ZKImportPreparationError(
                "DB_CONNECT_FAILED", f"连接 {host}:{port} 失败，请核对地址、端口、账号与网络") from exc

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
        # v1.6.0.6（A-P3-01）：主 Proxy 真正前移——先从列表移除再插到首位，
        # 已知可达端点优先，避免死 Proxy 排在前面先等一轮超时。
        if instance.get("host") and primary:
            raw = [item for item in raw if item != primary]
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

    def _list_business_databases(
        self, instance: dict, business: ImportCredentials, monitor_db: str
    ) -> tuple[list[str], str]:
        """对发现到的每个 Proxy 做只读目录检查，返回 (业务库列表, 来源标记)。

        v1.6.0.6（A-P1-01）：对齐扫描富集侧"≥1 成功即可"口径。旧版任一 Proxy
        失败即整实例 BUSINESS_PROXY_INCOMPLETE，而内网分布式与集中式普遍双 Proxy
        （主+备），备 Proxy 不可达会让预检大面积失败。现逐端点捕获异常不抛出：
          - 0 个成功 -> NO_AVAILABLE_PROXY；
          - 有失败或目录不一致 -> 取并集，来源标 proxy_show_partial（R-15：
            宁可多给可见的错误，不可少给不可见的错误）；
          - 全部成功且一致 -> proxy_show。
        """
        instance_id = str(instance.get("instance_id") or "").strip()
        endpoints = self._proxy_endpoints(instance)
        if not endpoints:
            raise ZKImportPreparationError("NO_AVAILABLE_PROXY", "未发现可用 Proxy 地址")
        excluded = set(SYSTEM_DATABASES)
        if monitor_db:
            excluded.add(monitor_db.strip().lower())
        catalogues: list[set[str]] = []
        failed = 0
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
                failed += 1
                logger.warning(
                    "ZK_IMPORT_BUSINESS_PROXY_FAILED instance=%s endpoint=%s:%s error_type=%s",
                    instance_id, host, port, type(exc).__name__,
                )
                continue
            catalogues.append({name for name in values if name and name.lower() not in excluded})
        if not catalogues:
            raise ZKImportPreparationError("NO_AVAILABLE_PROXY", "全部 Proxy 均无法枚举业务库")
        union = set().union(*catalogues)
        if not union:
            raise ZKImportPreparationError("NO_BUSINESS_DATABASE", "未发现可导入的业务库")
        inconsistent = any(values != catalogues[0] for values in catalogues[1:])
        if failed or inconsistent:
            logger.warning(
                "ZK_IMPORT_DATABASES_DEGRADED instance=%s proxies=%s failed=%s inconsistent=%s 取并集",
                instance_id, len(endpoints), failed, inconsistent,
            )
            return sorted(union, key=lambda item: (item.lower(), item)), "proxy_show_partial"
        return sorted(union, key=lambda item: (item.lower(), item)), "proxy_show"

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
        name_overrides: Optional[dict] = None,
        manual_databases: Optional[dict] = None,
        hint: str = "",
    ) -> list[dict]:
        """只读预检，返回可展示的候选项或逐实例失败项。

        v1.6.0.3：名称解析走五级解析链（手工覆盖 > 扫描期富集 > 链式解析）；
        业务库支持手工兜底（manual_databases）与扫描期富集复用。
        """
        from backend.services.zk_name_resolution_service import zk_name_resolution_service

        name_overrides = name_overrides or {}
        manual_databases = manual_databases or {}
        rows: list[dict] = []
        monitor_conn = None
        try:
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
                    # ── 实例名称：手工覆盖 > 扫描富集 > 五级解析链 ──
                    override_name = str(name_overrides.get(instance_id) or "").strip()
                    if override_name:
                        instance_name, name_source = override_name, "manual"
                    elif str(instance.get("resolved_name") or "").strip():
                        instance_name = str(instance["resolved_name"]).strip()
                        name_source = str(instance.get("name_source") or "scan_enrich")
                    else:
                        if monitor_conn is None and not str(monitor.host or "").strip():
                            raise ZKImportPreparationError("MONITOR_CONNECT_FAILED", "未提供 MonitorDB 配置，无法解析实例名称")
                        if monitor_conn is None:
                            try:
                                monitor_conn = self._connect(
                                    monitor.host, monitor.port, monitor.username,
                                    monitor.password, monitor.database)
                            except ZKImportPreparationError as exc:
                                # A-P2-02：MonitorDB 连不上归一到既有语义，逐实例可读，
                                # 不再裸抛 OperationalError 把整批预检掀翻成 500。
                                raise ZKImportPreparationError(
                                    "MONITOR_CONNECT_FAILED",
                                    f"无法连接 MonitorDB {monitor.host}:{monitor.port}，"
                                    "请核对主机、端口、账号、口令与网络") from exc
                        instance_name, name_source, _detail = zk_name_resolution_service.resolve(
                            monitor_conn, instance_id, set_ids,
                            str(instance.get("instance_kind") or ""), hint=hint,
                            zk_name_fields=instance.get("zk_name_fields"))
                        if not instance_name:
                            raise ZKImportPreparationError("INSTANCE_NAME_UNRESOLVED",
                                                           "五级解析链均未命中，可在导入弹窗手工命名或跑 name-diagnose 固化模式")
                except ZKImportPreparationError as exc:
                    # 名称阶段失败：错误行不带名称；库阶段失败在下方保留已解析名称
                    logger.info("ZK_IMPORT_PREVIEW_ITEM_FAILED instance=%s code=%s", instance_id, exc.code)
                    rows.append({
                        **base,
                        "resolved_instance_name": "",
                        "name_source": "",
                        "databases_source": "",
                        "database": "",
                        "generated_connection_name": "",
                        "status": "error",
                        "failure_code": exc.code,
                        "failure_detail": exc.detail,
                    })
                    continue
                try:
                    # ── 业务库：手工兜底 > 扫描富集 > Proxy 枚举 ──
                    manual_dbs = [str(d).strip() for d in (manual_databases.get(instance_id) or [])
                                  if str(d).strip()]
                    if manual_dbs:
                        databases, databases_source = manual_dbs, "manual"
                    elif instance.get("business_dbs"):
                        databases = [str(d) for d in instance["business_dbs"]]
                        databases_source = str(instance.get("databases_source") or "scan_enrich")
                    else:
                        databases, databases_source = self._list_business_databases(
                            instance, business, monitor.database)
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
                            "databases_source": databases_source,
                            "database": database,
                            "generated_connection_name": generated_name,
                            "status": "ready",
                            "failure_code": "",
                            "failure_detail": "",
                        })
                except ZKImportPreparationError as exc:
                    # 库阶段失败：保留已解析名称，便于 UI 展示与手工兜底
                    logger.info("ZK_IMPORT_PREVIEW_ITEM_FAILED instance=%s code=%s", instance_id, exc.code)
                    rows.append({
                        **base,
                        "resolved_instance_name": instance_name,
                        "name_source": name_source,
                        "databases_source": "",
                        "database": "",
                        "generated_connection_name": "",
                        "status": "error",
                        "failure_code": exc.code,
                        "failure_detail": exc.detail,
                    })
        finally:
            if monitor_conn is not None:
                try:
                    monitor_conn.close()
                except Exception:
                    pass
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
        preview_total: int = 0,
    ) -> dict:
        """在一个元库事务中创建连接和导入审计；有冲突时零连接写入。

        preview_total 为本次预览生成的候选行总数（含冲突/失败行），
        落库到批次审计的 candidate_count，保证"预览全量 vs 实际提交"可对账。
        """
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
            # 事务内再检查：尽早拦截可感知的冲突。注意 REPEATABLE READ 下这是
            # 非锁定一致性读，并发事务可能双双通过——最终防线是表上的唯一约束
            # uq_conn_name / uq_conn_endpoint（v9/090 迁移），重复键在下方转
            # IMPORT_CONFLICT，保持"零连接写入"语义（P2-01）。
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
                 max(preview_total, len(selected)), len(selected)),
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
                    "generated_connection_name, connection_id, result_status, name_source, databases_source, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, NOW())",
                    (
                        batch_id, row["source_instance_id"], row["instance_kind"], row["instance_type"],
                        row["primary_proxy_host"], row["primary_proxy_port"], ",".join(row["set_ids"]),
                        row["resolved_instance_name"], row["database"], row["generated_connection_name"],
                        connection_id, str(row.get("name_source") or "")[:32],
                        str(row.get("databases_source") or "")[:32],
                    ),
                )
                created.append({"id": connection_id, "name": row["generated_connection_name"], "database": row["database"]})
            conn.commit()
        except ZKImportCommitError:
            conn.rollback()
            raise
        except pymysql.err.IntegrityError as exc:
            # P2-01：唯一约束（uq_conn_name/uq_conn_endpoint）拦下的并发重复写入。
            # 事务内预检 SELECT 为非锁定读，两位操作者可能同时通过检查，
            # 此处由数据库兜底并归一到既有冲突语义，保持"零连接写入"。
            conn.rollback()
            logger.warning("ZK_IMPORT_COMMIT_DUPLICATE_KEY error=%s", type(exc).__name__)
            raise ZKImportCommitError("IMPORT_CONFLICT", "提交期间发现既有连接，未创建任何连接") from exc
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

    def record_failed_batch(self, selected: list[dict], operator: str, discovery_id: str,
                            code: str, preview_total: int = 0) -> None:
        """提交失败时在独立短事务登记 status='failed' 批次（v1.6.0.1 修复 P4）。

        主事务回滚后失败导入不留任何审计痕迹，与合规可追溯目标相悖；
        本方法只写错误码、数量与实例 ID——不含任何口令、密文或连接串。
        登记失败本身不得再向上抛错（审计是尽力而为，不能掩盖原始提交错误）。
        """
        try:
            from backend.services.database import _get_connection, ensure_db

            ensure_db()
            instances = sorted({str(row.get("source_instance_id") or "") for row in selected})
            summary = f"code={code};selected={len(selected)};instances={','.join(instances)}"[:1000]
            conn = _get_connection()
            try:
                conn.execute(
                    "INSERT INTO zk_discovery_import_batches "
                    "(id, discovery_id, operator_username, selected_instance_count, candidate_count, "
                    "created_count, skipped_count, failed_count, status, failure_summary, created_at, completed_at) "
                    "VALUES (?, ?, ?, ?, ?, 0, 0, ?, 'failed', ?, NOW(), NOW())",
                    (uuid.uuid4().hex, discovery_id, operator, len(instances),
                     max(preview_total, len(selected)), len(selected), summary),
                )
                conn.commit()
                logger.warning("ZK_IMPORT_FAILED_BATCH_RECORDED operator=%s code=%s selected=%s",
                               operator, code, len(selected))
            finally:
                conn.close()
        except Exception:
            logger.exception("ZK_IMPORT_FAILED_BATCH_RECORD_ERROR operator=%s code=%s", operator, code)


zk_connection_import_service = ZKConnectionImportService()
