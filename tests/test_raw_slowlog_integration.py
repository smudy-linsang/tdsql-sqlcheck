from __future__ import annotations

import base64
import uuid
from pathlib import Path

import pytest

from backend.services.database import _get_connection, ensure_db
from backend.services.raw_slowlog_ssh import SSHSecretResolver
from backend.services.raw_slowlog_service import RawSlowLogBusyError, RawSlowLogService


_LOG = b"""# Time: 2026-08-02T10:00:01
# User@Host: app[app] @ proxy [10.1.2.3]
# Thread_id: 42  Schema: payment
# Query_time: 1.250000  Lock_time: 0.001000 Rows_sent: 1  Rows_examined: 200
SET timestamp=1785636001;
SELECT * FROM orders WHERE card_no='6222-1234-5678-9999' AND amount=120.50;
"""


class FakeExporterClient:
    def __init__(self, secret_root: Path):
        self.secret_resolver = SSHSecretResolver(secret_root)

    def request(self, node, source, payload, timeout_seconds):
        if payload["op"] == "probe":
            return [{"type": "probe", "protocol": "raw_slowlog_exporter_v1", "version": "1.5.3.0",
                     "source_key": node["remote_source_key"], "storage_identity": "test-storage-a",
                     "format_signature": {"parser_profile": "tdsql_mysql_slowlog_v1", "time_header": True, "query_time_header": True},
                     "files": [{"file_identity": "dev:1:ino:2", "file_label": "slow.log", "file_size": len(_LOG)}]}]
        if payload["op"] == "pull":
            cursor = payload["cursors"][0] if payload.get("cursors") else None
            offset = int(cursor["offset"]) if cursor else 0
            data = _LOG[offset:]
            anchor = _LOG[int(cursor["anchor_start_offset"]):int(cursor["anchor_start_offset"])+int(cursor["anchor_length"])] if cursor else b""
            return [{"type": "chunk", "protocol": "raw_slowlog_exporter_v1", "source_key": node["remote_source_key"],
                     "file_identity": "dev:1:ino:2", "file_label": "slow.log",
                     "file_size": len(_LOG), "offset": offset, "next_offset": len(_LOG), "eof": True,
                     "data_base64": base64.b64encode(data).decode(),
                     "post_anchor_base64": base64.b64encode(_LOG[max(0, len(_LOG)-64):]).decode(),
                     "pre_anchor_base64": base64.b64encode(anchor).decode()}]
        raise AssertionError(f"unexpected operation {payload['op']}")


def test_probe_enable_collect_and_event_persistence_against_test_metadata_db(tmp_path: Path, monkeypatch):
    (tmp_path / "reader.key").write_text("test key", encoding="utf-8")
    (tmp_path / "sit.known_hosts").write_text("proxy-a ssh-ed25519 AAAA-test", encoding="utf-8")
    source_key = f"raw_it_{uuid.uuid4().hex[:12]}"
    service = RawSlowLogService(FakeExporterClient(tmp_path))
    source_id = None
    try:
        created = service.create_source({
            "source_key": source_key, "connection_id": "integration_fake", "display_name": "集成测试原始慢日志",
            "credential_ref": "reader", "known_hosts_ref": "sit", "initial_position": "lookback",
            "initial_lookback_seconds": 300, "nodes": [{"node_key": "proxy_a", "display_name": "Proxy A",
                "ssh_host": "10.0.0.8", "ssh_port": 22, "host_key_alias": "proxy-a",
                "remote_source_key": source_key, "declared_path_template": "/approved/slow/*.log",
                "parser_profile": "tdsql_mysql_slowlog_v1"}],
        }, "pytest")
        source_id = created["id"]
        probe = service.probe_source(source_id, "pytest")
        assert probe["status"] == "completed"
        enabled = service.set_enabled(source_id, True, "pytest")
        assert enabled["enabled"] == 1

        run = service.collect_source(source_id, "pytest", "manual")
        assert run["status"] == "completed"
        assert run["events_inserted"] == 1
        events = service.list_events({"source_id": source_id, "limit": 10, "offset": 0})
        assert events["total"] == 1
        assert events["items"][0]["event_time"].startswith("2026-08-02T10:00:01")
        assert "6222-1234" not in events["items"][0]["sql_template"]
        assert events["items"][0]["query_time_us"] == 1_250_000

        # U05: 进程在写入事件后、游标提交前重放同一块时，origin 唯一键必须
        # 保证不重复入库。这里故意回拨测试游标，模拟该恢复路径。
        conn = _get_connection()
        try:
            conn.execute("""
                UPDATE slow_log_cursors SET cursor_offset=0,anchor_start_offset=0,
                anchor_length=0,anchor_sha256='' WHERE source_node_id=(
                    SELECT id FROM slow_log_source_nodes WHERE source_id=? LIMIT 1
                ) AND file_identity='dev:1:ino:2' AND status='active'
            """, (source_id,))
            conn.commit()
        finally:
            conn.close()
        replay = service.collect_source(source_id, "pytest", "manual")
        assert replay["status"] == "completed"
        assert replay["events_inserted"] == 0
        assert replay["events_duplicate"] == 1
        assert service.list_events({"source_id": source_id, "limit": 10, "offset": 0})["total"] == 1

        # U07: 首次 tail 建立的空块仍需保存后置锚点，以检测后续 copytruncate。
        source = service._load_source(source_id)
        node = source["nodes"][0]
        tail_offset = len(_LOG)
        outcome = service._store_chunk(source, node, {}, {
            "type": "chunk", "protocol": "raw_slowlog_exporter_v1", "source_key": source_key,
            "file_identity": "dev:1:ino:tail", "file_label": "tail.log", "file_size": tail_offset,
            "offset": tail_offset, "next_offset": tail_offset, "eof": True,
            "data_base64": "", "pre_anchor_base64": "",
            "post_anchor_base64": base64.b64encode(_LOG[-64:]).decode(),
        }, {key: 0 for key in (
            "files_seen", "bytes_read", "blocks_parsed", "events_inserted", "events_duplicate",
            "events_filtered", "incomplete_tail_count", "parse_error_count",
        )})
        assert outcome["complete"]
        conn = _get_connection()
        try:
            tail_cursor = conn.execute("SELECT anchor_length,anchor_sha256 FROM slow_log_cursors WHERE source_node_id=? AND file_identity=?",
                                       (node["id"], "dev:1:ino:tail")).fetchone()
            assert tail_cursor["anchor_length"] == 64, dict(tail_cursor)
            assert tail_cursor["anchor_sha256"]
        finally:
            conn.close()

        # U08/U17: copytruncate 后快速重新增长时，前置锚点不一致必须令旧代际
        # 失效并创建下一代，而不是从相同偏移静默继续。
        conn = _get_connection()
        try:
            tail_cursor = dict(conn.execute("SELECT * FROM slow_log_cursors WHERE source_node_id=? AND file_identity=? AND status='active'",
                                            (node["id"], "dev:1:ino:tail")).fetchone())
        finally:
            conn.close()
        with pytest.raises(Exception, match="E5022"):
            service._store_chunk(source, node, {"dev:1:ino:tail": tail_cursor}, {
                "type": "chunk", "protocol": "raw_slowlog_exporter_v1", "source_key": source_key,
                "file_identity": "dev:1:ino:tail", "file_label": "tail.log", "file_size": tail_offset + 8,
                "offset": tail_offset, "next_offset": tail_offset + 8, "eof": True,
                "data_base64": base64.b64encode(b"garbage;").decode(),
                "pre_anchor_base64": base64.b64encode(b"x" * 64).decode(),
                "post_anchor_base64": base64.b64encode(b"x" * 64).decode(),
            }, {key: 0 for key in (
                "files_seen", "bytes_read", "blocks_parsed", "events_inserted", "events_duplicate",
                "events_filtered", "incomplete_tail_count", "parse_error_count",
            )})
        conn = _get_connection()
        try:
            generations = conn.execute("SELECT generation,status FROM slow_log_cursors WHERE source_node_id=? AND file_identity=? ORDER BY generation",
                                       (node["id"], "dev:1:ino:tail")).fetchall()
            assert [(row["generation"], row["status"]) for row in generations] == [(0, "superseded"), (1, "active")]
        finally:
            conn.close()

        # U06: 事件 INSERT 后游标写入失败时必须同事务回滚，不能留下无游标事件。
        rollback_identity = "dev:1:ino:rollback"
        original_upsert = service._upsert_cursor
        monkeypatch.setattr(service, "_upsert_cursor", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("cursor write fault")))
        with pytest.raises(RuntimeError, match="cursor write fault"):
            service._store_chunk(source, node, {}, {
                "type": "chunk", "protocol": "raw_slowlog_exporter_v1", "source_key": source_key,
                "file_identity": rollback_identity, "file_label": "rollback.log", "file_size": len(_LOG),
                "offset": 0, "next_offset": len(_LOG), "eof": True,
                "data_base64": base64.b64encode(_LOG).decode(), "pre_anchor_base64": "",
                "post_anchor_base64": base64.b64encode(_LOG[-64:]).decode(),
            }, {key: 0 for key in (
                "files_seen", "bytes_read", "blocks_parsed", "events_inserted", "events_duplicate",
                "events_filtered", "incomplete_tail_count", "parse_error_count",
            )})
        monkeypatch.setattr(service, "_upsert_cursor", original_upsert)
        conn = _get_connection()
        try:
            assert conn.execute("SELECT COUNT(*) AS count FROM slow_log_events WHERE source_id=? AND origin_file_identity=?",
                                (source_id, rollback_identity)).fetchone()["count"] == 0
        finally:
            conn.close()

        # U20: 积压告警必须去重，恢复后应转为 resolved，而不是持续制造 active 告警。
        RawSlowLogService._sync_backlog_alert(source, 4096, 601, True)
        RawSlowLogService._sync_backlog_alert(source, 8192, 602, True)
        conn = _get_connection()
        try:
            active = conn.execute("""
                SELECT COUNT(*) AS count FROM alerts WHERE connection_id='integration_fake'
                AND metric_name='raw_slowlog_backlog' AND status='active'
            """).fetchone()["count"]
            assert active == 1
        finally:
            conn.close()
        RawSlowLogService._sync_backlog_alert(source, 0, None, False)
        conn = _get_connection()
        try:
            resolved = conn.execute("""
                SELECT COUNT(*) AS count FROM alerts WHERE connection_id='integration_fake'
                AND metric_name='raw_slowlog_backlog' AND status='resolved'
            """).fetchone()["count"]
            assert resolved == 1
            conn.execute("DELETE FROM alerts WHERE connection_id='integration_fake' AND metric_name='raw_slowlog_backlog'")
            conn.commit()
        finally:
            conn.close()

        # U14: 已持有源租约时，手动入口不得创建第二个 running 任务。
        conn = _get_connection()
        try:
            conn.execute("UPDATE slow_log_sources SET lease_holder='other',lease_expires_at=DATE_ADD(NOW(), INTERVAL 5 MINUTE) WHERE id=?", (source_id,))
            conn.commit()
        finally:
            conn.close()
        try:
            service.queue_manual_collect(source_id, "pytest")
        except RawSlowLogBusyError:
            pass
        else:
            raise AssertionError("source lease conflict must reject a second manual run")
        finally:
            conn = _get_connection()
            try:
                conn.execute("UPDATE slow_log_sources SET lease_holder='',lease_expires_at=NULL WHERE id=?", (source_id,))
                conn.commit()
            finally:
                conn.close()
    finally:
        if source_id is not None:
            ensure_db()
            conn = _get_connection()
            try:
                node_rows = conn.execute("SELECT id FROM slow_log_source_nodes WHERE source_id=?", (source_id,)).fetchall()
                node_ids = [row["id"] for row in node_rows]
                conn.execute("DELETE FROM alerts WHERE message LIKE ?", (f"原始慢日志采集源 source_id={source_id}%",))
                conn.execute("DELETE FROM slow_log_events WHERE source_id=?", (source_id,))
                conn.execute("DELETE FROM slow_log_collection_runs WHERE source_id=?", (source_id,))
                for node_id in node_ids:
                    conn.execute("DELETE FROM slow_log_cursors WHERE source_node_id=?", (node_id,))
                    conn.execute("DELETE FROM slow_log_node_probe_files WHERE source_node_id=?", (node_id,))
                conn.execute("DELETE FROM slow_log_source_nodes WHERE source_id=?", (source_id,))
                conn.execute("DELETE FROM slow_log_sources WHERE id=?", (source_id,))
                conn.commit()
            finally:
                conn.close()
