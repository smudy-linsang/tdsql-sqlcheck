"""
在线元数据提取与文件审核 API 功能测试 (V1.2 新增)
"""
import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_extract_and_audit_unauthorized():
    """未登录或无 Token 时响应状态非 200"""
    resp = client.post("/api/v1/audit/extract-and-audit", json={"connection_id": "test"})
    assert resp.status_code in (401, 403, 404)


def test_extract_and_audit_invalid_conn():
    """选择无效连接 ID 时应返回 404"""
    login_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminpassword"})
    token = login_resp.json().get("token", "")
    
    resp = client.post(
        "/api/v1/audit/extract-and-audit",
        headers={"Authorization": f"Bearer {token}"},
        json={"connection_id": "non_existent_conn_9999"}
    )
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]
