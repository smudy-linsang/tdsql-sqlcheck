from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.raw_slowlog import router
from backend.services import auth_service
from backend.services.raw_slowlog_service import RawSlowLogService, RawSlowLogValidationError


ROOT = Path(__file__).resolve().parents[1]


def _valid_source():
    return {
        "source_key": "sit_proxy_slowlog", "connection_id": "sit_conn", "display_name": "SIT Proxy",
        "transport": "ssh_exporter_v1", "credential_ref": "reader", "known_hosts_ref": "sit", "nodes": [{"node_key": "proxy_a", "display_name": "Proxy A",
        "ssh_host": "10.0.0.8", "ssh_port": 22, "host_key_alias": "proxy-a", "remote_source_key": "sit_proxy_slowlog",
        "declared_path_template": "/approved/path/*.log", "parser_profile": "tdsql_mysql_slowlog_v1"}],
    }


def test_migration_is_isolated_and_has_origin_unique_key():
    script = (ROOT / "backend/schema/v7/070_raw_slow_log_collection.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS slow_log_events" in script
    assert "uq_sle_origin" in script
    assert "CREATE TABLE IF NOT EXISTS slow_queries" not in script
    assert "CREATE TABLE IF NOT EXISTS scan_tasks" not in script
    assert "INSERT IGNORE INTO retention_policies" in script


def test_router_exposes_independent_prefix_and_no_delete_endpoint():
    paths = {route.path for route in router.routes}
    assert "/api/v1/raw-slowlogs/sources" in paths
    assert "/api/v1/raw-slowlogs/events" in paths
    assert not any("DELETE" in str(route.methods) for route in router.routes)


def test_source_validation_requires_fixed_parser_and_safe_key():
    service = RawSlowLogService()
    service._validate_source_payload(_valid_source())
    invalid = _valid_source()
    invalid["nodes"][0]["parser_profile"] = "unknown"
    try:
        service._validate_source_payload(invalid)
    except RawSlowLogValidationError:
        pass
    else:
        raise AssertionError("unknown parser profile must be rejected")
    injected = _valid_source()
    injected["nodes"][0]["declared_path_template"] = "/approved/slow/*.log;id"
    with pytest.raises(RawSlowLogValidationError):
        service._validate_source_payload(injected)


def test_rbac_mapping_covers_new_prefix(monkeypatch):
    monkeypatch.setattr(auth_service, "get_visible_menus", lambda role: {"slow-raw-log"})
    assert auth_service.check_permission("developer", "GET", "/api/v1/raw-slowlogs/events")
    assert not auth_service.check_permission("developer", "POST", "/api/v1/raw-slowlogs/sources/1/collect")
    assert auth_service.check_permission("dba", "POST", "/api/v1/raw-slowlogs/sources/1/collect")


def test_non_admin_source_view_masks_all_connection_and_secret_references():
    source = _valid_source()
    source.update({"id": 1, "credential_ref": "reader", "known_hosts_ref": "sit"})
    source["nodes"] = [{**source["nodes"][0], "ssh_host_key_fingerprint": "sha256:test"}]
    view = RawSlowLogService._public_source(source, "developer", detail=True)
    assert view["credential_ref"] == "已配置"
    assert view["known_hosts_ref"] == "已配置"
    assert view["nodes"][0]["ssh_host"] == "已配置"
    assert view["nodes"][0]["declared_path_template"] == "已配置"


def test_chunk_protocol_or_source_key_mismatch_fails_before_database_access():
    service = RawSlowLogService()
    with pytest.raises(RawSlowLogValidationError, match="协议或采集源标识"):
        service._store_chunk({"max_batch_bytes": 1024}, {"remote_source_key": "expected"}, {}, {
            "type": "chunk", "protocol": "wrong", "source_key": "unexpected",
        }, {})


def test_main_registers_raw_slowlog_router(monkeypatch):
    from backend.api import raw_slowlog
    from backend.main import app

    monkeypatch.setattr(raw_slowlog.raw_slowlog_service, "list_sources", lambda role: [{"id": 7, "display_name": "masked"}])
    with TestClient(app) as client:
        response = client.get("/api/v1/raw-slowlogs/sources")
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == 7
