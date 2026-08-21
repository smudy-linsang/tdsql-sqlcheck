"""RBAC 实例权限真实浏览器 E2E。

默认全量 pytest 不启动浏览器；CI 或本地显式设置 RUN_BROWSER_E2E=1 后执行。
套件使用唯一临时用户、角色和实例，不登录或修改共享 admin。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import pytest


RUN_BROWSER_E2E = os.getenv("RUN_BROWSER_E2E") == "1"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not RUN_BROWSER_E2E,
        reason="设置 RUN_BROWSER_E2E=1 后执行真实浏览器 E2E",
    ),
]

if RUN_BROWSER_E2E:
    from playwright.sync_api import Browser, Error as PlaywrightError, Page, expect, sync_playwright


PASSWORD = "Test@2026Admin"
BUSINESS_MENUS = {
    "dashboard": 1,
    "schema-extractor-audit": 1,
    "slow-tasks": 1,
    "schema-check": 1,
    "bigtable": 1,
}


@dataclass(frozen=True)
class BrowserRbacData:
    run_id: str
    operator: str
    usernames: dict[str, str]
    roles: dict[str, str]
    marker_id: str
    marker_name: str
    sensitive_username: str
    ui_connection_name: str
    ui_connection_updated_name: str
    shared_admin_before: dict | None
    shared_admin_token: str | None


def _fetch_user_row(username: str) -> dict | None:
    from backend.services.database import _get_connection

    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@pytest.fixture(scope="module")
def browser_rbac_data():
    from backend.services.auth_service import (
        auth_service,
        create_custom_role,
        delete_role,
        issue_token,
        set_role_permissions,
    )
    from backend.services.connection_registry import ConnectionNotFoundError, registry
    from backend.services.database import _get_connection, ensure_db

    previous_auth_enabled = os.environ.get("AUTH_ENABLED")
    os.environ["AUTH_ENABLED"] = "true"
    ensure_db()

    run_id = uuid4().hex[:10]
    operator = f"e2e_{run_id}"
    roles = {
        "view": f"e2e_{run_id}_view",
        "nomenu": f"e2e_{run_id}_nomenu",
    }
    usernames = {
        "admin": f"e2e_{run_id}_admin",
        "dba": f"e2e_{run_id}_dba",
        "view": f"e2e_{run_id}_viewer",
        "nomenu": f"e2e_{run_id}_nomenu",
    }
    marker_id = f"e2e_{run_id}_marker"
    marker_name = f"E2E-RBAC-MARKER-{run_id}"
    sensitive_username = f"e2e_sensitive_{run_id}"
    ui_connection_name = f"E2E-DBA-CREATED-{run_id}"
    ui_connection_updated_name = f"E2E-DBA-UPDATED-{run_id}"
    created_roles: list[str] = []
    created_users: list[str] = []

    shared_admin_before = _fetch_user_row("admin")
    shared_admin_token = None
    if shared_admin_before:
        shared_admin_token = issue_token(
            "admin",
            shared_admin_before["role"],
            shared_admin_before.get("token_version", 0),
        )

    data = BrowserRbacData(
        run_id=run_id,
        operator=operator,
        usernames=usernames,
        roles=roles,
        marker_id=marker_id,
        marker_name=marker_name,
        sensitive_username=sensitive_username,
        ui_connection_name=ui_connection_name,
        ui_connection_updated_name=ui_connection_updated_name,
        shared_admin_before=shared_admin_before,
        shared_admin_token=shared_admin_token,
    )

    try:
        for alias, role_id in roles.items():
            result = create_custom_role(role_id, f"E2E临时角色-{alias}-{run_id}")
            assert "error" not in result, result.get("error")
            created_roles.append(role_id)

        set_role_permissions(roles["view"], {**BUSINESS_MENUS, "instances": 1})
        set_role_permissions(roles["nomenu"], {**BUSINESS_MENUS, "instances": 0})

        user_roles = {
            "admin": "admin",
            "dba": "dba",
            "view": roles["view"],
            "nomenu": roles["nomenu"],
        }
        for alias, role in user_roles.items():
            username = usernames[alias]
            _, error = auth_service.create_user(
                username,
                PASSWORD,
                role,
                display_name=f"E2E-{alias}-{run_id}",
                operator=operator,
            )
            assert error is None, f"创建 E2E 用户 {username} 失败: {error}"
            created_users.append(username)

        conn = _get_connection()
        try:
            placeholders = ",".join("?" for _ in created_users)
            conn.execute(
                f"UPDATE users SET must_change_password = 0 WHERE username IN ({placeholders})",
                tuple(created_users),
            )
            conn.commit()
        finally:
            conn.close()

        registry.save_connection(
            conn_id=marker_id,
            name=marker_name,
            host="192.0.2.123",
            port=3306,
            username=sensitive_username,
            password=PASSWORD,
            database=f"e2e_{run_id}",
            is_default=False,
            is_distributed=True,
            description="RBAC浏览器E2E标记实例",
            operator=operator,
        )

        assert _fetch_user_row("admin") == shared_admin_before
        yield data
    finally:
        cleanup_errors: list[str] = []

        def capture(label, action):
            try:
                action()
            except Exception as exc:
                cleanup_errors.append(f"{label}: {exc}")

        def cleanup_connections():
            conn = _get_connection()
            try:
                rows = conn.execute(
                    "SELECT id FROM tdsql_connections "
                    "WHERE id = ? OR name IN (?, ?)",
                    (marker_id, ui_connection_name, ui_connection_updated_name),
                ).fetchall()
            finally:
                conn.close()
            for row in rows:
                try:
                    registry.delete_saved(row["id"], operator=operator)
                except ConnectionNotFoundError:
                    continue

        capture("删除E2E临时实例", cleanup_connections)

        def delete_test_user(username: str):
            error = auth_service.delete_user(username, operator=operator)
            if error is None or error == "用户不存在":
                return
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
            capture(f"删除E2E用户 {username}", lambda value=username: delete_test_user(value))

        for role_id in reversed(created_roles):
            def remove_role(value=role_id):
                result = delete_role(value)
                if "error" in result and result["error"] != "角色不存在":
                    raise AssertionError(result["error"])
            capture(f"删除E2E角色 {role_id}", remove_role)

        def verify_zero_residue():
            conn = _get_connection()
            try:
                identifiers = [operator, marker_id, *roles.values(), *usernames.values()]
                placeholders = ",".join("?" for _ in identifiers)
                conn.execute(
                    f"DELETE FROM operation_logs WHERE operator IN ({placeholders}) "
                    f"OR target_id IN ({placeholders})",
                    tuple(identifiers + identifiers),
                )
                conn.commit()
                user_count = conn.execute(
                    f"SELECT COUNT(*) AS c FROM users WHERE username IN "
                    f"({','.join('?' for _ in usernames.values())})",
                    tuple(usernames.values()),
                ).fetchone()["c"]
                role_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM roles WHERE role_id IN (?, ?)",
                    tuple(roles.values()),
                ).fetchone()["c"]
                connection_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM tdsql_connections "
                    "WHERE id = ? OR name IN (?, ?)",
                    (marker_id, ui_connection_name, ui_connection_updated_name),
                ).fetchone()["c"]
                log_count = conn.execute(
                    f"SELECT COUNT(*) AS c FROM operation_logs WHERE operator IN ({placeholders}) "
                    f"OR target_id IN ({placeholders})",
                    tuple(identifiers + identifiers),
                ).fetchone()["c"]
                if user_count or role_count or connection_count or log_count:
                    raise AssertionError(
                        "E2E临时资源残留: "
                        f"users={user_count}, roles={role_count}, "
                        f"connections={connection_count}, logs={log_count}"
                    )
            finally:
                conn.close()

        capture("验证E2E资源零残留", verify_zero_residue)

        if _fetch_user_row("admin") != shared_admin_before:
            cleanup_errors.append("E2E 改写了共享 admin 整行状态")
        if shared_admin_token:
            from starlette.testclient import TestClient
            from backend.main import app

            response = TestClient(app).get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {shared_admin_token}"},
            )
            if response.status_code != 200:
                cleanup_errors.append(
                    f"E2E 使共享 admin 旧令牌失效: HTTP {response.status_code}"
                )

        if previous_auth_enabled is None:
            os.environ.pop("AUTH_ENABLED", None)
        else:
            os.environ["AUTH_ENABLED"] = previous_auth_enabled

        if cleanup_errors:
            raise AssertionError("浏览器 E2E teardown 失败:\n" + "\n".join(cleanup_errors))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def live_server(browser_rbac_data, tmp_path_factory):
    repo_root = Path(__file__).resolve().parents[2]
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = tmp_path_factory.mktemp("rbac-browser-e2e") / "uvicorn.log"
    env = os.environ.copy()
    env.update({
        "AUTH_ENABLED": "true",
        "SCHEDULER_ENABLED": "false",
        "PYTHONUNBUFFERED": "1",
    })

    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=repo_root,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log_file.flush()
                raise AssertionError(f"Uvicorn 启动失败:\n{log_path.read_text(encoding='utf-8')}")
            try:
                with urlopen(f"{base_url}/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except (URLError, TimeoutError):
                time.sleep(0.2)
        else:
            process.terminate()
            log_file.flush()
            raise AssertionError(f"Uvicorn 30 秒内未就绪:\n{log_path.read_text(encoding='utf-8')}")

        try:
            yield base_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture(scope="module")
def chromium_browser():
    with sync_playwright() as playwright:
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        if executable:
            browser = playwright.chromium.launch(headless=True, executable_path=executable)
        else:
            try:
                browser = playwright.chromium.launch(headless=True)
            except PlaywrightError:
                # 本地开发机可复用系统 Chrome；CI 始终安装 Playwright 固定版本 Chromium。
                system_chrome = next(
                    (
                        path
                        for path in (
                            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
                        )
                        if path.exists()
                    ),
                    None,
                )
                if system_chrome is None:
                    raise
                browser = playwright.chromium.launch(
                    headless=True,
                    executable_path=str(system_chrome),
                )
        try:
            yield browser
        finally:
            browser.close()


def _page(browser: Browser) -> tuple[Page, list[str]]:
    context = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = context.new_page()
    errors: list[str] = []
    page.on(
        "console",
        lambda message: errors.append(message.text)
        # Chromium 会把 RBAC 预期拒绝的 fetch 也记为资源错误；接口状态由页面断言单独覆盖。
        if message.type == "error" and not message.text.startswith("Failed to load resource:")
        else None,
    )
    page.on("pageerror", lambda error: errors.append(str(error)))
    return page, errors


def _login(page: Page, base_url: str, username: str, errors: list[str] | None = None):
    page.goto(base_url, wait_until="networkidle")
    _input(page, "login-username").fill(username)
    _input(page, "login-password").fill(PASSWORD)
    page.locator('[data-testid="login-submit"]').click()
    expect(page.locator(".app-layout")).to_be_visible(timeout=20_000)
    page.wait_for_load_state("networkidle")
    # 登录页会探测受保护的 logo，并由浏览器请求 favicon；不计入登录后业务页控制台。
    if errors is not None:
        errors.clear()


def _logout(page: Page):
    page.locator(".user-menu").click()
    page.get_by_text("退出登录", exact=True).click()
    expect(page.locator(".login-page")).to_be_visible(timeout=10_000)


def _input(page: Page, testid: str):
    """Element Plus 版本不同会把透传属性放在 input 本身或包装层。"""
    return page.locator(
        f'input[data-testid="{testid}"], [data-testid="{testid}"] input'
    )


def _open_menu(page: Page, submenu_testid: str, menu_testid: str):
    submenu = page.locator(f'[data-testid="{submenu_testid}"]')
    item = page.locator(f'[data-testid="{menu_testid}"]')
    if not item.is_visible():
        submenu.click()
    expect(item).to_be_visible()
    item.click()


def _select_connection(page: Page, select_testid: str, marker_name: str):
    select = page.locator(f'[data-testid="{select_testid}"]')
    expect(select).to_be_visible()
    select.click()
    option = page.locator(".el-select-dropdown:visible .el-select-dropdown__item").filter(
        has_text=marker_name
    ).first
    expect(option).to_be_visible(timeout=10_000)
    option.click()
    expect(select).to_contain_text(marker_name)


def _open_instances(page: Page):
    _open_menu(page, "submenu-platform", "menu-instances")
    expect(page.get_by_text("实例管理", exact=True).last).to_be_visible()


def _marker_row(page: Page, marker_name: str):
    row = page.locator(".el-table__body tr").filter(has_text=marker_name).first
    expect(row).to_be_visible(timeout=10_000)
    return row


def _assert_clean_console(errors: list[str]):
    assert errors == [], "浏览器控制台出现错误:\n" + "\n".join(errors)


def test_admin_and_dba_real_click_management_matrix(
    chromium_browser, live_server, browser_rbac_data
):
    data = browser_rbac_data

    admin_page, admin_errors = _page(chromium_browser)
    try:
        _login(admin_page, live_server, data.usernames["admin"], admin_errors)
        _open_instances(admin_page)
        admin_row = _marker_row(admin_page, data.marker_name)
        expect(admin_page.locator('[data-testid="instance-create"]')).to_be_visible()
        expect(admin_page.locator('[data-testid="instance-zk-config"]')).to_be_visible()
        expect(admin_row.get_by_role("button", name="编辑", exact=True)).to_be_visible()
        expect(admin_row.get_by_role("button", name="删除", exact=True)).to_be_visible()
        expect(admin_row.get_by_role("button", name="锁定类型", exact=True)).to_be_visible()

        admin_page.locator('[data-testid="instance-create"]').click()
        expect(admin_page.get_by_text("新建连接", exact=True)).to_be_visible()
        admin_page.locator(".el-drawer__close-btn:visible").click()
        expect(admin_page.locator(".el-drawer:visible")).to_have_count(0)

        admin_row.get_by_role("button", name="编辑", exact=True).click()
        expect(admin_page.get_by_text("编辑连接", exact=True)).to_be_visible()
        admin_page.locator(".el-drawer__close-btn:visible").click()
        expect(admin_page.locator(".el-drawer:visible")).to_have_count(0)

        admin_row = _marker_row(admin_page, data.marker_name)
        admin_row.get_by_role("button", name="删除", exact=True).click()
        expect(admin_page.get_by_text("删除确认", exact=True)).to_be_visible()
        admin_page.get_by_role("button", name="取消", exact=True).click()
        _marker_row(admin_page, data.marker_name)
        _assert_clean_console(admin_errors)
    finally:
        admin_page.context.close()

    dba_page, dba_errors = _page(chromium_browser)
    try:
        _login(dba_page, live_server, data.usernames["dba"], dba_errors)
        _open_instances(dba_page)
        dba_row = _marker_row(dba_page, data.marker_name)
        expect(dba_page.locator('[data-testid="instance-create"]')).to_be_visible()
        expect(dba_page.locator('[data-testid="instance-zk-config"]')).to_have_count(0)
        expect(dba_row.get_by_role("button", name="锁定类型", exact=True)).to_have_count(0)

        # DBA 经真实页面完成新增、编辑、删除闭环。
        dba_page.locator('[data-testid="instance-create"]').click()
        _input(dba_page, "connection-name").fill(data.ui_connection_name)
        _input(dba_page, "connection-host").fill("192.0.2.124")
        _input(dba_page, "connection-username").fill("e2e_dba_user")
        _input(dba_page, "connection-password").fill("e2e_dba_password")
        _input(dba_page, "connection-database").fill(f"e2e_ui_{data.run_id}")
        dba_page.locator('[data-testid="connection-save"]').click()
        created_row = _marker_row(dba_page, data.ui_connection_name)

        created_row.get_by_role("button", name="编辑", exact=True).click()
        name_input = _input(dba_page, "connection-name")
        name_input.fill(data.ui_connection_updated_name)
        dba_page.locator('[data-testid="connection-save"]').click()
        updated_row = _marker_row(dba_page, data.ui_connection_updated_name)

        updated_row.get_by_role("button", name="删除", exact=True).click()
        dba_page.get_by_role("button", name="确定", exact=True).click()
        expect(
            dba_page.locator(".el-table__body tr").filter(has_text=data.ui_connection_updated_name)
        ).to_have_count(0, timeout=10_000)
        _assert_clean_console(dba_errors)
    finally:
        dba_page.context.close()


def test_readonly_role_can_only_view_instances(chromium_browser, live_server, browser_rbac_data):
    data = browser_rbac_data
    page, errors = _page(chromium_browser)
    try:
        _login(page, live_server, data.usernames["view"], errors)
        _open_instances(page)
        row = _marker_row(page, data.marker_name)
        expect(row).to_contain_text(data.sensitive_username)
        expect(page.locator('[data-testid="instance-create"]')).to_have_count(0)
        expect(page.locator('[data-testid="instance-zk-config"]')).to_have_count(0)
        expect(page.locator(".el-table__header-wrapper th").filter(has_text="操作")).to_have_count(0)
        expect(page.get_by_role("button", name="编辑", exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="删除", exact=True)).to_have_count(0)
        _assert_clean_console(errors)
    finally:
        page.context.close()


def test_no_instances_menu_can_select_instance_in_four_modules(
    chromium_browser, live_server, browser_rbac_data
):
    data = browser_rbac_data
    page, errors = _page(chromium_browser)
    try:
        _login(page, live_server, data.usernames["nomenu"], errors)
        expect(page.locator('[data-testid="menu-instances"]')).to_have_count(0)
        expect(page.get_by_text(data.marker_name, exact=False)).to_have_count(0)
        expect(page.get_by_text(data.sensitive_username, exact=False)).to_have_count(0)

        _open_menu(page, "submenu-audit", "menu-online-metadata")
        _select_connection(page, "metadata-instance-select", data.marker_name)
        expect(page.locator('[data-testid="metadata-run"]')).to_be_enabled()

        _open_menu(page, "submenu-slow", "menu-scan-tasks")
        _select_connection(page, "scan-instance-select", data.marker_name)
        expect(page.locator('[data-testid="scan-query"]')).to_be_enabled()

        _open_menu(page, "submenu-instance", "menu-schema-check")
        _select_connection(page, "schema-check-instance-select", data.marker_name)
        expect(page.locator('[data-testid="schema-check-run"]')).to_be_enabled()

        _open_menu(page, "submenu-instance", "menu-bigtable")
        _select_connection(page, "bigtable-instance-select", data.marker_name)
        expect(page.locator('[data-testid="bigtable-refresh"]')).to_be_enabled()

        expect(page.locator('[data-testid="menu-instances"]')).to_have_count(0)
        expect(page.get_by_text(data.sensitive_username, exact=False)).to_have_count(0)
        _assert_clean_console(errors)
    finally:
        page.context.close()


def test_slow_network_role_switch_never_leaks_managed_instances(
    chromium_browser, live_server, browser_rbac_data
):
    data = browser_rbac_data
    page, errors = _page(chromium_browser)
    requested_paths: list[str] = []
    page.on("request", lambda request: requested_paths.append(request.url.split("?", 1)[0]))

    try:
        _login(page, live_server, data.usernames["dba"], errors)
        _open_instances(page)
        _marker_row(page, data.marker_name)
        expect(page.get_by_text(data.sensitive_username, exact=False)).to_be_visible()
        _logout(page)

        cdp = page.context.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send(
            "Network.emulateNetworkConditions",
            {
                "offline": False,
                "latency": 800,
                "downloadThroughput": 10 * 1024 * 1024,
                "uploadThroughput": 10 * 1024 * 1024,
                "connectionType": "wifi",
            },
        )
        requested_paths.clear()

        _input(page, "login-username").fill(data.usernames["nomenu"])
        _input(page, "login-password").fill(PASSWORD)
        page.locator('[data-testid="login-submit"]').click()

        for wait_ms in (100, 850, 900):
            page.wait_for_timeout(wait_ms)
            expect(page.locator('[data-testid="menu-instances"]')).to_have_count(0)
            expect(page.get_by_text(data.marker_name, exact=False)).to_have_count(0)
            expect(page.get_by_text(data.sensitive_username, exact=False)).to_have_count(0)

        expect(page.locator(".app-layout")).to_be_visible(timeout=20_000)
        _open_menu(page, "submenu-audit", "menu-online-metadata")
        _select_connection(page, "metadata-instance-select", data.marker_name)
        page.wait_for_timeout(1200)

        full_connections_url = f"{live_server}/api/v1/tdsql/connections"
        options_url = f"{live_server}/api/v1/tdsql/connections/options"
        assert options_url in requested_paths
        assert full_connections_url not in requested_paths
        expect(page.locator('[data-testid="menu-instances"]')).to_have_count(0)
        expect(page.get_by_text(data.sensitive_username, exact=False)).to_have_count(0)
        _assert_clean_console(errors)
    finally:
        try:
            cdp.send(
                "Network.emulateNetworkConditions",
                {
                    "offline": False,
                    "latency": 0,
                    "downloadThroughput": -1,
                    "uploadThroughput": -1,
                    "connectionType": "none",
                },
            )
        except UnboundLocalError:
            pass
        page.context.close()
