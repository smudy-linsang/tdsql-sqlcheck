"""ZK 自动发现的安全边界、形态映射和 API 回归测试。"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.api import zk_discovery as zk_api
from backend.main import app
from backend.services.database import _get_connection
from backend.services.zk_discovery_service import zk_discovery_service


client = TestClient(app)


def _clear_sessions():
    with zk_api._sessions_lock:
        zk_api._sessions.clear()


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
    monkeypatch.setenv("ZK_DISCOVERY_DRIVER", "shell")
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
    )
    assert len(calls) == 2
    assert calls[1][0][3] == "second.example:2118"
    assert "--zk-auth" not in calls[1][0]
    assert calls[1][1]["env"]["ZK_AUTH_USER"] == "reader"
    assert calls[1][1]["env"]["ZK_AUTH_PASSWORD"] == credential
    assert results[0]["is_mock"] is False


def test_kazoo_driver_reads_centralized_and_distributed_instances(monkeypatch):
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
    assert all(item["is_mock"] is False for item in results)


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


def test_zk_api_unconfigured_real_discovery_returns_503(monkeypatch):
    _clear_sessions()
    for name in ("ZK_DISCOVERY_FORCE_MOCK", "ZK_DISCOVERY_SERVERS", "ZK_DISCOVERY_AUTH_FILE"):
        monkeypatch.delenv(name, raising=False)
    resp = client.post("/api/v1/tdsql/discover")
    assert resp.status_code == 503
    assert "Mock" not in resp.text


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
    assert register.status_code == 409


def test_zk_api_real_discovery_redacts_password_maps_address_and_registers_server_side(tmp_path, monkeypatch):
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
    registered = {}
    monkeypatch.setattr(
        zk_discovery_service, "register_discovered",
        lambda connection_id, instance: registered.update({"id": connection_id, "instance": instance}) or connection_id,
    )

    resp = client.post("/api/v1/tdsql/discover")
    assert resp.status_code == 200
    payload = resp.json()
    item = payload["items"][0]
    assert payload["source"] == "zk"
    assert item["host"] == "119.45.220.89"
    assert item["proxy_list"] == "119.45.220.89:15002"
    assert "password" not in item
    assert len(sync_calls) == 1

    register = client.post("/api/v1/tdsql/discover/register", json={
        "discovery_id": payload["discovery_id"],
        "item_token": item["item_token"],
        "connection_id": "set_1",
    })
    assert register.status_code == 200
    assert registered["instance"]["password"] == "db-secret"
    assert registered["instance"]["host"] == "119.45.220.89"
    assert len(sync_calls) == 2
