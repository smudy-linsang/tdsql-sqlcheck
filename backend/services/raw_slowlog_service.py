"""原始慢日志采集的应用服务与持久化边界。

本模块不读取 TDSQL 数据库、不复用 scan_tasks/slow_queries；所有事件只来自
受限导出器返回的 Proxy/Gateway 原始慢日志完整块。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import socket
import time
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.services.database import _get_connection, ensure_db, log_operation
from backend.services.raw_slowlog_parser import (
    PARSER_PROFILE,
    PARSE_VERSION,
    make_anchor,
    parse_incremental_chunk,
    verify_anchor,
)
from backend.services.raw_slowlog_ssh import RawSlowLogSSHClient, RawSlowLogSSHError


logger = logging.getLogger("tdsql.raw_slowlog")
_SOURCE_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_NODE_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_HOLDER_ID = f"{socket.gethostname()}:{__import__('os').getpid()}"
_EXPORTER_PROTOCOL = "raw_slowlog_exporter_v1"
_EXPORTER_VERSION_RE = re.compile(r"^1\.\d+\.\d+\.\d+$")


class RawSlowLogValidationError(ValueError):
    pass


class RawSlowLogNotFoundError(LookupError):
    pass


class RawSlowLogBusyError(RuntimeError):
    """同一采集源已有未过期租约，禁止并发读取。"""


def _now() -> datetime:
    return datetime.now()


def _rows(rows: Iterable[dict]) -> list[dict]:
    return [dict(row) for row in rows]


def _safe_error(exc: Exception) -> tuple[str, str]:
    """API/运行表不保存路径、主机或原始 SQL；完整诊断仅写本地日志。"""
    logger.warning("原始慢日志运行失败: %s", exc, exc_info=True)
    message = str(exc)
    if isinstance(exc, RawSlowLogSSHError):
        code = "E5021" if message.startswith("E5021:") else "E5020"
        return code, "SSH 导出器通信或协议校验失败，请查看受控服务日志。"
    if message.startswith("E5022:"):
        return "E5022", "日志文件锚点校验失败，已停止该节点读取。"
    if message.startswith("E4223:"):
        return "E4223", "日志格式解析失败，已停止该节点读取，等待格式准入处理。"
    if isinstance(exc, RawSlowLogValidationError):
        return "validation_error", str(exc)[:240]
    return "collection_error", "采集处理失败，请查看受控服务日志。"


def _mask_value(value: str) -> str:
    return "已配置" if value else "未配置"


class RawSlowLogService:
    def __init__(self, ssh_client: RawSlowLogSSHClient | None = None):
        self.ssh_client = ssh_client or RawSlowLogSSHClient()

    # ── 查询与 RBAC 视图 ──────────────────────────────────────────────
    def _load_source(self, source_id: int, conn=None) -> dict:
        own_conn = conn is None
        conn = conn or _get_connection()
        try:
            source = conn.execute("SELECT * FROM slow_log_sources WHERE id = ?", (source_id,)).fetchone()
            if not source:
                raise RawSlowLogNotFoundError("原始慢日志采集源不存在")
            result = dict(source)
            result["nodes"] = _rows(conn.execute(
                "SELECT * FROM slow_log_source_nodes WHERE source_id = ? ORDER BY id", (source_id,)).fetchall())
            return result
        finally:
            if own_conn:
                conn.close()

    @staticmethod
    def _public_source(source: dict, role: str, detail: bool = False) -> dict:
        item = {key: value for key, value in source.items() if key != "nodes"}
        if not item.get("enabled"):
            item["health_status"] = "disabled"
        elif item.get("last_error_code"):
            item["health_status"] = "degraded" if item.get("last_success_at") else "failed"
        elif not item.get("last_success_at"):
            item["health_status"] = "degraded"
        elif int(item.get("last_backlog_bytes") or 0) > 0 or (
            item.get("last_lag_seconds") is not None
            and int(item["last_lag_seconds"]) >= int(item.get("lag_alert_seconds") or 600)
        ):
            item["health_status"] = "degraded"
        else:
            try:
                last_success = item["last_success_at"]
                if isinstance(last_success, str):
                    last_success = datetime.fromisoformat(last_success)
                stale_after = max(3 * int(item.get("poll_interval_seconds") or 60), 300)
                item["health_status"] = "degraded" if (_now() - last_success).total_seconds() > stale_after else "healthy"
            except (TypeError, ValueError):
                item["health_status"] = "degraded"
        is_admin = role == "admin"
        if not is_admin:
            item["credential_ref"] = _mask_value(str(item.get("credential_ref", "")))
            item["known_hosts_ref"] = _mask_value(str(item.get("known_hosts_ref", "")))
        nodes = []
        for node in source.get("nodes", []):
            result = dict(node)
            if not is_admin:
                result["ssh_host"] = _mask_value(str(node.get("ssh_host", "")))
                result["host_key_alias"] = _mask_value(str(node.get("host_key_alias", "")))
                result["declared_path_template"] = _mask_value(str(node.get("declared_path_template", "")))
                result["ssh_host_key_fingerprint"] = _mask_value(str(node.get("ssh_host_key_fingerprint", "")))
            nodes.append(result)
        if detail:
            item["nodes"] = nodes
        else:
            item["node_count"] = len(nodes)
            item["nodes_enabled"] = sum(1 for n in nodes if n.get("enabled"))
        return item

    def list_sources(self, role: str) -> list[dict]:
        ensure_db()
        conn = _get_connection()
        try:
            ids = conn.execute("SELECT id FROM slow_log_sources ORDER BY id DESC").fetchall()
            return [self._public_source(self._load_source(row["id"], conn), role) for row in ids]
        finally:
            conn.close()

    def get_source(self, source_id: int, role: str) -> dict:
        ensure_db()
        return self._public_source(self._load_source(source_id), role, detail=True)

    # ── 配置校验与写入 ────────────────────────────────────────────────
    @staticmethod
    def _validate_source_payload(payload: dict, updating: bool = False) -> None:
        if not isinstance(payload, dict):
            raise RawSlowLogValidationError("请求体必须为对象")
        if not updating:
            if not _SOURCE_KEY_RE.fullmatch(str(payload.get("source_key", ""))):
                raise RawSlowLogValidationError("source_key 格式不正确")
        if payload.get("transport", "ssh_exporter_v1") != "ssh_exporter_v1":
            raise RawSlowLogValidationError("本版本仅支持 ssh_exporter_v1")
        if not str(payload.get("connection_id", "")).strip() or not str(payload.get("display_name", "")).strip():
            raise RawSlowLogValidationError("connection_id 和 display_name 为必填项")
        if not str(payload.get("credential_ref", "")).strip() or not str(payload.get("known_hosts_ref", "")).strip():
            raise RawSlowLogValidationError("credential_ref 和 known_hosts_ref 为必填的部署机引用名")
        if not _REF_RE.fullmatch(str(payload.get("credential_ref", ""))) or not _REF_RE.fullmatch(str(payload.get("known_hosts_ref", ""))):
            raise RawSlowLogValidationError("凭据引用名和 known_hosts 引用名格式不正确")
        try:
            ZoneInfo(str(payload.get("timezone", "Asia/Shanghai")))
        except ZoneInfoNotFoundError as exc:
            raise RawSlowLogValidationError("timezone 必须为有效的 IANA 时区") from exc
        for key, minimum, maximum in (
            ("poll_interval_seconds", 30, 600), ("max_batch_bytes", 64 * 1024, 16 * 1024 * 1024),
            ("max_events_per_batch", 1, 10000), ("max_run_seconds", 5, 120),
            ("lag_alert_seconds", 60, 3600), ("min_query_time_ms", 0, 3600000),
        ):
            if key in payload and not minimum <= int(payload[key]) <= maximum:
                raise RawSlowLogValidationError(f"{key} 超出允许范围")
        if payload.get("initial_position", "tail") not in {"tail", "lookback"}:
            raise RawSlowLogValidationError("initial_position 仅允许 tail 或 lookback")
        if payload.get("initial_position") == "lookback" and not 60 <= int(payload.get("initial_lookback_seconds", 0)) <= 86400:
            raise RawSlowLogValidationError("lookback 模式的 initial_lookback_seconds 必须在 60~86400")
        nodes = payload.get("nodes", [])
        if not isinstance(nodes, list) or not nodes:
            raise RawSlowLogValidationError("至少配置一个采集节点")
        known = set()
        for node in nodes:
            key = str(node.get("node_key", ""))
            if not _NODE_KEY_RE.fullmatch(key) or key in known:
                raise RawSlowLogValidationError("node_key 格式不正确或重复")
            known.add(key)
            if not str(node.get("display_name", "")).strip():
                raise RawSlowLogValidationError("节点 display_name 为必填项")
            if not str(node.get("ssh_host", "")).strip() or not str(node.get("host_key_alias", "")).strip():
                raise RawSlowLogValidationError("节点 SSH 主机和主机密钥别名为必填项")
            if not 1 <= int(node.get("ssh_port", 22)) <= 65535:
                raise RawSlowLogValidationError("节点 SSH 端口不正确")
            if not _SOURCE_KEY_RE.fullmatch(str(node.get("remote_source_key", ""))) or not str(node.get("declared_path_template", "")).strip():
                raise RawSlowLogValidationError("远端源键和日志路径声明为必填项")
            path_template = str(node["declared_path_template"])
            if not path_template.startswith("/"):
                raise RawSlowLogValidationError("日志路径声明必须为 Linux 绝对路径")
            if any(char.isspace() or char in ";|&$`\\" for char in path_template):
                raise RawSlowLogValidationError("日志路径声明包含不允许的控制或命令字符")
            if node.get("parser_profile") != PARSER_PROFILE:
                raise RawSlowLogValidationError(f"本版本仅支持 {PARSER_PROFILE}")

    @staticmethod
    def _source_values(payload: dict) -> tuple:
        defaults = {
            "transport": "ssh_exporter_v1", "timezone": "Asia/Shanghai", "poll_interval_seconds": 60,
            "max_batch_bytes": 8 * 1024 * 1024, "max_events_per_batch": 2000, "max_run_seconds": 25,
            "lag_alert_seconds": 600, "initial_position": "tail", "initial_lookback_seconds": 300,
            "min_query_time_ms": 1000, "credential_ref": "", "known_hosts_ref": "",
        }
        value = {**defaults, **payload}
        return tuple(value[key] for key in (
            "source_key", "connection_id", "display_name", "transport", "timezone", "poll_interval_seconds",
            "max_batch_bytes", "max_events_per_batch", "max_run_seconds", "lag_alert_seconds", "initial_position",
            "initial_lookback_seconds", "min_query_time_ms", "credential_ref", "known_hosts_ref",
        ))

    @staticmethod
    def _insert_nodes(conn, source_id: int, nodes: list[dict]) -> None:
        for node in nodes:
            conn.execute("""
                INSERT INTO slow_log_source_nodes
                (source_id,node_key,display_name,ssh_host,ssh_port,host_key_alias,remote_source_key,
                 declared_path_template,parser_profile,enabled)
                VALUES (?,?,?,?,?,?,?,?,?,0)
            """, (source_id, node["node_key"], node["display_name"], node["ssh_host"],
                  int(node.get("ssh_port", 22)), node["host_key_alias"], node["remote_source_key"],
                  node["declared_path_template"], node["parser_profile"]))

    def create_source(self, payload: dict, operator: str) -> dict:
        self._validate_source_payload(payload)
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute("""
                INSERT INTO slow_log_sources
                (source_key,connection_id,display_name,transport,timezone,poll_interval_seconds,max_batch_bytes,
                 max_events_per_batch,max_run_seconds,lag_alert_seconds,initial_position,initial_lookback_seconds,
                 min_query_time_ms,credential_ref,known_hosts_ref,enabled,created_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (*self._source_values(payload), 0, operator))
            source_id = conn.execute("SELECT LAST_INSERT_ID() AS id").fetchone()["id"]
            self._insert_nodes(conn, source_id, payload["nodes"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        log_operation(operator, "raw_slowlog_source_create", "raw_slowlog", str(source_id), "创建禁用采集源")
        return self.get_source(source_id, "admin")

    def update_source(self, source_id: int, payload: dict, operator: str) -> dict:
        self._validate_source_payload(payload, updating=True)
        ensure_db()
        conn = _get_connection()
        try:
            old = self._load_source(source_id, conn)
            if "source_key" in payload and payload["source_key"] != old["source_key"]:
                raise RawSlowLogValidationError("source_key 创建后不可修改")
            merged = {**old, **payload}
            conn.execute("""
                UPDATE slow_log_sources SET connection_id=?,display_name=?,transport=?,timezone=?,
                poll_interval_seconds=?,max_batch_bytes=?,max_events_per_batch=?,max_run_seconds=?,
                lag_alert_seconds=?,initial_position=?,initial_lookback_seconds=?,min_query_time_ms=?,
                credential_ref=?,known_hosts_ref=?,enabled=0,last_error_code='',last_error_detail=''
                WHERE id=?
            """, (merged["connection_id"], merged["display_name"], merged.get("transport", "ssh_exporter_v1"),
                  merged.get("timezone", "Asia/Shanghai"), int(merged.get("poll_interval_seconds", 60)),
                  int(merged.get("max_batch_bytes", 8 * 1024 * 1024)), int(merged.get("max_events_per_batch", 2000)),
                  int(merged.get("max_run_seconds", 25)), int(merged.get("lag_alert_seconds", 600)),
                  merged.get("initial_position", "tail"), int(merged.get("initial_lookback_seconds", 300)),
                  int(merged.get("min_query_time_ms", 1000)), merged.get("credential_ref", ""),
                  merged.get("known_hosts_ref", ""), source_id))
            conn.execute("DELETE FROM slow_log_source_nodes WHERE source_id = ?", (source_id,))
            self._insert_nodes(conn, source_id, payload["nodes"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        log_operation(operator, "raw_slowlog_source_update", "raw_slowlog", str(source_id), "更新配置并自动停用")
        return self.get_source(source_id, "admin")

    # ── Probe 与启停 ──────────────────────────────────────────────────
    def _create_run(self, conn, source_id: int, trigger_type: str, operator: str, nodes_total: int) -> int:
        conn.execute("""
            INSERT INTO slow_log_collection_runs
            (source_id,trigger_type,requested_by,status,started_at,nodes_total,error_detail)
            VALUES (?,?,?,?,?,?,?)
        """, (source_id, trigger_type, operator, "running", _now(), nodes_total, ""))
        return conn.execute("SELECT LAST_INSERT_ID() AS id").fetchone()["id"]

    def _finish_run(self, run_id: int, status: str, counters: dict[str, int], error_code: str = "", error_detail: str = "") -> None:
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute("""
                UPDATE slow_log_collection_runs SET status=?,finished_at=?,nodes_success=?,files_seen=?,bytes_read=?,
                blocks_parsed=?,events_inserted=?,events_duplicate=?,events_filtered=?,incomplete_tail_count=?,
                parse_error_count=?,error_code=?,error_detail=? WHERE id=?
            """, (status, _now(), counters.get("nodes_success", 0), counters.get("files_seen", 0),
                  counters.get("bytes_read", 0), counters.get("blocks_parsed", 0), counters.get("events_inserted", 0),
                  counters.get("events_duplicate", 0), counters.get("events_filtered", 0),
                  counters.get("incomplete_tail_count", 0), counters.get("parse_error_count", 0),
                  error_code, error_detail[:1024], run_id))
            conn.commit()
        finally:
            conn.close()

    def _known_host_fingerprint(self, source: dict) -> str:
        paths = self.ssh_client.secret_resolver.resolve(source["credential_ref"], source["known_hosts_ref"])
        return hashlib.sha256(paths.known_hosts.read_bytes()).hexdigest()

    def _probe_node(self, source: dict, node: dict, conn) -> int:
        messages = self.ssh_client.request(
            node, source,
            {"op": "probe", "protocol": _EXPORTER_PROTOCOL, "source_key": node["remote_source_key"]},
            timeout_seconds=min(int(source["max_run_seconds"]), 30),
        )
        probe = next((message for message in messages if message.get("type") == "probe"), None)
        if (not probe or probe.get("protocol") != _EXPORTER_PROTOCOL
                or probe.get("source_key") != node["remote_source_key"]
                or not _EXPORTER_VERSION_RE.fullmatch(str(probe.get("version", "")))):
            raise RawSlowLogValidationError("远端导出器 probe 响应与已保存配置不一致")
        files = probe.get("files")
        if not isinstance(files, list) or not files:
            raise RawSlowLogValidationError("未发现可采集的慢日志文件")
        signature = probe.get("format_signature")
        if not isinstance(signature, dict) or signature.get("parser_profile") != PARSER_PROFILE \
                or signature.get("time_header") is not True or signature.get("query_time_header") is not True:
            raise RawSlowLogValidationError("Probe 未通过慢日志格式门禁（必须识别 # Time 与 # Query_time）")
        host_fingerprint = self._known_host_fingerprint(source)
        storage_identity = str(probe.get("storage_identity", ""))[:256]
        for item in files:
            identity = str(item.get("file_identity", ""))[:256]
            if not identity:
                raise RawSlowLogValidationError("probe 返回了缺少文件身份的记录")
            conflict = conn.execute("""
                SELECT pf.source_node_id FROM slow_log_node_probe_files pf
                JOIN slow_log_source_nodes n ON n.id=pf.source_node_id
                WHERE pf.source_node_id<>? AND n.enabled=1 AND pf.file_identity=?
                  AND ((pf.ssh_host_key_fingerprint=? AND ?<>'') OR (pf.storage_identity=? AND ?<>''))
                LIMIT 1
            """, (node["id"], identity, host_fingerprint, host_fingerprint, storage_identity, storage_identity)).fetchone()
            if conflict:
                raise RawSlowLogValidationError("日志文件已被其他已启用节点采集，禁止重复启用")
            conn.execute("""
                INSERT INTO slow_log_node_probe_files
                (source_node_id,ssh_host_key_fingerprint,storage_identity,file_identity,file_label,observed_at)
                VALUES (?,?,?,?,?,?)
                ON DUPLICATE KEY UPDATE ssh_host_key_fingerprint=VALUES(ssh_host_key_fingerprint),
                    storage_identity=VALUES(storage_identity),file_label=VALUES(file_label),observed_at=VALUES(observed_at)
            """, (node["id"], host_fingerprint, storage_identity, identity, str(item.get("file_label", ""))[:512], _now()))
        conn.execute("""
            UPDATE slow_log_source_nodes SET last_probe_at=?,last_probe_status='passed',last_probe_detail=?,
            ssh_host_key_fingerprint=? WHERE id=?
        """, (_now(), "已验证 # Time/# Query_time 格式签名", host_fingerprint, node["id"]))
        return len(files)

    def probe_source(self, source_id: int, operator: str) -> dict:
        ensure_db()
        conn = _get_connection()
        try:
            source = self._load_source(source_id, conn)
            run_id = self._create_run(conn, source_id, "probe", operator, len(source["nodes"]))
            conn.commit()
        finally:
            conn.close()
        counters = {"nodes_success": 0, "files_seen": 0}
        failure: Exception | None = None
        for node in source["nodes"]:
            conn = _get_connection()
            try:
                counters["files_seen"] += self._probe_node(source, node, conn)
                conn.commit()
                counters["nodes_success"] += 1
            except Exception as exc:
                conn.rollback()
                code, detail = _safe_error(exc)
                conn.execute("UPDATE slow_log_source_nodes SET last_probe_at=?,last_probe_status='failed',last_probe_detail=? WHERE id=?",
                             (_now(), detail[:512], node["id"]))
                conn.commit()
                failure = failure or exc
            finally:
                conn.close()
        code, detail = _safe_error(failure) if failure else ("", "")
        probe_status = "completed" if not failure else ("failed" if counters["nodes_success"] == 0 else "partial_failed")
        self._finish_run(run_id, probe_status, counters, code, detail)
        log_operation(operator, "raw_slowlog_probe", "raw_slowlog", str(source_id), f"run_id={run_id}")
        return self.get_run(run_id, "admin")

    def set_enabled(self, source_id: int, enabled: bool, operator: str) -> dict:
        ensure_db()
        conn = _get_connection()
        try:
            source = self._load_source(source_id, conn)
            if enabled:
                nodes = source["nodes"]
                if not nodes or any(node.get("last_probe_status") != "passed" for node in nodes):
                    raise RawSlowLogValidationError("所有节点均须先通过 Probe 才能启用")
                conn.execute("UPDATE slow_log_source_nodes SET enabled=1 WHERE source_id=?", (source_id,))
            conn.execute("UPDATE slow_log_sources SET enabled=? WHERE id=?", (1 if enabled else 0, source_id))
            conn.commit()
        finally:
            conn.close()
        log_operation(operator, "raw_slowlog_enable" if enabled else "raw_slowlog_disable", "raw_slowlog", str(source_id), "")
        return self.get_source(source_id, "admin")

    # ── 游标、入库与采集 ──────────────────────────────────────────────
    def _acquire_source_lease(self, source_id: int) -> bool:
        conn = _get_connection()
        try:
            affected = conn.execute("""
                UPDATE slow_log_sources SET lease_holder=?,lease_expires_at=DATE_ADD(NOW(),INTERVAL 5 MINUTE)
                WHERE id=? AND (lease_expires_at IS NULL OR lease_expires_at<NOW() OR lease_holder=?)
            """, (_HOLDER_ID, source_id, _HOLDER_ID)).rowcount
            conn.commit()
            return affected == 1
        finally:
            conn.close()

    def _release_source_lease(self, source_id: int) -> None:
        conn = _get_connection()
        try:
            conn.execute("UPDATE slow_log_sources SET lease_holder='',lease_expires_at=NULL WHERE id=? AND lease_holder=?", (source_id, _HOLDER_ID))
            conn.commit()
        finally:
            conn.close()

    def _renew_source_lease(self, source_id: int) -> bool:
        conn = _get_connection()
        try:
            count = conn.execute("""
                UPDATE slow_log_sources SET lease_expires_at=DATE_ADD(NOW(),INTERVAL 5 MINUTE)
                WHERE id=? AND lease_holder=?
            """, (source_id, _HOLDER_ID)).rowcount
            conn.commit()
            if count == 1:
                return True
            # MySQL reports rowcount=0 when renewal occurs in the same second
            # and the DATETIME value is unchanged; that is still our valid
            # lease, not a failed compare-and-set.
            row = conn.execute("SELECT lease_holder,lease_expires_at FROM slow_log_sources WHERE id=?", (source_id,)).fetchone()
            return bool(row and row["lease_holder"] == _HOLDER_ID and row["lease_expires_at"] and str(row["lease_expires_at"]) > _now().isoformat())
        finally:
            conn.close()

    @staticmethod
    def _load_cursors(conn, node_id: int) -> list[dict]:
        return _rows(conn.execute("SELECT * FROM slow_log_cursors WHERE source_node_id=? AND status='active'", (node_id,)).fetchall())

    @staticmethod
    def _upsert_cursor(conn, node_id: int, file_identity: str, generation: int, file_label: str,
                       offset: int, file_size: int, anchor: dict, event_time: datetime | None) -> None:
        conn.execute("""
            INSERT INTO slow_log_cursors
            (source_node_id,file_identity,generation,file_label,cursor_offset,last_file_size,anchor_start_offset,
             anchor_length,anchor_sha256,last_event_time,status)
            VALUES (?,?,?,?,?,?,?,?,?,?, 'active')
            ON DUPLICATE KEY UPDATE file_label=VALUES(file_label),cursor_offset=VALUES(cursor_offset),
             last_file_size=VALUES(last_file_size),anchor_start_offset=VALUES(anchor_start_offset),
             anchor_length=VALUES(anchor_length),anchor_sha256=VALUES(anchor_sha256),
             last_event_time=COALESCE(VALUES(last_event_time),last_event_time),status='active'
        """, (node_id, file_identity, generation, file_label, offset, file_size,
              anchor["anchor_start_offset"], anchor["anchor_length"], anchor["anchor_sha256"], event_time))

    @staticmethod
    def _reset_cursor_generation(conn, cursor: dict) -> None:
        conn.execute("UPDATE slow_log_cursors SET status='superseded' WHERE id=?", (cursor["id"],))
        RawSlowLogService._upsert_cursor(
            conn, cursor["source_node_id"], cursor["file_identity"], int(cursor["generation"]) + 1,
            cursor.get("file_label", ""), 0, 0, make_anchor(b"", 0), None,
        )

    def _store_chunk(self, source: dict, node: dict, cursor_map: dict[str, dict], message: dict, counters: dict[str, int]) -> dict[str, Any]:
        if message.get("protocol") != _EXPORTER_PROTOCOL or message.get("source_key") != node["remote_source_key"]:
            raise RawSlowLogValidationError("导出器 chunk 协议或采集源标识不匹配")
        identity = str(message.get("file_identity", ""))[:256]
        label = str(message.get("file_label", ""))[:512]
        if not identity or not isinstance(message.get("data_base64"), str):
            raise RawSlowLogValidationError("导出器 chunk 缺少文件身份或数据")
        try:
            raw = base64.b64decode(message["data_base64"], validate=True)
        except Exception as exc:
            raise RawSlowLogValidationError("导出器返回无效 base64 数据") from exc
        if len(raw) > int(source["max_batch_bytes"]):
            raise RawSlowLogValidationError("导出器返回的数据块超过已配置的最大字节数")
        cursor = cursor_map.get(identity)
        offset = int(message.get("offset", cursor.get("cursor_offset", 0) if cursor else 0))
        if cursor and int(cursor["cursor_offset"]) != offset:
            raise RawSlowLogValidationError("导出器读取偏移与保存游标不一致")
        if cursor and cursor.get("anchor_sha256"):
            try:
                prior = base64.b64decode(str(message.get("pre_anchor_base64", "")), validate=True)
            except Exception as exc:
                raise RawSlowLogValidationError("导出器返回无效游标锚点") from exc
            if not verify_anchor(prior, cursor["anchor_sha256"]):
                conn = _get_connection()
                try:
                    self._reset_cursor_generation(conn, cursor)
                    conn.commit()
                finally:
                    conn.close()
                raise RawSlowLogValidationError("E5022: 日志文件锚点变化，已建立新代际，将于下轮从安全起点重读")
        generation = int(cursor["generation"]) if cursor else 0
        chunk = parse_incremental_chunk(raw, offset, source["timezone"])
        counters["files_seen"] += 1
        counters["bytes_read"] += len(raw)
        counters["blocks_parsed"] += len(chunk.complete_blocks)
        counters["parse_error_count"] += len(chunk.parse_errors)
        counters["incomplete_tail_count"] += 1 if chunk.incomplete_tail_start is not None else 0
        if chunk.parse_errors:
            raise RawSlowLogValidationError("E4223: 检测到未获准的慢日志格式块")
        if not raw and cursor:
            next_offset = int(message.get("next_offset", offset))
            if next_offset != offset:
                raise RawSlowLogValidationError("导出器在空数据块中移动了游标")
            file_size = int(message.get("file_size", offset))
            return {"progress": 0, "backlog": max(0, file_size - offset),
                    "complete": bool(message.get("eof")) and offset >= file_size,
                    "oldest_unread_event_time": None}
        conn = _get_connection()
        try:
            last_event_time = None
            processed_blocks = 0
            processed_end = offset
            max_events = int(source["max_events_per_batch"])
            for block in chunk.complete_blocks:
                # This is a per-protocol-batch ceiling. Stop precisely at a
                # complete boundary so the following batch can safely reread
                # remaining blocks; never discard data merely to honor a cap.
                if processed_blocks >= max_events:
                    break
                processed_blocks += 1
                processed_end = block.offset_end
                if block.query_time_us < int(source["min_query_time_ms"]) * 1000:
                    counters["events_filtered"] += 1
                    continue
                rowcount = conn.execute("""
                    INSERT IGNORE INTO slow_log_events
                    (source_id,source_node_id,origin_file_identity,origin_generation,origin_offset_start,origin_offset_end,
                     event_time,event_time_source,db_name,client_user,client_host,backend_host,thread_id,query_time_us,
                     lock_time_us,rows_sent,rows_examined,statement_type,sql_fingerprint,sql_template,
                     sql_template_truncated,sql_template_original_bytes,parse_version,extra_json,collected_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (source["id"], node["id"], identity, generation, block.offset_start, block.offset_end,
                      block.event_time, "proxy_log_time", block.db_name, block.client_user, block.client_host,
                      block.backend_host, block.thread_id, block.query_time_us, block.lock_time_us, block.rows_sent,
                      block.rows_examined, block.statement_type, block.sql.fingerprint, block.sql.stored_template,
                      1 if block.sql.truncated else 0, block.sql.original_bytes, PARSE_VERSION, block.extra_json, _now())).rowcount
                counters["events_inserted" if rowcount else "events_duplicate"] += 1
                last_event_time = block.event_time
            exporter_next_offset = int(message.get("next_offset", chunk.next_safe_offset))
            candidate_offset = processed_end if processed_blocks < len(chunk.complete_blocks) else exporter_next_offset
            # Parser safety boundary wins over exporter EOF: an incomplete or
            # unrecognised block is deliberately reread on the next run.
            next_offset = min(candidate_offset, chunk.next_safe_offset)
            if next_offset < offset or next_offset > offset + len(raw):
                raise RawSlowLogValidationError("导出器返回非法下一游标")
            anchor = make_anchor(b"", 0)
            if next_offset > 0:
                try:
                    post_anchor = base64.b64decode(str(message.get("post_anchor_base64", "")), validate=True)
                except Exception as exc:
                    raise RawSlowLogValidationError("导出器返回无效后置游标锚点") from exc
                expected_anchor_length = min(64, next_offset)
                if len(post_anchor) != expected_anchor_length:
                    raise RawSlowLogValidationError("导出器返回缺失、超长或长度不匹配的后置游标锚点")
                anchor = {
                    "anchor_start_offset": next_offset - len(post_anchor),
                    "anchor_length": len(post_anchor),
                    "anchor_sha256": hashlib.sha256(post_anchor).hexdigest(),
                }
            self._upsert_cursor(conn, node["id"], identity, generation, label, next_offset,
                                int(message.get("file_size", next_offset)), anchor, last_event_time)
            conn.commit()
            file_size = int(message.get("file_size", next_offset))
            oldest_unread = (
                chunk.complete_blocks[processed_blocks].event_time
                if processed_blocks < len(chunk.complete_blocks) else None
            )
            return {"progress": next_offset - offset, "backlog": max(0, file_size - next_offset),
                    "complete": bool(message.get("eof")) and next_offset >= file_size,
                    "oldest_unread_event_time": oldest_unread}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _collect_node(self, source: dict, node: dict, counters: dict[str, int], deadline: float) -> tuple[int, bool, datetime | None]:
        """在单次运行预算内连续拉取协议批次，直到追平或没有安全进度。"""
        backlog_bytes = 0
        budget_exhausted = False
        oldest_unread: datetime | None = None
        while time.monotonic() < deadline:
            conn = _get_connection()
            try:
                cursors = self._load_cursors(conn, node["id"])
            finally:
                conn.close()
            cursor_map = {str(item["file_identity"]): item for item in cursors}
            payload = {
                "op": "pull", "protocol": _EXPORTER_PROTOCOL, "source_key": node["remote_source_key"],
                "max_bytes": int(source["max_batch_bytes"]), "initial_position": source["initial_position"],
                "initial_lookback_seconds": int(source["initial_lookback_seconds"]),
                "timezone": source["timezone"],
                "cursors": [{"file_identity": item["file_identity"], "generation": item["generation"],
                             "offset": item["cursor_offset"], "anchor_start_offset": item["anchor_start_offset"],
                             "anchor_length": item["anchor_length"]} for item in cursors],
            }
            messages = self.ssh_client.request(node, source, payload, max(1, int(deadline - time.monotonic())))
            made_progress = False
            all_complete = True
            backlog_bytes = 0
            for message in messages:
                if time.monotonic() >= deadline:
                    budget_exhausted = True
                    break
                if message.get("type") == "chunk":
                    outcome = self._store_chunk(source, node, cursor_map, message, counters)
                    made_progress = made_progress or int(outcome["progress"]) > 0
                    backlog_bytes += int(outcome["backlog"])
                    candidate = outcome.get("oldest_unread_event_time")
                    if candidate and (oldest_unread is None or candidate < oldest_unread):
                        oldest_unread = candidate
                    all_complete = all_complete and bool(outcome["complete"])
                elif message.get("type") == "error":
                    raise RawSlowLogSSHError("exporter declared an operation error")
            if all_complete or not made_progress:
                break
        if time.monotonic() >= deadline and backlog_bytes > 0:
            budget_exhausted = True
        conn = _get_connection()
        try:
            conn.execute("UPDATE slow_log_source_nodes SET last_success_at=?,last_error_code='',last_error_detail='' WHERE id=?", (_now(), node["id"]))
            conn.commit()
        finally:
            conn.close()
        return backlog_bytes, budget_exhausted, oldest_unread

    @staticmethod
    def _finish_unstarted_run(run_id: int | None, status: str, code: str, detail: str) -> None:
        """让已入队但尚未取得租约的任务也有终态，避免审计表留下 running。"""
        if run_id is None:
            return
        RawSlowLogService()._finish_run(run_id, status, {"nodes_success": 0}, code, detail)

    @staticmethod
    def _sync_backlog_alert(source: dict, backlog_bytes: int, lag_seconds: int | None, budget_exhausted: bool) -> None:
        """将有效积压映射为一条可恢复、可确认的现有告警，不暴露主机、路径或 SQL。"""
        alert_needed = backlog_bytes > 0 and (
            budget_exhausted or (lag_seconds is not None and lag_seconds >= int(source["lag_alert_seconds"]))
        )
        marker = f"原始慢日志采集源 source_id={source['id']}"
        conn = _get_connection()
        try:
            active = conn.execute("""
                SELECT id FROM alerts WHERE connection_id=? AND metric_name='raw_slowlog_backlog'
                  AND status='active' AND message LIKE ? ORDER BY id DESC LIMIT 1
            """, (source["connection_id"], f"{marker}%")).fetchone()
            if alert_needed:
                value = float(lag_seconds if lag_seconds is not None else backlog_bytes)
                message = f"{marker} 存在未读积压：backlog_bytes={backlog_bytes}，lag_seconds={lag_seconds if lag_seconds is not None else 'unknown'}。"
                if active:
                    conn.execute("UPDATE alerts SET metric_value=?,level='WARNING',threshold=?,message=? WHERE id=?",
                                 (value, float(source["lag_alert_seconds"]), message, active["id"]))
                else:
                    conn.execute("""
                        INSERT INTO alerts (connection_id,metric_name,metric_value,level,threshold,message,status)
                        VALUES (?, 'raw_slowlog_backlog', ?, 'WARNING', ?, ?, 'active')
                    """, (source["connection_id"], value, float(source["lag_alert_seconds"]), message))
            elif active:
                conn.execute("UPDATE alerts SET status='resolved',resolved_at=? WHERE id=?", (_now().isoformat(), active["id"]))
            conn.commit()
        finally:
            conn.close()

    def collect_source(self, source_id: int, operator: str = "scheduler", trigger_type: str = "scheduled", run_id: int | None = None) -> dict:
        ensure_db()
        try:
            source = self._load_source(source_id)
        except Exception as exc:
            code, detail = _safe_error(exc)
            self._finish_unstarted_run(run_id, "failed", code, detail)
            if run_id is not None:
                return self.get_run(run_id, "admin")
            raise
        if not source.get("enabled"):
            exc = RawSlowLogValidationError("采集源未启用")
            code, detail = _safe_error(exc)
            self._finish_unstarted_run(run_id, "skipped", code, detail)
            if run_id is not None:
                return self.get_run(run_id, "admin")
            raise exc
        if not self._acquire_source_lease(source_id):
            exc = RawSlowLogBusyError("采集源正在被其他工作者处理")
            self._finish_unstarted_run(run_id, "skipped", "E4091", "采集源已有未过期运行租约。")
            if run_id is not None:
                return self.get_run(run_id, "admin")
            raise exc
        if run_id is None:
            conn = _get_connection()
            try:
                run_id = self._create_run(conn, source_id, trigger_type, operator, len(source["nodes"]))
                conn.commit()
            finally:
                conn.close()
        counters = {key: 0 for key in (
            "nodes_success", "files_seen", "bytes_read", "blocks_parsed", "events_inserted", "events_duplicate",
            "events_filtered", "incomplete_tail_count", "parse_error_count",
        )}
        failure: Exception | None = None
        backlog_bytes = 0
        budget_exhausted = False
        oldest_unread: datetime | None = None
        deadline = time.monotonic() + int(source["max_run_seconds"])
        try:
            for node in source["nodes"]:
                if not node.get("enabled") or time.monotonic() >= deadline:
                    continue
                try:
                    if not self._renew_source_lease(source_id):
                        raise RawSlowLogValidationError("采集源租约续租失败，已停止后续采集")
                    node_backlog, node_budget_exhausted, node_oldest_unread = self._collect_node(source, node, counters, deadline)
                    backlog_bytes += node_backlog
                    budget_exhausted = budget_exhausted or node_budget_exhausted
                    if node_oldest_unread and (oldest_unread is None or node_oldest_unread < oldest_unread):
                        oldest_unread = node_oldest_unread
                    counters["nodes_success"] += 1
                except Exception as exc:
                    code, detail = _safe_error(exc)
                    conn = _get_connection()
                    try:
                        conn.execute("UPDATE slow_log_source_nodes SET last_error_code=?,last_error_detail=? WHERE id=?", (code, detail[:512], node["id"]))
                        conn.commit()
                    finally:
                        conn.close()
                    failure = failure or exc
            conn = _get_connection()
            try:
                status = "completed" if not failure else "partial_failed"
                if counters["nodes_success"] == 0 and failure:
                    status = "failed"
                lag_seconds = (
                    max(0, int((_now() - oldest_unread).total_seconds()))
                    if oldest_unread is not None else None
                )
                conn.execute("""
                    UPDATE slow_log_sources SET last_success_at=?,last_backlog_bytes=?,last_lag_seconds=?,last_error_code=?,last_error_detail=? WHERE id=?
                """, (_now() if counters["nodes_success"] else source.get("last_success_at"), backlog_bytes, lag_seconds,
                      "" if not failure else _safe_error(failure)[0], "" if not failure else _safe_error(failure)[1], source_id))
                conn.commit()
            finally:
                conn.close()
            code, detail = _safe_error(failure) if failure else ("", "")
            self._finish_run(run_id, status, counters, code, detail)
            self._sync_backlog_alert(source, backlog_bytes, lag_seconds, budget_exhausted)
            try:
                from backend.services import metrics_service
                metrics_service.inc("tdsql_raw_slowlog_runs_total", {"status": status})
                metrics_service.inc("tdsql_raw_slowlog_events_inserted_total", {"source": source["source_key"]}, counters["events_inserted"])
                metrics_service.inc("tdsql_raw_slowlog_parse_errors_total", {"source": source["source_key"]}, counters["parse_error_count"])
                metrics_service.set_gauge("tdsql_raw_slowlog_backlog_bytes", backlog_bytes, {"source": source["source_key"]})
                metrics_service.set_gauge("tdsql_raw_slowlog_source_lag_seconds", lag_seconds or 0, {"source": source["source_key"]})
                if budget_exhausted:
                    metrics_service.inc("tdsql_raw_slowlog_run_budget_exhausted_total", {"source": source["source_key"]})
            except Exception:
                pass
            return self.get_run(run_id, "admin")
        finally:
            self._release_source_lease(source_id)

    def queue_manual_collect(self, source_id: int, operator: str) -> int:
        ensure_db()
        source = self._load_source(source_id)
        if not source.get("enabled"):
            raise RawSlowLogValidationError("采集源未启用")
        conn = _get_connection()
        try:
            busy = conn.execute("""
                SELECT id FROM slow_log_sources WHERE id=? AND lease_expires_at>NOW()
            """, (source_id,)).fetchone()
            if busy:
                raise RawSlowLogBusyError("采集源已有未过期运行租约")
            run_id = self._create_run(conn, source_id, "manual", operator, len(source["nodes"]))
            conn.commit()
        finally:
            conn.close()
        log_operation(operator, "raw_slowlog_collect_manual", "raw_slowlog", str(source_id), f"run_id={run_id}")
        return run_id

    def run_due_sources(self) -> list[int]:
        ensure_db()
        conn = _get_connection()
        try:
            rows = conn.execute("""
                SELECT id FROM slow_log_sources WHERE enabled=1 AND (
                  (last_error_code<>'' AND TIMESTAMPDIFF(SECOND,updated_at,NOW()) >= 60) OR
                  (last_error_code='' AND (last_success_at IS NULL OR TIMESTAMPDIFF(SECOND,last_success_at,NOW()) >= poll_interval_seconds))
                )
                ORDER BY last_success_at IS NULL DESC,last_success_at ASC LIMIT 20
            """).fetchall()
            ids = [int(row["id"]) for row in rows]
        finally:
            conn.close()
        for source_id in ids:
            try:
                self.collect_source(source_id)
            except Exception as exc:
                logger.warning("到期原始慢日志源采集失败 source_id=%s: %s", source_id, exc)
        return ids

    # ── 运行、事件与保留 ──────────────────────────────────────────────
    def get_run(self, run_id: int, role: str) -> dict:
        ensure_db()
        conn = _get_connection()
        try:
            item = conn.execute("SELECT * FROM slow_log_collection_runs WHERE id=?", (run_id,)).fetchone()
            if not item:
                raise RawSlowLogNotFoundError("采集运行不存在")
            result = dict(item)
            if role != "admin" and result.get("error_detail"):
                result["error_detail"] = "采集失败，请联系管理员查看受控服务日志。"
            return result
        finally:
            conn.close()

    def list_runs(self, source_id: int | None, limit: int, role: str) -> list[dict]:
        ensure_db()
        conn = _get_connection()
        try:
            if source_id:
                rows = conn.execute("SELECT * FROM slow_log_collection_runs WHERE source_id=? ORDER BY id DESC LIMIT ?", (source_id, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM slow_log_collection_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [self.get_run(row["id"], role) for row in rows]
        finally:
            conn.close()

    def list_events(self, filters: dict[str, Any]) -> dict:
        ensure_db()
        clauses = ["1=1"]
        params: list[Any] = []
        for key, column in (("source_id", "source_id"), ("db_name", "db_name"), ("source_node_id", "source_node_id")):
            if filters.get(key) not in (None, ""):
                clauses.append(f"{column}=?")
                params.append(filters[key])
        if filters.get("start_time"):
            clauses.append("event_time>=?")
            params.append(filters["start_time"])
        if filters.get("end_time"):
            clauses.append("event_time<=?")
            params.append(filters["end_time"])
        if filters.get("fingerprint"):
            clauses.append("sql_fingerprint=?")
            params.append(filters["fingerprint"])
        if filters.get("min_query_time_us") is not None:
            clauses.append("query_time_us>=?")
            params.append(int(filters["min_query_time_us"]))
        limit = min(max(int(filters.get("limit", 100)), 1), 1000)
        offset = max(int(filters.get("offset", 0)), 0)
        conn = _get_connection()
        try:
            where = " AND ".join(clauses)
            total = conn.execute(f"SELECT COUNT(*) AS cnt FROM slow_log_events WHERE {where}", tuple(params)).fetchone()["cnt"]
            rows = conn.execute(f"""
                SELECT e.*, s.connection_id FROM slow_log_events e
                JOIN slow_log_sources s ON s.id=e.source_id
                WHERE {where}
                ORDER BY e.event_time DESC,e.id DESC LIMIT ? OFFSET ?
            """, tuple(params + [limit, offset])).fetchall()
            return {"items": _rows(rows), "total": total, "time_label": "Proxy 慢日志记录时间"}
        finally:
            conn.close()

    def get_event(self, event_id: int) -> dict:
        ensure_db()
        conn = _get_connection()
        try:
            event = conn.execute("SELECT * FROM slow_log_events WHERE id=?", (event_id,)).fetchone()
            if not event:
                raise RawSlowLogNotFoundError("原始慢日志事件不存在")
            return dict(event)
        finally:
            conn.close()

    @staticmethod
    def cleanup_retention(table_name: str, retention_days: int, batch_size: int = 5000, max_batches: int = 20) -> int:
        """大表专用、每批独立提交的清理器；不得加入通用单次大 DELETE 路径。"""
        mapping = {"slow_log_events": "event_time", "slow_log_collection_runs": "started_at"}
        if table_name not in mapping:
            return 0
        total = 0
        for _ in range(max_batches):
            conn = _get_connection()
            try:
                # 派生表避免 MySQL "can't specify target table" 限制。
                cursor = conn.execute(f"""
                    DELETE FROM {table_name} WHERE id IN (
                        SELECT id FROM (SELECT id FROM {table_name}
                        WHERE {mapping[table_name]} < DATE_SUB(NOW(), INTERVAL ? DAY)
                        ORDER BY id LIMIT ?) AS retention_batch
                    )
                """, (int(retention_days), int(batch_size)))
                count = cursor.rowcount
                conn.commit()
                total += count
                if count < batch_size:
                    break
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return total


raw_slowlog_service = RawSlowLogService()
