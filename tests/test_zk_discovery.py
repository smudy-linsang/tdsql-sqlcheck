"""ZK 自动发现的安全边界、形态映射和 API 回归测试。"""
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api import zk_discovery as zk_api
from backend.main import app
from backend.services.database import _get_connection
from backend.services.security_service import decrypt_password
from backend.services.zk_connection_import_service import (
    ImportCredentials,
    MonitorCredentials,
    zk_connection_import_service,
)
from backend.services.zk_discovery_service import ZKDiscoveryUnavailableError, zk_discovery_service


client = TestClient(app)


def _clear_sessions():
    with zk_api._sessions_lock:
        zk_api._sessions.clear()
        zk_api._previews.clear()


def _clear_zk_config():
    """测试库中的唯一 ZK 配置不得影响环境变量兼容用例。"""
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM zk_discovery_config WHERE config_id = 1")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _isolated_zk_config():
    _clear_zk_config()
    yield
    _clear_zk_config()


def test_zk_discovery_service_mock_is_explicit_and_reserved():
    results = zk_discovery_service.discover(
        zk_server="unused:2181",
        zk_auth_user="unused",
        zk_auth_password="",
        force_mock=True,
    )
    assert len(results) == 3
    assert all(item["is_mock"] is True for item in results)
    assert all(item["host"].startswith("192.0.2.") for item in results)


def test_apply_endpoint_mapping_updates_primary_and_proxy_list():
    mapped = zk_discovery_service.apply_endpoint_mapping([{
        "host": "10.206.0.4",
        "port": 15002,
        "proxy_list": "10.206.0.4:15002;10.206.0.8:15002",
    }], {"10.206.0.4": "119.45.220.89", "10.206.0.8": "118.195.161.48"})[0]
    assert mapped["host"] == "119.45.220.89"
    assert mapped["proxy_list"] == "119.45.220.89:15002;118.195.161.48:15002"


def test_real_discovery_fails_over_candidates_and_never_places_auth_in_argv(tmp_path, monkeypatch):
    """多节点按成员尝试；认证只经子进程环境传递，不能出现在进程参数中。"""
    fake_zkcli = tmp_path / "zkCli.sh"
    fake_zkcli.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_zkcli.chmod(0o700)
    monkeypatch.setattr(zk_discovery_service, "is_real_discovery_runtime_supported", lambda: True)
    monkeypatch.setattr(zk_discovery_service, "is_zk_port_open", lambda _: True)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[3] == "first.example:2118":
            return SimpleNamespace(returncode=4, stdout="")
        return SimpleNamespace(
            returncode=0,
            stdout="set_1,10.0.0.1,15002,u,p,ALL,0,running,noshard,set_1,10.0.0.1:15002\n",
        )

    monkeypatch.setattr("backend.services.zk_discovery_service.subprocess.run", fake_run)
    credential = "x" * 6
    results = zk_discovery_service.discover(
        zk_server="first.example:2118,second.example:2118",
        zk_auth_user="reader",
        zk_auth_password=credential,
        zkcli_path=str(fake_zkcli),
        driver="shell",
    )
    assert len(calls) == 2
    assert calls[1][0][3] == "second.example:2118"
    assert "--zk-auth" not in calls[1][0]
    assert calls[1][1]["env"]["ZK_AUTH_USER"] == "reader"
    assert calls[1][1]["env"]["ZK_AUTH_PASSWORD"] == credential
    assert results[0]["is_mock"] is False


def test_kazoo_driver_reads_centralized_and_distributed_instances(monkeypatch, caplog):
    """默认 Python 客户端不依赖 zkCli，且保留两种实例形态与全部 Proxy。"""
    class FakeKazooClient:
        last = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.auth = None
            FakeKazooClient.last = self

        def start(self, timeout):
            assert timeout == 15

        def add_auth(self, scheme, credential):
            self.auth = (scheme, credential)

        def get_children(self, path):
            return {
                "/tdsqlzk": ["sets", "group_demo"],
                "/tdsqlzk/sets": ["set@central_1"],
                "/tdsqlzk/group_demo/sets": ["set@shard_1", "set@shard_2"],
            }[path]

        def get(self, path):
            values = {
                "/tdsqlzk/sets/set@central_1/setrun@central_1": {
                    "set": "central_1", "status": 0, "user": "reader", "password": "secret-a",
                    "proxy": [{"name": "10.0.0.4_15002"}, {"name": "10.0.0.8_15002"}],
                },
                "/tdsqlzk/group_demo/sets/set@shard_1/setrun@shard_1": {
                    "set": "shard_1", "status": 0, "user": "reader", "password": "secret-b",
                    "proxy": [{"name": "10.0.0.4_15005"}, {"name": "10.0.0.8_15005"}],
                },
            }
            return json.dumps(values[path]).encode("utf-8"), None

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("kazoo.client.KazooClient", FakeKazooClient)
    monkeypatch.setenv("ZK_TEST_AUTH_PASSWORD", "auth-secret")
    caplog.set_level(logging.INFO, logger="tdsql.zk_discovery")
    results = zk_discovery_service._discover_with_kazoo(
        zk_server="zk.example:2118", zk_auth_user="reader",
        zk_auth_password=os.environ["ZK_TEST_AUTH_PASSWORD"],
        zk_root="/tdsqlzk", proxy_mode="first", default_database="ALL",
    )

    assert FakeKazooClient.last.auth == ("digest", "reader:auth-secret")
    assert [(item["instance_kind"], item["instance_type"]) for item in results] == [
        ("noshard", "centralized"), ("groupshard", "distributed"),
    ]
    assert results[1]["proxy_list"] == "10.0.0.4:15005;10.0.0.8:15005"
    assert results[0]["set_ids"] == ["central_1"]
    assert results[1]["set_ids"] == ["shard_1", "shard_2"]
    assert all(item["is_mock"] is False for item in results)
    assert "ZK_DISCOVERY_KAZOO_SESSION_CONNECTED candidate=zk.example:2118" in caplog.text
    assert "ZK_DISCOVERY_KAZOO_STRUCTURE candidate=zk.example:2118" in caplog.text
    assert "ZK_DISCOVERY_KAZOO_RECORD_SUMMARY candidate=zk.example:2118 usable=2" in caplog.text
    assert "auth-secret" not in caplog.text
    assert "secret-a" not in caplog.text
    assert "secret-b" not in caplog.text


def test_kazoo_failure_logs_stage_without_credentials(monkeypatch, caplog):
    class FailingKazooClient:
        def __init__(self, **kwargs):
            pass

        def start(self, timeout):
            raise TimeoutError("network handshake did not finish")

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("kazoo.client.KazooClient", FailingKazooClient)
    monkeypatch.setenv("ZK_LOG_TEST_AUTH_PASSWORD", "never-log-me")
    caplog.set_level(logging.INFO, logger="tdsql.zk_discovery")
    with pytest.raises(ZKDiscoveryUnavailableError):
        zk_discovery_service._discover_with_kazoo(
            zk_server="zk.example:2118", zk_auth_user="reader",
            zk_auth_password=os.environ["ZK_LOG_TEST_AUTH_PASSWORD"],
            zk_root="/tdsqlzk", proxy_mode="first", default_database="ALL",
        )
    assert "ZK_DISCOVERY_KAZOO_FAILED candidate=zk.example:2118 stage=session_start error_type=TimeoutError" in caplog.text
    assert "never-log-me" not in caplog.text


def test_inventory_script_handles_prompt_prefixed_noninteractive_response():
    script = (Path(__file__).resolve().parents[1] / "deploy" / "tdsql_inventory.sh").read_text(
        encoding="utf-8")
    assert "_normalized_output" in script
    assert "非交互客户端可能不回显每条 ls 命令" in script


def test_parse_csv_11_columns_and_kind_mapping():
    csv_text = (
        "Central,10.206.0.4,15002,u,p,ALL,0,running,"
        "noshard,set_1782130875_4,10.206.0.4:15002;10.206.0.8:15002\n"
        "Distributed,10.206.0.8,15005,u,p,ALL,0,running,"
        "groupshard,group_1782132247_10,10.206.0.8:15005\n"
    )
    centralized, distributed = zk_discovery_service.parse_csv(csv_text)
    assert centralized["instance_type"] == "centralized"
    assert distributed["instance_type"] == "distributed"
    assert distributed["instance_id"] == "group_1782132247_10"


def test_parse_csv_preserves_complete_set_list_for_shell_driver():
    item = zk_discovery_service.parse_csv(
        "Distributed,h,15005,u,p,ALL,0,ok,groupshard,group_1,h:15005,set_a;set_b;set_a\n"
    )[0]
    assert item["set_ids"] == ["set_a", "set_b"]


def test_unknown_kind_does_not_guess():
    item = zk_discovery_service.parse_csv(
        "n,h,1,u,p,ALL,0,ok,brand_new_kind,x_1,h:1\n"
    )[0]
    assert item["instance_type"] is None
    assert item["instance_kind"] == "brand_new_kind"


def test_sync_matches_any_mapped_proxy_of_instance():
    conn = _get_connection()
    conn_id = "zk_sync_mapping_test"
    try:
        conn.execute("DELETE FROM tdsql_connections WHERE id = ?", (conn_id,))
        conn.execute(
            "INSERT INTO tdsql_connections "
            "(id, name, host, port, username, password_encrypted, `database`) "
            "VALUES (?,?,?,?,?,?,?)",
            (conn_id, "ZK sync mapping test", "118.195.161.48", 15002, "u", "", "ALL"),
        )
        conn.commit()
        assert zk_discovery_service.sync_instance_kinds([{
            "host": "119.45.220.89", "port": 15002,
            "instance_kind": "noshard", "instance_id": "set_1782130875_4",
            "proxy_list": "119.45.220.89:15002;118.195.161.48:15002",
        }]) >= 1
        row = dict(conn.execute(
            "SELECT zk_instance_kind, zk_instance_id FROM tdsql_connections WHERE id = ?", (conn_id,)
        ).fetchone())
        assert row == {"zk_instance_kind": "noshard", "zk_instance_id": "set_1782130875_4"}
    finally:
        conn.execute("DELETE FROM tdsql_connections WHERE id = ?", (conn_id,))
        conn.commit()
        conn.close()


def test_standardized_import_creates_one_connection_per_business_database(monkeypatch):
    """提交只接受预检出的业务库，且类型/SET/监控配置均由权威来源写入。"""
    instance = {
        "instance_id": "group_import_test", "instance_kind": "groupshard",
        "instance_type": "distributed", "host": "198.51.100.11", "port": 15136,
        "proxy_list": "198.51.100.11:15136;198.51.100.12:15136",
        "set_ids": ["set_import_a", "set_import_b"],
    }
    business = ImportCredentials("biz_user", "business-secret-for-test")
    monitor = MonitorCredentials("198.51.100.20", 15001, "mon_user", "monitor-secret-for-test", "monitor_meta")
    monkeypatch.setattr(zk_connection_import_service, "_resolve_instance_name", lambda *_: ("统一收单-分布式-提前批2", "instance"))
    monkeypatch.setattr(zk_connection_import_service, "_list_business_databases", lambda *_: ["cap_gz", "cap_settle"])
    rows = zk_connection_import_service.build_preview([instance], business, monitor)
    assert [row["generated_connection_name"] for row in rows] == [
        "统一收单-分布式-提前批2-15136-cap_gz",
        "统一收单-分布式-提前批2-15136-cap_settle",
    ]
    assert all(row["status"] == "ready" for row in rows)

    result = zk_connection_import_service.commit(rows, business, monitor, "pytest", "discovery-import-test")
    conn = _get_connection()
    try:
        saved = [dict(conn.execute("SELECT * FROM tdsql_connections WHERE id=?", (item["id"],)).fetchone()) for item in result["created"]]
        assert {row["database"] for row in saved} == {"cap_gz", "cap_settle"}
        assert all(int(row["is_distributed"]) == 1 for row in saved)
        assert all(row["set_list"] == "set_import_a,set_import_b" for row in saved)
        assert all(row["zk_instance_kind"] == "groupshard" for row in saved)
        assert all(row["zk_instance_id"] == "group_import_test" for row in saved)
        assert all(row["monitor_host"] == "198.51.100.20" for row in saved)
        assert all(decrypt_password(row["password_encrypted"]) == "business-secret-for-test" for row in saved)
        assert all(decrypt_password(row["monitor_password_encrypted"]) == "monitor-secret-for-test" for row in saved)
        audit = dict(conn.execute("SELECT * FROM zk_discovery_import_batches WHERE id=?", (result["batch_id"],)).fetchone())
        assert audit["created_count"] == 2
        assert "business-secret-for-test" not in str(audit)
        assert "monitor-secret-for-test" not in str(audit)
    finally:
        for item in result["created"]:
            conn.execute("DELETE FROM tdsql_connections WHERE id=?", (item["id"],))
        conn.execute("DELETE FROM zk_discovery_import_items WHERE batch_id=?", (result["batch_id"],))
        conn.execute("DELETE FROM zk_discovery_import_batches WHERE id=?", (result["batch_id"],))
        conn.commit()
        conn.close()


def test_zk_api_unconfigured_real_discovery_returns_503(monkeypatch):
    _clear_sessions()
    for name in ("ZK_DISCOVERY_FORCE_MOCK", "ZK_DISCOVERY_SERVERS", "ZK_DISCOVERY_AUTH_FILE"):
        monkeypatch.delenv(name, raising=False)
    resp = client.post("/api/v1/tdsql/discover")
    assert resp.status_code == 503
    assert "Mock" not in resp.text


def test_zk_config_api_encrypts_password_redacts_it_and_runtime_prefers_database(monkeypatch):
    """管理员可配置，口令永不回显；保存后扫描路径即时读取数据库配置。"""
    monkeypatch.delenv("ZK_DISCOVERY_FORCE_MOCK", raising=False)
    monkeypatch.setenv("ZK_DISCOVERY_SERVERS", "legacy.invalid:2118")
    payload = {
        "servers": "zk-a.example:2118, zk-b.example:2118",
        "root_path": "/tdsqlzk",
        "driver": "kazoo",
        "zkcli_path": "",
        "proxy_mode": "first",
        "default_database": "ALL",
        "endpoint_map": {"10.0.0.1": "198.51.100.10"},
        "auth_username": "zk_reader",
        "auth_password": "test-zk-secret",
    }
    saved = client.put("/api/v1/tdsql/discover/config", json=payload)
    assert saved.status_code == 200
    body = saved.json()
    assert body["servers"] == "zk-a.example:2118,zk-b.example:2118"
    assert body["password_configured"] is True
    assert "auth_password" not in body
    assert "encrypted" not in body

    public = client.get("/api/v1/tdsql/discover/config")
    assert public.status_code == 200
    assert "test-zk-secret" not in public.text
    assert public.json()["endpoint_map"] == {"10.0.0.1": "198.51.100.10"}

    conn = _get_connection()
    try:
        row = dict(conn.execute(
            "SELECT auth_password_encrypted FROM zk_discovery_config WHERE config_id = 1"
        ).fetchone())
    finally:
        conn.close()
    assert row["auth_password_encrypted"] != "test-zk-secret"
    assert decrypt_password(row["auth_password_encrypted"]) == "test-zk-secret"

    runtime = zk_api._read_deployment_config()
    assert runtime["source"] == "database"
    assert runtime["servers"] == "zk-a.example:2118,zk-b.example:2118"
    assert runtime["auth_password"] == "test-zk-secret"


def test_zk_config_blank_password_preserves_existing_secret():
    initial = {
        "servers": "zk.example:2118", "root_path": "/tdsqlzk", "driver": "kazoo",
        "zkcli_path": "", "proxy_mode": "first", "default_database": "ALL",
        "endpoint_map": {}, "auth_username": "reader", "auth_password": "first-secret",
    }
    assert client.put("/api/v1/tdsql/discover/config", json=initial).status_code == 200
    conn = _get_connection()
    try:
        before = dict(conn.execute(
            "SELECT auth_password_encrypted FROM zk_discovery_config WHERE config_id = 1"
        ).fetchone())["auth_password_encrypted"]
    finally:
        conn.close()
    initial["servers"] = "zk-new.example:2118"
    initial["auth_password"] = ""
    updated = client.put("/api/v1/tdsql/discover/config", json=initial)
    assert updated.status_code == 200
    conn = _get_connection()
    try:
        after = dict(conn.execute(
            "SELECT auth_password_encrypted FROM zk_discovery_config WHERE config_id = 1"
        ).fetchone())["auth_password_encrypted"]
    finally:
        conn.close()
    assert after == before
    assert decrypt_password(after) == "first-secret"


def test_zk_config_is_admin_only():
    request = SimpleNamespace(state=SimpleNamespace(role="dba", username="dba"))
    with pytest.raises(HTTPException) as exc_info:
        zk_api.get_discovery_config(request)
    assert exc_info.value.status_code == 403


def test_zk_config_frontend_exposes_admin_entry_and_redacted_password_flow():
    root = Path(__file__).resolve().parents[1]
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    javascript = (root / "frontend" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert '<script src="/static/js/app.js?v=20260803.4"></script>' in html
    assert 'v-if="isAdmin" type="warning" size="small" @click="openZkConfig">ZK发现配置' in html
    assert 'v-model="zkConfigForm.auth_password" type="password"' in html
    # v1.6.0.1 修复 P5：Mock 结果必须有醒目"演示"标识，前端模板与状态暴露缺一不可
    assert 'v-if="zkDiscoveryIsMock" type="error"' in html
    assert ">演示</el-tag>" in html
    assert "zkDiscoveryIsMock" in javascript
    assert "/api/v1/tdsql/discover/config" in javascript
    assert "password_configured" in javascript
    assert "responseMessage(d,'扫描失败')" in javascript
    assert "zkConfigDialogVisible.value=true" in javascript
    assert "zkConfigDialogVisible,zkConfigLoading" in javascript
    assert "/api/v1/tdsql/discover/import-preview" in javascript
    assert "配置导入并生成预览" in html


def test_zk_api_mock_is_marked_and_cannot_register(monkeypatch):
    _clear_sessions()
    monkeypatch.setenv("ZK_DISCOVERY_FORCE_MOCK", "1")
    called = []
    monkeypatch.setattr(zk_discovery_service, "sync_instance_kinds", lambda _: called.append(True) or 0)

    resp = client.post("/api/v1/tdsql/discover")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["source"] == "mock"
    assert payload["is_mock"] is True
    assert "password" not in payload["items"][0]
    assert called == []

    register = client.post("/api/v1/tdsql/discover/register", json={
        "discovery_id": payload["discovery_id"],
        "item_token": payload["items"][0]["item_token"],
        "connection_id": "must_not_register",
    })
    assert register.status_code == 410


def test_zk_api_real_discovery_redacts_zk_credentials_maps_address_and_creates_preview(tmp_path, monkeypatch):
    _clear_sessions()
    auth_file = tmp_path / "zk-auth.json"
    auth_file.write_text(json.dumps({"username": "reader", "password": "secret"}), encoding="utf-8")
    monkeypatch.delenv("ZK_DISCOVERY_FORCE_MOCK", raising=False)
    monkeypatch.setenv("ZK_DISCOVERY_SERVERS", "10.206.0.4:2118,10.206.0.8:2118")
    monkeypatch.setenv("ZK_DISCOVERY_AUTH_FILE", str(auth_file))
    monkeypatch.setenv("ZK_DISCOVERY_ENDPOINT_MAP", json.dumps({"10.206.0.4": "119.45.220.89"}))

    raw = [{
        "service_name": "set_1", "host": "10.206.0.4", "port": 15002,
        "user": "db_reader", "password": "db-secret", "database": "ALL",
        "status_code": "0", "status_text": "running", "instance_kind": "noshard",
        "instance_id": "set_1", "instance_type": "centralized",
        "proxy_list": "10.206.0.4:15002", "is_mock": False,
    }]
    monkeypatch.setattr(zk_discovery_service, "discover", lambda **_: raw)
    sync_calls = []
    monkeypatch.setattr(zk_discovery_service, "sync_instance_kinds", lambda items: sync_calls.append(items) or 1)
    preview_calls = []
    monkeypatch.setattr(
        zk_api.zk_connection_import_service, "build_preview",
        lambda instances, business, monitor: preview_calls.append((instances, business, monitor)) or [{
            "source_instance_id": "set_1", "instance_kind": "noshard", "instance_type": "centralized",
            "primary_proxy": "119.45.220.89:15002", "primary_proxy_host": "119.45.220.89",
            "primary_proxy_port": 15002, "set_ids": ["set_1"], "resolved_instance_name": "集中式测试",
            "name_source": "instance", "database": "business_db",
            "generated_connection_name": "集中式测试-15002-business_db", "status": "ready",
            "failure_code": "", "failure_detail": "", "monitor_host": monitor.host,
            "monitor_port": monitor.port, "monitor_user": monitor.username, "monitor_db": monitor.database,
        }],
    )

    resp = client.post("/api/v1/tdsql/discover")
    assert resp.status_code == 200
    payload = resp.json()
    item = payload["items"][0]
    assert payload["source"] == "zk"
    assert item["host"] == "119.45.220.89"
    assert item["proxy_list"] == "119.45.220.89:15002"
    assert "password" not in item
    assert "user" not in item
    assert item["set_ids"] == ["set_1"]
    assert len(sync_calls) == 1

    preview = client.post("/api/v1/tdsql/discover/import-preview", json={
        "discovery_id": payload["discovery_id"],
        "item_tokens": [item["item_token"]],
        "business": {"username": "business_user", "password": "business-secret"},
        "monitor": {"host": "monitor.example", "port": 15001, "username": "monitor_user", "password": "monitor-secret", "database": "tdsqlpcloud_monitor"},
    })
    assert preview.status_code == 200
    assert "business-secret" not in preview.text
    assert "monitor-secret" not in preview.text
    assert preview.json()["rows"][0]["generated_connection_name"] == "集中式测试-15002-business_db"
    assert preview_calls[0][0][0]["host"] == "119.45.220.89"
    assert "password" not in preview_calls[0][0][0]
