"""
RBAC 四角色权限矩阵自动化测试套件 (v1.2)
"""
import pytest
from starlette.testclient import TestClient
from backend.main import app

STRONG_PW = "Test@2026Admin"


@pytest.fixture(scope="module")
def rbac_env():
    import os
    os.environ["AUTH_ENABLED"] = "true"
    from backend.services.auth_service import auth_service
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    auth_service.ensure_bootstrap_admin()
    auth_service.reset_password("admin", STRONG_PW, operator="test")

    conn = _get_connection()
    conn.execute("UPDATE users SET must_change_password = 0 WHERE username = 'admin'")
    conn.commit()

    for name, role in (("test_dba", "dba"), ("test_dev", "developer"), ("test_aud", "auditor")):
        auth_service.delete_user(name, operator="test")
        auth_service.create_user(name, STRONG_PW, role, operator="test")

    conn.close()
    client = TestClient(app)

    tokens = {}
    for name in ("admin", "test_dba", "test_dev", "test_aud"):
        resp = client.post("/api/v1/auth/login", json={"username": name, "password": STRONG_PW})
        tokens[name] = {"Authorization": f"Bearer {resp.json()['token']}"}

    yield client, tokens
    os.environ["AUTH_ENABLED"] = "false"


def test_rbac_admin_full_access(rbac_env):
    client, tokens = rbac_env
    # 管理员允许访问全量接口
    resp = client.get("/api/v1/auth/users", headers=tokens["admin"])
    assert resp.status_code == 200
    resp = client.get("/api/v1/admin/operation-logs", headers=tokens["admin"])
    assert resp.status_code == 200


def test_rbac_developer_restrictions(rbac_env):
    client, tokens = rbac_env
    # 开发人员禁止访问用户管理
    resp = client.get("/api/v1/auth/users", headers=tokens["test_dev"])
    assert resp.status_code == 403
    # 开发人员禁止访问操作日志
    resp = client.get("/api/v1/admin/operation-logs", headers=tokens["test_dev"])
    assert resp.status_code == 403


def test_rbac_dba_restrictions(rbac_env):
    client, tokens = rbac_env
    # DBA 禁止越权访问操作/审计日志
    resp = client.get("/api/v1/admin/operation-logs", headers=tokens["test_dba"])
    assert resp.status_code == 403, f"DBA 不应能看操作审计日志: {resp.status_code}"
    # DBA 禁止创建/管理用户
    resp = client.post("/api/v1/auth/users", headers=tokens["test_dba"], json={"username": "new_user", "password": STRONG_PW, "role": "developer"})
    assert resp.status_code == 403, f"DBA 不应能管理用户: {resp.status_code}"


def test_rbac_auditor_read_only(rbac_env):
    client, tokens = rbac_env
    # 审计员只读
    resp = client.get("/api/v1/rules", headers=tokens["test_aud"])
    assert resp.status_code == 200
    # 审计员允许查看审计日志
    resp = client.get("/api/v1/admin/operation-logs", headers=tokens["test_aud"])
    assert resp.status_code == 200
    # 写操作禁止
    resp = client.post("/api/v1/audit/sql", headers=tokens["test_aud"], json={"sql": "SELECT 1"})
    assert resp.status_code == 403


def test_rbac_gateway_log_isolation(rbac_env):
    client, tokens = rbac_env
    # 测试最长前缀匹配防遮蔽：/api/v1/gateway-log 被设置不可见时应精准拦截
    from backend.services.auth_service import set_role_permissions
    set_role_permissions("developer", {"deep-diag-gateway": 0})

    resp = client.get("/api/v1/gateway-log/reports", headers=tokens["test_dev"])
    assert resp.status_code == 403

    # 重置权限
    set_role_permissions("developer", {"deep-diag-gateway": 1})
