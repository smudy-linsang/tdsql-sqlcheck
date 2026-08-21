"""
RBAC V3.1 实例管理权限收敛与全模块解耦自动化测试套件
覆盖: RBAC-01~09, DATA-01~03, UI-06 (后端状态同步)
"""
import os
from dataclasses import dataclass
from uuid import uuid4

import pytest
from starlette.testclient import TestClient
from backend.main import app
from backend.services.connection_registry import registry, ConnectionNotFoundError
from backend.services.auth_service import (
    auth_service,
    create_custom_role,
    delete_role,
    issue_token,
    set_role_permissions,
)
from backend.services.database import _get_connection, ensure_db

STRONG_PW = "Test@2026Admin"


@dataclass(frozen=True)
class RbacV3Context:
    client: TestClient
    tokens: dict[str, dict[str, str]]
    conn_id: str
    dba_conn_id: str
    custom_role: str
    usernames: dict[str, str]
    shared_admin_before: dict | None
    shared_admin_token: str | None


def _fetch_user_row(username: str) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@pytest.fixture(scope="module")
def rbac_v3_env():
    previous_auth_enabled = os.environ.get("AUTH_ENABLED")
    os.environ["AUTH_ENABLED"] = "true"
    ensure_db()

    run_id = uuid4().hex[:10]
    operator = f"rbac_{run_id}"
    custom_role = f"rbac_{run_id}_role"
    usernames = {
        "admin": f"rbac_{run_id}_admin",
        "test_dba": f"rbac_{run_id}_dba",
        "test_dev": f"rbac_{run_id}_dev",
        "test_aud": f"rbac_{run_id}_aud",
        "test_custom": f"rbac_{run_id}_view",
    }
    test_roles = {
        "admin": "admin",
        "test_dba": "dba",
        "test_dev": "developer",
        "test_aud": "auditor",
        "test_custom": custom_role,
    }
    test_conn_id = f"rbac_{run_id}_base"
    dba_conn_id = f"rbac_{run_id}_dba_conn"
    created_users: list[str] = []
    created_role = False
    client = TestClient(app)

    # 真实 admin 仅做只读快照，并签发一个不改变数据库状态的旧令牌探针。
    shared_admin_before = _fetch_user_row("admin")
    shared_admin_token = None
    if shared_admin_before:
        shared_admin_token = issue_token(
            "admin",
            shared_admin_before["role"],
            shared_admin_before.get("token_version", 0),
        )

    try:
        role_result = create_custom_role(custom_role, f"RBAC临时只读角色-{run_id}")
        assert "error" not in role_result, role_result.get("error")
        created_role = True

        for alias, role in test_roles.items():
            username = usernames[alias]
            _, error = auth_service.create_user(
                username,
                STRONG_PW,
                role,
                display_name=f"RBAC临时用户-{alias}",
                operator=operator,
            )
            assert error is None, f"创建临时用户 {username} 失败: {error}"
            created_users.append(username)

        placeholders = ",".join("?" for _ in created_users)
        conn = _get_connection()
        try:
            conn.execute(
                f"UPDATE users SET must_change_password = 0 WHERE username IN ({placeholders})",
                tuple(created_users),
            )
            conn.commit()
        finally:
            conn.close()

        registry.save_connection(
            conn_id=test_conn_id,
            name=f"RBAC临时测试实例-{run_id}",
            host="127.0.0.1",
            port=3306,
            username="tdsql_user",
            password="SecretPassword123!",
            database=f"rbac_{run_id}",
            is_default=False,
            is_distributed=True,
            description="RBAC测试用例专用临时连接",
            monitor_host="127.0.0.1",
            monitor_port=15001,
            monitor_user="monitor_user",
            monitor_password="MonitorSecret123!",
            monitor_db="tdsqlpcloud_monitor",
            operator=operator,
        )

        tokens = {}
        for alias, username in usernames.items():
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": STRONG_PW},
            )
            assert resp.status_code == 200, f"Login failed for {username}: {resp.text}"
            tokens[alias] = {"Authorization": f"Bearer {resp.json()['token']}"}

        # fixture setup 本身就不得改写共享 admin 的任何列。
        assert _fetch_user_row("admin") == shared_admin_before

        yield RbacV3Context(
            client=client,
            tokens=tokens,
            conn_id=test_conn_id,
            dba_conn_id=dba_conn_id,
            custom_role=custom_role,
            usernames=usernames,
            shared_admin_before=shared_admin_before,
            shared_admin_token=shared_admin_token,
        )
    finally:
        cleanup_errors: list[str] = []

        def capture_cleanup(label, action):
            try:
                action()
            except Exception as exc:  # teardown 必须显式暴露清理失败
                cleanup_errors.append(f"{label}: {exc}")

        def delete_connection(conn_id: str):
            try:
                registry.delete_saved(conn_id, operator=operator)
            except ConnectionNotFoundError:
                return

        capture_cleanup("删除基础临时实例", lambda: delete_connection(test_conn_id))
        capture_cleanup("删除DBA临时实例", lambda: delete_connection(dba_conn_id))

        def delete_test_user(username: str):
            error = auth_service.delete_user(username, operator=operator)
            if error is None or error == "用户不存在":
                return
            # 空白测试库中临时 admin 可能是唯一管理员，只允许按精确用户名兜底删除。
            if username == usernames["admin"] and ("最后一个" in error or "至少一个" in error):
                conn = _get_connection()
                try:
                    conn.execute("DELETE FROM users WHERE username = ?", (username,))
                    conn.commit()
                finally:
                    conn.close()
                return
            raise AssertionError(error)

        for username in reversed(created_users):
            capture_cleanup(f"删除临时用户 {username}", lambda value=username: delete_test_user(value))

        if created_role:
            def remove_role():
                result = delete_role(custom_role)
                if "error" in result and result["error"] != "角色不存在":
                    raise AssertionError(result["error"])
            capture_cleanup("删除临时角色", remove_role)

        def verify_and_clean_metadata():
            conn = _get_connection()
            try:
                all_ids = [operator, custom_role, test_conn_id, dba_conn_id, *usernames.values()]
                placeholders = ",".join("?" for _ in all_ids)
                conn.execute(
                    f"DELETE FROM operation_logs WHERE operator IN ({placeholders}) "
                    f"OR target_id IN ({placeholders})",
                    tuple(all_ids + all_ids),
                )
                conn.commit()

                remaining_users = conn.execute(
                    f"SELECT COUNT(*) AS c FROM users WHERE username IN ({','.join('?' for _ in usernames)})",
                    tuple(usernames.values()),
                ).fetchone()["c"]
                remaining_role = conn.execute(
                    "SELECT COUNT(*) AS c FROM roles WHERE role_id = ?", (custom_role,)
                ).fetchone()["c"]
                remaining_connections = conn.execute(
                    "SELECT COUNT(*) AS c FROM tdsql_connections WHERE id IN (?, ?)",
                    (test_conn_id, dba_conn_id),
                ).fetchone()["c"]
                remaining_logs = conn.execute(
                    f"SELECT COUNT(*) AS c FROM operation_logs WHERE operator IN ({placeholders}) "
                    f"OR target_id IN ({placeholders})",
                    tuple(all_ids + all_ids),
                ).fetchone()["c"]
                if remaining_users or remaining_role or remaining_connections or remaining_logs:
                    raise AssertionError(
                        "临时资源残留: "
                        f"users={remaining_users}, roles={remaining_role}, "
                        f"connections={remaining_connections}, logs={remaining_logs}"
                    )
            finally:
                conn.close()

        capture_cleanup("验证临时资源零残留", verify_and_clean_metadata)

        current_admin = _fetch_user_row("admin")
        if current_admin != shared_admin_before:
            cleanup_errors.append("共享 admin 整行状态发生变化")
        if shared_admin_token:
            response = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {shared_admin_token}"},
            )
            if response.status_code != 200:
                cleanup_errors.append(
                    f"共享 admin 测试前签发的令牌失效: HTTP {response.status_code}"
                )

        if previous_auth_enabled is None:
            os.environ.pop("AUTH_ENABLED", None)
        else:
            os.environ["AUTH_ENABLED"] = previous_auth_enabled

        if cleanup_errors:
            raise AssertionError("RBAC fixture teardown 失败:\n" + "\n".join(cleanup_errors))


def test_rbac_01_anonymous_access(rbac_v3_env):
    """RBAC-01: 未登录或无有效 Token 访问 /connections/options 返回 401"""
    client = rbac_v3_env.client
    resp = client.get("/api/v1/tdsql/connections/options")
    assert resp.status_code == 401
    resp_invalid = client.get("/api/v1/tdsql/connections/options", headers={"Authorization": "Bearer invalid_token"})
    assert resp_invalid.status_code == 401


def test_fixture_never_mutates_shared_admin(rbac_v3_env):
    """测试夹具不得登录、重置或更新真实 admin，旧令牌必须持续有效。"""
    assert _fetch_user_row("admin") == rbac_v3_env.shared_admin_before
    if rbac_v3_env.shared_admin_token:
        response = rbac_v3_env.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {rbac_v3_env.shared_admin_token}"},
        )
        assert response.status_code == 200, response.text


def test_rbac_02_all_roles_read_options_without_menu(rbac_v3_env):
    """RBAC-02: 无论是否分配 instances 菜单，所有已认证角色均可访问 /connections/options"""
    client, tokens = rbac_v3_env.client, rbac_v3_env.tokens
    # 唯一临时自定义角色保持 instances=0，不改写任何内置角色权限。
    set_role_permissions(rbac_v3_env.custom_role, {"instances": 0})

    for user in ("admin", "test_dba", "test_dev", "test_aud", "test_custom"):
        resp = client.get("/api/v1/tdsql/connections/options", headers=tokens[user])
        assert resp.status_code == 200, f"User {user} should be able to get options: {resp.text}"
        d = resp.json()
        assert "connections" in d
        assert len(d["connections"]) >= 1


def test_rbac_03_connections_menu_dependent(rbac_v3_env):
    """RBAC-03: /connections 全量管理列表依赖 instances 菜单"""
    client, tokens = rbac_v3_env.client, rbac_v3_env.tokens
    try:
        set_role_permissions(rbac_v3_env.custom_role, {"instances": 0})
        resp_denied = client.get("/api/v1/tdsql/connections", headers=tokens["test_custom"])
        assert resp_denied.status_code == 403

        set_role_permissions(rbac_v3_env.custom_role, {"instances": 1})
        resp_allowed = client.get("/api/v1/tdsql/connections", headers=tokens["test_custom"])
        assert resp_allowed.status_code == 200
    finally:
        set_role_permissions(rbac_v3_env.custom_role, {"instances": 0})


def test_rbac_04_non_manager_write_rejected(rbac_v3_env):
    """RBAC-04: 普通角色（即使拥有 instances 菜单）调用所有写操作均返回 403"""
    client, tokens, conn_id = rbac_v3_env.client, rbac_v3_env.tokens, rbac_v3_env.conn_id
    try:
        set_role_permissions(rbac_v3_env.custom_role, {"instances": 1})

        # 新建连接
        resp_create = client.post("/api/v1/tdsql/connections", headers=tokens["test_custom"], json={
            "name": "dev_illegal", "host": "127.0.0.1", "port": 3306, "username": "u", "password": "p"
        })
        assert resp_create.status_code == 403

        # 更新连接
        resp_update = client.put(f"/api/v1/tdsql/connections/{conn_id}", headers=tokens["test_custom"], json={
            "name": "dev_illegal_mod", "host": "127.0.0.1", "port": 3306, "username": "u", "password": "p"
        })
        assert resp_update.status_code == 403

        # 删除连接
        resp_delete = client.delete(f"/api/v1/tdsql/connections/{conn_id}", headers=tokens["test_custom"])
        assert resp_delete.status_code == 403

        # 设为默认
        resp_default = client.post(f"/api/v1/tdsql/connections/{conn_id}/set-default", headers=tokens["test_custom"])
        assert resp_default.status_code == 403

        # 显式激活
        resp_connect = client.post(f"/api/v1/tdsql/connections/{conn_id}/connect", headers=tokens["test_custom"])
        assert resp_connect.status_code == 403

        # 断开连接
        resp_disc = client.post(f"/api/v1/tdsql/disconnect?connection_id={conn_id}", headers=tokens["test_custom"])
        assert resp_disc.status_code == 403
    finally:
        set_role_permissions(rbac_v3_env.custom_role, {"instances": 0})


def test_rbac_05_test_connection_restrictions(rbac_v3_env):
    """RBAC-05: test-connection 仅限 POST 且仅限 admin/dba"""
    client, tokens = rbac_v3_env.client, rbac_v3_env.tokens

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
    client, tokens, conn_id = rbac_v3_env.client, rbac_v3_env.tokens, rbac_v3_env.conn_id
    try:
        set_role_permissions(rbac_v3_env.custom_role, {"instances": 1})

        resp_probe = client.get(f"/api/v1/tdsql/connections/{conn_id}/probe", headers=tokens["test_custom"])
        assert resp_probe.status_code == 403

        resp_mprobe = client.post(f"/api/v1/tdsql/connections/{conn_id}/monitor-probe", headers=tokens["test_custom"])
        assert resp_mprobe.status_code == 403
    finally:
        set_role_permissions(rbac_v3_env.custom_role, {"instances": 0})


def test_rbac_07_dba_manager_permissions(rbac_v3_env):
    """RBAC-07: DBA 角色允许进行常规管理写操作"""
    client, tokens = rbac_v3_env.client, rbac_v3_env.tokens
    dba_conn_id = rbac_v3_env.dba_conn_id
    try:
        try:
            registry.delete_saved(dba_conn_id, operator=rbac_v3_env.usernames["test_dba"])
        except ConnectionNotFoundError:
            pass

        # DBA 创建连接
        resp_create = client.post("/api/v1/tdsql/connections", headers=tokens["test_dba"], json={
            "id": dba_conn_id, "name": "DBA专用测试库", "host": "127.0.0.2", "port": 3306, "username": "u", "password": "p"
        })
        assert resp_create.status_code == 200

        # DBA 删除连接
        resp_delete = client.delete(f"/api/v1/tdsql/connections/{dba_conn_id}", headers=tokens["test_dba"])
        assert resp_delete.status_code == 200
    finally:
        try:
            registry.delete_saved(dba_conn_id, operator=rbac_v3_env.usernames["test_dba"])
        except ConnectionNotFoundError:
            pass


def test_rbac_08_dba_admin_only_rejection(rbac_v3_env):
    """RBAC-08: DBA 尝试调用 admin-only 端点（instance-type-lock、discover/config）返回 403"""
    client, tokens, conn_id = rbac_v3_env.client, rbac_v3_env.tokens, rbac_v3_env.conn_id
    resp_lock = client.put(f"/api/v1/tdsql/connections/{conn_id}/instance-type-lock", headers=tokens["test_dba"], json={
        "locked": True, "instance_type": "centralized", "reason": "DBA试图锁定"
    })
    assert resp_lock.status_code == 403

    resp_cfg = client.get("/api/v1/tdsql/discover/config", headers=tokens["test_dba"])
    assert resp_cfg.status_code == 403


def test_rbac_09_deprecated_register_gone(rbac_v3_env):
    """RBAC-09: 废弃的 ZK 注册接口返回 410 Gone"""
    client, tokens = rbac_v3_env.client, rbac_v3_env.tokens
    resp = client.post("/api/v1/tdsql/discover/register", headers=tokens["admin"], json={
        "discovery_id": "disc_1", "item_token": "tok_1", "connection_id": "c1"
    })
    assert resp.status_code == 410


def test_data_01_options_allowlist(rbac_v3_env):
    """DATA-01: /connections/options 响应严格遵循 8 字段白名单"""
    client, tokens = rbac_v3_env.client, rbac_v3_env.tokens
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
    client, tokens = rbac_v3_env.client, rbac_v3_env.tokens
    resp = client.get("/api/v1/tdsql/connections/options", headers=tokens["test_dev"])
    text = resp.text.lower()
    for sensitive in ("secretpassword", "monitorsecret", "password_encrypted", "monitor_password_encrypted", "monitor_user"):
        assert sensitive not in text, f"Found sensitive string in options response: {sensitive}"


def test_data_03_connections_dual_password_strip(rbac_v3_env):
    """DATA-03: /connections 全量列表绝不包含 password_encrypted 或 monitor_password_encrypted"""
    client, tokens = rbac_v3_env.client, rbac_v3_env.tokens
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
    client, tokens = rbac_v3_env.client, rbac_v3_env.tokens
    try:
        # 确保唯一临时普通角色无 instances 菜单
        set_role_permissions(rbac_v3_env.custom_role, {"instances": 0})

        # 1. 模拟 DBA 登录并获取全量管理列表与菜单
        resp_dba_menus = client.get("/api/v1/auth/visible-menus", headers=tokens["test_dba"])
        assert resp_dba_menus.status_code == 200
        assert "instances" in resp_dba_menus.json()["menus"]
        resp_dba_conns = client.get("/api/v1/tdsql/connections", headers=tokens["test_dba"])
        assert resp_dba_conns.status_code == 200

        # 2. 模拟同会话登出后，普通角色 developer 登录
        resp_dev_menus = client.get("/api/v1/auth/visible-menus", headers=tokens["test_custom"])
        assert resp_dev_menus.status_code == 200
        assert "instances" not in resp_dev_menus.json()["menus"]

        # 3. developer 绝对无法访问 /connections 全量管理端点
        resp_dev_conns = client.get("/api/v1/tdsql/connections", headers=tokens["test_custom"])
        assert resp_dev_conns.status_code == 403

        # 4. developer 可以正常访问 /connections/options 精简下拉列表
        resp_dev_opts = client.get("/api/v1/tdsql/connections/options", headers=tokens["test_custom"])
        assert resp_dev_opts.status_code == 200
        assert "connections" in resp_dev_opts.json()
    finally:
        set_role_permissions(rbac_v3_env.custom_role, {"instances": 0})


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

    # 契约 5: clearRoleScopedState 中 extractedResult 必须保持对象契约 {}
    assert "extractedResult.value={};" in content

    # 契约 6: index.html 中对 extractedResult 访问具备空值保护
    index_html_path = Path("frontend/index.html")
    assert index_html_path.exists(), "index.html must exist"
    html_content = index_html_path.read_text(encoding="utf-8")
    assert "extractedResult && extractedResult.filename" in html_content
