"""
RBAC V3.1 实例管理权限收敛与全模块解耦自动化测试套件
覆盖: RBAC-01~09, DATA-01~03, UI-06 (后端状态同步)
"""
import pytest
from starlette.testclient import TestClient
from backend.main import app
from backend.services.connection_registry import registry, ConnectionNotFoundError
from backend.services.auth_service import auth_service, set_role_permissions
from backend.services.database import _get_connection, ensure_db

STRONG_PW = "Test@2026Admin"


@pytest.fixture(scope="module")
def rbac_v3_env():
    import os
    os.environ["AUTH_ENABLED"] = "true"
    ensure_db()
    auth_service.ensure_bootstrap_admin()
    auth_service.reset_password("admin", STRONG_PW, operator="test")

    conn = _get_connection()
    conn.execute("UPDATE users SET must_change_password = 0 WHERE username = 'admin'")
    conn.commit()

    test_roles = [
        ("test_dba", "dba"),
        ("test_dev", "developer"),
        ("test_aud", "auditor"),
        ("test_custom", "custom_role"),
    ]

    # 创建自定义角色
    try:
        from backend.services.auth_service import create_custom_role
        create_custom_role("custom_role", "自定义角色")
    except Exception:
        pass

    for name, role in test_roles:
        auth_service.delete_user(name, operator="test")
        auth_service.create_user(name, STRONG_PW, role, operator="test")

    conn = _get_connection()
    conn.execute("UPDATE users SET must_change_password = 0")
    conn.commit()
    conn.close()

    # 初始化测试连接
    test_conn_id = "test_rbac_conn_1"
    try:
        registry.delete_saved(test_conn_id, operator="test")
    except Exception:
        pass

    registry.save_connection(
        conn_id=test_conn_id,
        name="测试集群主库",
        host="127.0.0.1",
        port=3306,
        username="tdsql_user",
        password="SecretPassword123!",
        database="test_db",
        is_default=True,
        is_distributed=True,
        description="RBAC测试用例专用连接",
        monitor_host="127.0.0.1",
        monitor_port=15001,
        monitor_user="monitor_user",
        monitor_password="MonitorSecret123!",
        monitor_db="tdsqlpcloud_monitor",
        operator="test"
    )

    client = TestClient(app)
    tokens = {}
    for name in ("admin", "test_dba", "test_dev", "test_aud", "test_custom"):
        resp = client.post("/api/v1/auth/login", json={"username": name, "password": STRONG_PW})
        assert resp.status_code == 200, f"Login failed for {name}: {resp.text}"
        tokens[name] = {"Authorization": f"Bearer {resp.json()['token']}"}

    yield client, tokens, test_conn_id

    try:
        registry.delete_saved(test_conn_id, operator="test")
    except Exception:
        pass
    os.environ["AUTH_ENABLED"] = "false"


def test_rbac_01_anonymous_access(rbac_v3_env):
    """RBAC-01: 未登录或无有效 Token 访问 /connections/options 返回 401"""
    client, _, _ = rbac_v3_env
    resp = client.get("/api/v1/tdsql/connections/options")
    assert resp.status_code == 401
    resp_invalid = client.get("/api/v1/tdsql/connections/options", headers={"Authorization": "Bearer invalid_token"})
    assert resp_invalid.status_code == 401


def test_rbac_02_all_roles_read_options_without_menu(rbac_v3_env):
    """RBAC-02: 无论是否分配 instances 菜单，所有已认证角色均可访问 /connections/options"""
    client, tokens, _ = rbac_v3_env
    # 剥夺 developer, auditor, custom_role 的 instances 菜单
    for role in ("developer", "auditor", "custom_role"):
        set_role_permissions(role, {"instances": 0})

    for user in ("admin", "test_dba", "test_dev", "test_aud", "test_custom"):
        resp = client.get("/api/v1/tdsql/connections/options", headers=tokens[user])
        assert resp.status_code == 200, f"User {user} should be able to get options: {resp.text}"
        d = resp.json()
        assert "connections" in d
        assert len(d["connections"]) >= 1


def test_rbac_03_connections_menu_dependent(rbac_v3_env):
    """RBAC-03: /connections 全量管理列表依赖 instances 菜单"""
    client, tokens, _ = rbac_v3_env
    # 剥夺 developer 的 instances 菜单
    set_role_permissions("developer", {"instances": 0})
    resp_denied = client.get("/api/v1/tdsql/connections", headers=tokens["test_dev"])
    assert resp_denied.status_code == 403

    # 授予 developer 的 instances 菜单后可读
    set_role_permissions("developer", {"instances": 1})
    resp_allowed = client.get("/api/v1/tdsql/connections", headers=tokens["test_dev"])
    assert resp_allowed.status_code == 200


def test_rbac_04_non_manager_write_rejected(rbac_v3_env):
    """RBAC-04: 普通角色（即使拥有 instances 菜单）调用所有写操作均返回 403"""
    client, tokens, conn_id = rbac_v3_env
    set_role_permissions("developer", {"instances": 1})

    # 新建连接
    resp_create = client.post("/api/v1/tdsql/connections", headers=tokens["test_dev"], json={
        "name": "dev_illegal", "host": "127.0.0.1", "port": 3306, "username": "u", "password": "p"
    })
    assert resp_create.status_code == 403

    # 更新连接
    resp_update = client.put(f"/api/v1/tdsql/connections/{conn_id}", headers=tokens["test_dev"], json={
        "name": "dev_illegal_mod", "host": "127.0.0.1", "port": 3306, "username": "u", "password": "p"
    })
    assert resp_update.status_code == 403

    # 删除连接
    resp_delete = client.delete(f"/api/v1/tdsql/connections/{conn_id}", headers=tokens["test_dev"])
    assert resp_delete.status_code == 403

    # 设为默认
    resp_default = client.post(f"/api/v1/tdsql/connections/{conn_id}/set-default", headers=tokens["test_dev"])
    assert resp_default.status_code == 403

    # 显式激活
    resp_connect = client.post(f"/api/v1/tdsql/connections/{conn_id}/connect", headers=tokens["test_dev"])
    assert resp_connect.status_code == 403

    # 断开连接
    resp_disc = client.post(f"/api/v1/tdsql/disconnect?connection_id={conn_id}", headers=tokens["test_dev"])
    assert resp_disc.status_code == 403


def test_rbac_05_test_connection_restrictions(rbac_v3_env):
    """RBAC-05: test-connection 仅限 POST 且仅限 admin/dba"""
    client, tokens, _ = rbac_v3_env

    # GET 访问应该被拒绝（404 或 405 Method Not Allowed）
    resp_get = client.get("/api/v1/tdsql/test-connection?host=127.0.0.1", headers=tokens["admin"])
    assert resp_get.status_code in (404, 405)

    # 普通角色 POST 访问返回 403
    resp_post_dev = client.post("/api/v1/tdsql/test-connection", headers=tokens["test_dev"], json={
        "host": "127.0.0.1", "port": 3306, "username": "root", "password": "pw"
    })
    assert resp_post_dev.status_code == 403

    # admin/dba POST 访问通过鉴权（由于 127.0.0.1 端口未开真实 MySQL，返回 status: error 但 HTTP 状态码为 200）
    resp_post_admin = client.post("/api/v1/tdsql/test-connection", headers=tokens["admin"], json={
        "host": "127.0.0.1", "port": 3306, "username": "root", "password": "pw"
    })
    assert resp_post_admin.status_code == 200
    assert "status" in resp_post_admin.json()


def test_rbac_06_monitor_probe_defense(rbac_v3_env):
    """RBAC-06: 有副作用的探测接口仅限 admin/dba"""
    client, tokens, conn_id = rbac_v3_env
    set_role_permissions("developer", {"instances": 1})

    resp_probe = client.get(f"/api/v1/tdsql/connections/{conn_id}/probe", headers=tokens["test_dev"])
    assert resp_probe.status_code == 403

    resp_mprobe = client.post(f"/api/v1/tdsql/connections/{conn_id}/monitor-probe", headers=tokens["test_dev"])
    assert resp_mprobe.status_code == 403


def test_rbac_07_dba_manager_permissions(rbac_v3_env):
    """RBAC-07: DBA 角色允许进行常规管理写操作"""
    client, tokens, _ = rbac_v3_env
    dba_conn_id = "test_dba_created_conn"
    try:
        registry.delete_saved(dba_conn_id, operator="test")
    except Exception:
        pass

    # DBA 创建连接
    resp_create = client.post("/api/v1/tdsql/connections", headers=tokens["test_dba"], json={
        "id": dba_conn_id, "name": "DBA专用测试库", "host": "127.0.0.2", "port": 3306, "username": "u", "password": "p"
    })
    assert resp_create.status_code == 200

    # DBA 删除连接
    resp_delete = client.delete(f"/api/v1/tdsql/connections/{dba_conn_id}", headers=tokens["test_dba"])
    assert resp_delete.status_code == 200


def test_rbac_08_dba_admin_only_rejection(rbac_v3_env):
    """RBAC-08: DBA 尝试调用 admin-only 端点（instance-type-lock、discover/config）返回 403"""
    client, tokens, conn_id = rbac_v3_env
    resp_lock = client.put(f"/api/v1/tdsql/connections/{conn_id}/instance-type-lock", headers=tokens["test_dba"], json={
        "locked": True, "instance_type": "centralized", "reason": "DBA试图锁定"
    })
    assert resp_lock.status_code == 403

    resp_cfg = client.get("/api/v1/tdsql/discover/config", headers=tokens["test_dba"])
    assert resp_cfg.status_code == 403


def test_rbac_09_deprecated_register_gone(rbac_v3_env):
    """RBAC-09: 废弃的 ZK 注册接口返回 410 Gone"""
    client, tokens, _ = rbac_v3_env
    resp = client.post("/api/v1/tdsql/discover/register", headers=tokens["admin"], json={
        "discovery_id": "disc_1", "item_token": "tok_1", "connection_id": "c1"
    })
    assert resp.status_code == 410


def test_data_01_options_allowlist(rbac_v3_env):
    """DATA-01: /connections/options 响应严格遵循 8 字段白名单"""
    client, tokens, _ = rbac_v3_env
    resp = client.get("/api/v1/tdsql/connections/options", headers=tokens["test_dev"])
    assert resp.status_code == 200
    data = resp.json()
    assert "connections" in data
    assert len(data["connections"]) > 0

    allowed_keys = {"id", "name", "host", "port", "database", "effective_instance_type", "is_default", "active"}
    for conn in data["connections"]:
        assert set(conn.keys()) == allowed_keys, f"Keys mismatch: {set(conn.keys())} vs {allowed_keys}"


def test_data_02_options_no_sensitive_fields(rbac_v3_env):
    """DATA-02: /connections/options 绝不包含任何密码或密文"""
    client, tokens, _ = rbac_v3_env
    resp = client.get("/api/v1/tdsql/connections/options", headers=tokens["test_dev"])
    text = resp.text.lower()
    for sensitive in ("secretpassword", "monitorsecret", "password_encrypted", "monitor_password_encrypted", "monitor_user"):
        assert sensitive not in text, f"Found sensitive string in options response: {sensitive}"


def test_data_03_connections_dual_password_strip(rbac_v3_env):
    """DATA-03: /connections 全量列表绝不包含 password_encrypted 或 monitor_password_encrypted"""
    client, tokens, _ = rbac_v3_env
    resp = client.get("/api/v1/tdsql/connections", headers=tokens["admin"])
    assert resp.status_code == 200
    for conn in resp.json()["connections"]:
        assert "password_encrypted" not in conn
        assert "monitor_password_encrypted" not in conn
        assert conn.get("password") == "***"
        if conn.get("monitor_user"):
            assert conn.get("monitor_password") == "***"


def test_session_isolation_and_role_switching(rbac_v3_env):
    """DEFECT-01 回归测试: 跨角色切换时后端权限隔离与可见菜单一致性"""
    client, tokens, _ = rbac_v3_env
    # 确保 developer 角色无 instances 菜单
    set_role_permissions("developer", {"instances": 0})

    # 1. 模拟 DBA 登录并获取全量管理列表与菜单
    resp_dba_menus = client.get("/api/v1/auth/visible-menus", headers=tokens["test_dba"])
    assert resp_dba_menus.status_code == 200
    assert "instances" in resp_dba_menus.json()["menus"]
    resp_dba_conns = client.get("/api/v1/tdsql/connections", headers=tokens["test_dba"])
    assert resp_dba_conns.status_code == 200

    # 2. 模拟同会话登出后，普通角色 developer 登录
    resp_dev_menus = client.get("/api/v1/auth/visible-menus", headers=tokens["test_dev"])
    assert resp_dev_menus.status_code == 200
    assert "instances" not in resp_dev_menus.json()["menus"]

    # 3. developer 绝对无法访问 /connections 全量管理端点
    resp_dev_conns = client.get("/api/v1/tdsql/connections", headers=tokens["test_dev"])
    assert resp_dev_conns.status_code == 403

    # 4. developer 可以正常访问 /connections/options 精简下拉列表
    resp_dev_opts = client.get("/api/v1/tdsql/connections/options", headers=tokens["test_dev"])
    assert resp_dev_opts.status_code == 200
    assert "connections" in resp_dev_opts.json()


def test_frontend_security_contract():
    """DEFECT-01 前端防御性契约测试: 静态断言 app.js 中的安全状态清理与时序控制"""
    from pathlib import Path
    app_js_path = Path("frontend/static/js/app.js")
    assert app_js_path.exists(), "app.js must exist"
    content = app_js_path.read_text(encoding="utf-8")

    # 契约 1: visibleMenus 默认初始化必须为空 Set
    assert "const visibleMenus=ref(new Set());" in content or "const visibleMenus = ref(new Set());" in content

    # 契约 2: 必须包含统一的 clearRoleScopedState 角色态清理函数
    assert "clearRoleScopedState" in content

    # 契约 3: doLogin 中必须先 await loadVisibleMenus() 再应用用户与页面
    assert "await loadVisibleMenus()" in content

    # 契约 4: loadManagedConnections 必须具备 fail-closed 特性
    assert "managedConnections.value=[];" in content

