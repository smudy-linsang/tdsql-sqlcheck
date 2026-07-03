"""
V2.0 认证与RBAC测试（单元 + API集成）

覆盖:
- 口令哈希/强度校验/常量时间验证
- 令牌签发/验证/过期/篡改
- RBAC权限矩阵（admin/dba/developer/auditor 四角色）
- 登录/锁定/解锁/用户管理API全流程
"""
import time

import pytest
from fastapi.testclient import TestClient

from backend.services.auth_service import (
    AuthService, check_permission, hash_password, is_public_path,
    issue_token, validate_password_strength, verify_password, verify_token,
)

STRONG_PW = "Bank@2026Test"


# ══════════════════════════════════════════════════════════════
# 单元测试: 口令
# ══════════════════════════════════════════════════════════════

class TestPassword:
    def test_hash_and_verify(self):
        h, s = hash_password(STRONG_PW)
        assert verify_password(STRONG_PW, h, s)
        assert not verify_password("wrong", h, s)

    def test_different_salt_different_hash(self):
        h1, s1 = hash_password(STRONG_PW)
        h2, s2 = hash_password(STRONG_PW)
        assert s1 != s2 and h1 != h2

    def test_strength_too_short(self):
        assert validate_password_strength("Ab1!") is not None

    def test_strength_insufficient_classes(self):
        assert validate_password_strength("abcdefgh") is not None
        assert validate_password_strength("abcd1234") is not None

    def test_strength_ok(self):
        assert validate_password_strength(STRONG_PW) is None
        assert validate_password_strength("Abcd1234") is None  # 大小写+数字=三类


# ══════════════════════════════════════════════════════════════
# 单元测试: 令牌
# ══════════════════════════════════════════════════════════════

class TestToken:
    def test_issue_and_verify(self):
        t = issue_token("alice", "dba")
        payload = verify_token(t)
        assert payload["sub"] == "alice"
        assert payload["role"] == "dba"

    def test_tampered_token_rejected(self):
        t = issue_token("alice", "developer")
        body, sig = t.rsplit(".", 1)
        # 篡改payload
        assert verify_token(body + "x." + sig) is None
        # 篡改签名
        assert verify_token(body + "." + sig[:-2] + "zz") is None

    def test_expired_token_rejected(self, monkeypatch):
        monkeypatch.setenv("AUTH_TOKEN_TTL_HOURS", "0")
        t = issue_token("alice", "developer")
        time.sleep(1.1)
        assert verify_token(t) is None

    def test_garbage_token_rejected(self):
        assert verify_token("") is None
        assert verify_token("notavalidtoken") is None
        assert verify_token("a.b.c.d") is None


# ══════════════════════════════════════════════════════════════
# 单元测试: RBAC权限矩阵
# ══════════════════════════════════════════════════════════════

class TestPermissionMatrix:
    def test_admin_full_access(self):
        assert check_permission("admin", "POST", "/api/v1/auth/users")
        assert check_permission("admin", "DELETE", "/api/v1/tdsql/connections/x")
        assert check_permission("admin", "POST", "/api/v1/rulesets")

    def test_dba_no_user_management(self):
        assert not check_permission("dba", "GET", "/api/v1/auth/users")
        assert not check_permission("dba", "POST", "/api/v1/auth/users")

    def test_dba_full_platform_access(self):
        assert check_permission("dba", "POST", "/api/v1/tdsql/connections")
        assert check_permission("dba", "POST", "/api/v1/rulesets")
        assert check_permission("dba", "PUT", "/api/v1/admin/retention")
        assert check_permission("dba", "POST", "/api/v1/tdsql/slow-queries/fetch")

    def test_developer_read_and_audit_only(self):
        assert check_permission("developer", "GET", "/api/v1/dashboard/summary")
        assert check_permission("developer", "POST", "/api/v1/audit/sql")
        assert check_permission("developer", "POST", "/api/v1/gitlab/audit/diff")
        assert check_permission("developer", "POST", "/api/v1/slow-queries/analyze-explain")
        # 禁止的写操作
        assert not check_permission("developer", "POST", "/api/v1/tdsql/connections")
        assert not check_permission("developer", "POST", "/api/v1/rulesets")
        assert not check_permission("developer", "DELETE", "/api/v1/slow-queries/1")
        assert not check_permission("developer", "POST", "/api/v1/admin/retention/run")

    def test_auditor_read_only(self):
        assert check_permission("auditor", "GET", "/api/v1/admin/operation-logs")
        assert check_permission("auditor", "GET", "/api/v1/dashboard/summary")
        assert not check_permission("auditor", "POST", "/api/v1/audit/sql")
        assert not check_permission("auditor", "POST", "/api/v1/tdsql/connect")

    def test_all_roles_self_service(self):
        for role in ("admin", "dba", "developer", "auditor"):
            assert check_permission(role, "POST", "/api/v1/auth/change-password"), role
            assert check_permission(role, "POST", "/api/v1/auth/logout"), role

    def test_public_paths(self):
        assert is_public_path("/health")
        assert is_public_path("/metrics")
        assert is_public_path("/")
        assert is_public_path("/api/v1/auth/login")
        assert is_public_path("/static/vendor/vue.global.prod.js")
        assert is_public_path("/api/v1/gitlab/webhook/merge-request")
        assert not is_public_path("/api/v1/audit/sql")


# ══════════════════════════════════════════════════════════════
# API集成测试: 认证开启模式
# ══════════════════════════════════════════════════════════════

@pytest.fixture()
def auth_client(monkeypatch, tmp_path):
    """认证开启的TestClient + 预置四角色用户"""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from backend.main import app
    from backend.services.auth_service import auth_service
    from backend.services.database import ensure_db
    ensure_db()
    auth_service.ensure_bootstrap_admin()
    # 重置admin口令为已知值（绕过随机初始口令）
    auth_service.reset_password("admin", STRONG_PW, operator="test")
    # admin必须可登录：清除must_change标记
    from backend.services.database import _get_connection
    conn = _get_connection()
    conn.execute("UPDATE users SET must_change_password = 0 WHERE username = 'admin'")
    conn.commit()
    conn.close()
    # 预置其他角色
    for name, role in (("dba1", "dba"), ("dev1", "developer"), ("aud1", "auditor")):
        auth_service.delete_user(name, operator="test")
        auth_service.create_user(name, STRONG_PW, role, operator="test")
    client = TestClient(app)
    yield client
    monkeypatch.setenv("AUTH_ENABLED", "false")


def _login(client, username, password=STRONG_PW):
    resp = client.post("/api/v1/auth/login",
                       json={"username": username, "password": password})
    return resp


def _auth_headers(client, username):
    resp = _login(client, username)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


class TestAuthAPI:
    def test_unauthenticated_request_rejected(self, auth_client):
        resp = auth_client.post("/api/v1/audit/sql", json={"sql": "SELECT 1"})
        assert resp.status_code == 401

    def test_health_and_metrics_public(self, auth_client):
        assert auth_client.get("/health").status_code == 200
        assert auth_client.get("/metrics").status_code == 200

    def test_login_success_returns_token(self, auth_client):
        resp = _login(auth_client, "admin")
        assert resp.status_code == 200
        data = resp.json()
        assert data["token"]
        assert data["user"]["role"] == "admin"

    def test_login_wrong_password(self, auth_client):
        resp = _login(auth_client, "admin", "WrongPw@123")
        assert resp.status_code == 401

    def test_login_lockout_after_failures(self, auth_client, monkeypatch):
        monkeypatch.setenv("AUTH_MAX_LOGIN_FAILURES", "3")
        from backend.services.auth_service import auth_service
        auth_service.delete_user("locktest", operator="test")
        auth_service.create_user("locktest", STRONG_PW, "developer", operator="test")
        for _ in range(3):
            _login(auth_client, "locktest", "WrongPw@123")
        # 第4次即使口令正确也被锁定
        resp = _login(auth_client, "locktest")
        assert resp.status_code == 401
        assert "锁定" in resp.json()["detail"]
        # 管理员解锁后可登录
        headers = _auth_headers(auth_client, "admin")
        resp = auth_client.post("/api/v1/auth/users/locktest/unlock", headers=headers)
        assert resp.status_code == 200
        resp = _login(auth_client, "locktest")
        assert resp.status_code == 200

    def test_authenticated_audit_works(self, auth_client):
        headers = _auth_headers(auth_client, "dev1")
        resp = auth_client.post("/api/v1/audit/sql",
                                json={"sql": "SELECT * FROM t_user"}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["passed"] is False  # R012 SELECT *

    def test_developer_cannot_manage_connections(self, auth_client):
        headers = _auth_headers(auth_client, "dev1")
        resp = auth_client.post("/api/v1/tdsql/connections", headers=headers,
                                json={"host": "1.2.3.4", "port": 3306,
                                      "user": "x", "password": "y"})
        assert resp.status_code == 403

    def test_auditor_read_only(self, auth_client):
        headers = _auth_headers(auth_client, "aud1")
        # 可读
        resp = auth_client.get("/api/v1/dashboard/summary", headers=headers)
        assert resp.status_code == 200
        # 不可写
        resp = auth_client.post("/api/v1/audit/sql",
                                json={"sql": "SELECT 1"}, headers=headers)
        assert resp.status_code == 403

    def test_user_management_admin_only(self, auth_client):
        dba_headers = _auth_headers(auth_client, "dba1")
        resp = auth_client.get("/api/v1/auth/users", headers=dba_headers)
        assert resp.status_code == 403
        admin_headers = _auth_headers(auth_client, "admin")
        resp = auth_client.get("/api/v1/auth/users", headers=admin_headers)
        assert resp.status_code == 200
        usernames = [u["username"] for u in resp.json()["users"]]
        assert "admin" in usernames and "dev1" in usernames

    def test_create_user_weak_password_rejected(self, auth_client):
        headers = _auth_headers(auth_client, "admin")
        resp = auth_client.post("/api/v1/auth/users", headers=headers,
                                json={"username": "weakpw", "password": "123",
                                      "role": "developer"})
        assert resp.status_code == 400

    def test_change_password_flow(self, auth_client):
        from backend.services.auth_service import auth_service
        auth_service.delete_user("pwchange", operator="test")
        auth_service.create_user("pwchange", STRONG_PW, "developer", operator="test")
        headers = _auth_headers(auth_client, "pwchange")
        new_pw = "NewBank@2026X"
        resp = auth_client.post("/api/v1/auth/change-password", headers=headers,
                                json={"old_password": STRONG_PW, "new_password": new_pw})
        assert resp.status_code == 200
        # 旧口令失效，新口令生效
        assert _login(auth_client, "pwchange", STRONG_PW).status_code == 401
        assert _login(auth_client, "pwchange", new_pw).status_code == 200

    def test_cannot_delete_last_admin(self, auth_client):
        from backend.services.auth_service import auth_service
        # 仅保留admin一个管理员时删除应被拒绝
        headers = _auth_headers(auth_client, "admin")
        # 先删掉可能存在的其他admin
        for u in auth_service.list_users():
            if u["role"] == "admin" and u["username"] != "admin":
                auth_service.delete_user(u["username"], operator="test")
        err = auth_service.delete_user("admin", operator="test")
        assert err is not None and "管理员" in err

    def test_operation_log_records_user(self, auth_client):
        headers = _auth_headers(auth_client, "dev1")
        auth_client.post("/api/v1/audit/sql",
                         json={"sql": "SELECT id FROM t_a WHERE id=1"}, headers=headers)
        admin_headers = _auth_headers(auth_client, "admin")
        resp = auth_client.get("/api/v1/admin/operation-logs?operator=dev1",
                               headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] > 0

    def test_disabled_user_cannot_login(self, auth_client):
        from backend.services.auth_service import auth_service
        auth_service.delete_user("disme", operator="test")
        auth_service.create_user("disme", STRONG_PW, "developer", operator="test")
        headers = _auth_headers(auth_client, "admin")
        resp = auth_client.put("/api/v1/auth/users/disme", headers=headers,
                               json={"status": "disabled"})
        assert resp.status_code == 200
        resp = _login(auth_client, "disme")
        assert resp.status_code == 401
