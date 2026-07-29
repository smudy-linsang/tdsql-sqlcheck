"""G10 ZK 自动发现单元测试与集成测试"""
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.services.zk_discovery_service import zk_discovery_service
from backend.services.database import _get_connection

client = TestClient(app)


def test_zk_discovery_service_mock():
    """测试 ZK 发现服务的 Mock 模式"""
    results = zk_discovery_service.discover(
        zk_server="127.0.0.1:2118",
        zk_auth_user="test",
        zk_auth_password="password",
        force_mock=True
    )
    assert len(results) == 3
    assert results[0]["service_name"] == "TDSQL-Set-1(合约库)"
    assert results[0]["host"] == "127.0.0.1"
    assert results[0]["port"] == 15005
    assert results[0]["user"] == "tdsqlsys_normal"


# ═══ V1.5.1：--with-type 列解析与形态同步 ═══

def test_parse_csv_11_columns():
    """11 列（--with-status --with-type）：形态列正确解析与映射"""
    csv_text = ("# header\n"
                "集中式实例,10.206.0.4,15002,u,p,ALL,0,运营中,"
                "noshard,set_1782130875_4,10.206.0.4:15002;10.206.0.8:15002\n")
    item = zk_discovery_service.parse_csv(csv_text)[0]
    assert item["instance_kind"] == "noshard"
    assert item["instance_type"] == "centralized"
    assert item["instance_id"] == "set_1782130875_4"
    assert item["proxy_list"] == "10.206.0.4:15002;10.206.0.8:15002"


def test_parse_csv_groupshard_maps_distributed():
    csv_text = ("分布式实例,10.206.0.8,15005,u,p,ALL,0,运营中,"
                "groupshard,group_1782132247_10,10.206.0.8:15005\n")
    item = zk_discovery_service.parse_csv(csv_text)[0]
    assert item["instance_type"] == "distributed"


def test_parse_csv_backward_compatible():
    """6 列 / 8 列旧格式必须继续可解析（新列一律追加在末尾）"""
    assert zk_discovery_service.parse_csv("a,h,15002,u,p,ALL\n")[0]["host"] == "h"
    item8 = zk_discovery_service.parse_csv("a,h,15002,u,p,ALL,1,已隔离\n")[0]
    assert item8["status_text"] == "已隔离"
    assert "instance_kind" not in item8


def test_unknown_kind_does_not_guess():
    """未知形态不得静默映射 —— 那是本次事故的同类错误"""
    item = zk_discovery_service.parse_csv(
        "n,h,1,u,p,ALL,0,ok,brand_new_kind,x_1,h:1\n")[0]
    assert item["instance_type"] is None
    assert item["instance_kind"] == "brand_new_kind"


def test_sync_matches_any_proxy_of_instance():
    """系统登记的是同实例的另一个网关时也必须匹配上（按 proxy_list 全集）。

    ZK CSV 选中 10.206.0.4:15002，系统登记的是 10.206.0.8:15002，
    proxy_list 含两者 → 应同步成功。
    """
    conn = _get_connection()
    conn_id = "v151_sync_test_conn"
    try:
        conn.execute("DELETE FROM tdsql_connections WHERE id = ?", (conn_id,))
        conn.execute(
            "INSERT INTO tdsql_connections "
            "(id, name, host, port, username, password_encrypted, `database`) "
            "VALUES (?,?,?,?,?,?,?)",
            (conn_id, "同步测试实例", "10.206.0.8", 15002, "u", "", "ALL"))
        conn.commit()
        conn.close()

        synced = zk_discovery_service.sync_instance_kinds([{
            "host": "10.206.0.4", "port": 15002,
            "instance_kind": "noshard",
            "instance_id": "set_1782130875_4",
            "proxy_list": "10.206.0.4:15002;10.206.0.8:15002",
        }])
        assert synced >= 1

        conn = _get_connection()
        row = conn.execute(
            "SELECT zk_instance_kind, zk_instance_id, zk_synced_at "
            "FROM tdsql_connections WHERE id = ?", (conn_id,)).fetchone()
        row = dict(row)
        assert row["zk_instance_kind"] == "noshard"
        assert row["zk_instance_id"] == "set_1782130875_4"
        assert row["zk_synced_at"] is not None
    finally:
        try:
            conn.execute("DELETE FROM tdsql_connections WHERE id = ?", (conn_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass


def test_sync_skips_entries_without_kind():
    """无形态信息的发现条目不参与同步（不得凭空值覆写）"""
    assert zk_discovery_service.sync_instance_kinds(
        [{"host": "1.2.3.4", "port": 1, "instance_kind": ""}]) == 0


def test_zk_discovery_api():
    """测试 ZK 发现 API 接口"""
    resp = client.post("/api/v1/tdsql/discover", json={
        "zk_server": "127.0.0.1:2118",
        "zk_auth_user": "test",
        "zk_auth_password": "password",
        "force_mock": True
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert data[0]["service_name"] == "TDSQL-Set-1(合约库)"


def test_zk_register_api():
    """测试 ZK 自动发现实例登记 API"""
    # 清理已存在的连接，防止冲突
    conn = _get_connection()
    conn.execute("DELETE FROM tdsql_connections WHERE id = 'TDSQL-Set-1(合约库)'")
    conn.commit()
    conn.close()

    resp = client.post("/api/v1/tdsql/discover/register", json={
        "connection_id": "TDSQL-Set-1(合约库)",
        "service_name": "TDSQL-Set-1(合约库)",
        "host": "127.0.0.1",
        "port": 15005,
        "user": "tdsqlsys_normal",
        "password": "mock_password_set1",
        "database": "ALL"
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

    # 验证是否存入数据库
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT host, port FROM tdsql_connections WHERE id = 'TDSQL-Set-1(合约库)'")
    row = cursor.fetchone()
    assert row is not None
    assert row["host"] == "127.0.0.1"
    assert row["port"] == 15005
    conn.close()
