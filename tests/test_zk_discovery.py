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
