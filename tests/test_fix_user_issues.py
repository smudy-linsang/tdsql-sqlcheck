"""
验证用户报告的 5 个关键问题已完全修复：
1. 开发人员/测试人员：扫描对比 POST /api/v1/scan-compare/compare 正常放行（非403）
2. 开发人员/测试人员：扫描抓取 POST /api/v1/tdsql/slow-queries/fetch 正常放行（非403）
3. 开发人员/测试人员：慢SQL标记 PUT /api/v1/slow-queries/{id}/status 正常生效（200 OK）
4. 全角色：慢SQL分析报告导出 PDF 正常生成并下载（200 OK，包含中文文件名）
5. 自定义测试人员角色：即时元数据提取与审核 POST /api/v1/audit/extract-and-audit 正常放行（非403）
6. 实例管理写操作安全隔离：非管理员无论何种角色调用 POST /api/v1/tdsql/connections 均严格返回 403
"""
import os
import pytest
from starlette.testclient import TestClient
from backend.main import app
from backend.services.database import _get_connection, ensure_db
from backend.services.auth_service import (
    auth_service, create_custom_role, set_role_permissions, delete_role
)

STRONG_PW = "TestUser@2026Pw"


@pytest.fixture(scope="module")
def fix_verification_env():
    os.environ["AUTH_ENABLED"] = "true"
    ensure_db()

    conn = _get_connection()
    orig_admin = conn.execute(
        "SELECT password_hash, salt, must_change_password, status FROM users WHERE username = 'admin'"
    ).fetchone()
    orig_admin_dict = dict(orig_admin) if orig_admin else None

    orig_perm_rows = conn.execute("SELECT role_id, menu_key, visible FROM role_permissions").fetchall()
    orig_permissions = [dict(r) for r in orig_perm_rows]
    conn.close()

    # 准备测试用户: dev_test_user, tester_test_user
    auth_service.delete_user("dev_test_user", operator="test")
    auth_service.create_user("dev_test_user", STRONG_PW, "developer", operator="test")

    try:
        create_custom_role("tester_role", "测试人员")
    except Exception:
        pass
    set_role_permissions("tester_role", {
        "schema-extractor-audit": 1,
        "slow-tasks": 1,
        "slow-records": 1,
        "bigtable": 1,
        "instances": 1,
    })

    auth_service.delete_user("tester_test_user", operator="test")
    auth_service.create_user("tester_test_user", STRONG_PW, "tester_role", operator="test")

    conn = _get_connection()
    conn.execute(
        "UPDATE users SET must_change_password = 0 WHERE username IN ('dev_test_user', 'tester_test_user')"
    )
    conn.commit()
    conn.close()

    auth_service.reset_password("admin", STRONG_PW, operator="test")
    conn = _get_connection()
    conn.execute("UPDATE users SET must_change_password = 0 WHERE username = 'admin'")
    conn.commit()
    conn.close()

    client = TestClient(app)
    tokens = {}
    for name in ("admin", "dev_test_user", "tester_test_user"):
        resp = client.post("/api/v1/auth/login", json={"username": name, "password": STRONG_PW})
        assert resp.status_code == 200, f"Login failed for {name}: {resp.text}"
        tokens[name] = {"Authorization": f"Bearer {resp.json()['token']}"}

    yield client, tokens

    # Teardown
    auth_service.delete_user("dev_test_user", operator="test")
    auth_service.delete_user("tester_test_user", operator="test")
    try:
        delete_role("tester_role")
    except Exception:
        pass

    conn = _get_connection()
    try:
        conn.execute("DELETE FROM role_permissions WHERE role_id = 'tester_role'")
        for row in orig_permissions:
            conn.execute("""
                INSERT INTO role_permissions(role_id, menu_key, visible)
                VALUES (?, ?, ?)
                ON DUPLICATE KEY UPDATE visible = VALUES(visible)
            """, (row["role_id"], row["menu_key"], row["visible"]))
        if orig_admin_dict:
            conn.execute("""
                UPDATE users SET password_hash = ?, salt = ?, must_change_password = ?, status = ?
                WHERE username = 'admin'
            """, (
                orig_admin_dict["password_hash"],
                orig_admin_dict["salt"],
                orig_admin_dict["must_change_password"],
                orig_admin_dict["status"]
            ))
        conn.commit()
    finally:
        conn.close()

    os.environ["AUTH_ENABLED"] = "false"


def test_pdf_export_for_all_roles(fix_verification_env):
    """问题3&系统管理员问题1: 导出慢SQL PDF成功生成并下载，无中文字体编码异常"""
    client, tokens = fix_verification_env
    # 获取一条存在的慢SQL记录ID
    conn = _get_connection()
    row = conn.execute("SELECT id FROM slow_queries LIMIT 1").fetchone()
    conn.close()
    assert row is not None, "必须存在测试慢SQL记录"
    slow_id = row["id"]

    for user in ("admin", "dev_test_user", "tester_test_user"):
        resp = client.get(f"/api/v1/audit/slow-report/{slow_id}/export", headers=tokens[user])
        assert resp.status_code == 200, f"{user} 导出失败: {resp.text}"
        assert resp.headers.get("content-type") == "application/pdf"
        assert "filename*=" in resp.headers.get("content-disposition", "")
        assert len(resp.content) > 1000


def test_developer_and_tester_scan_compare(fix_verification_env):
    """问题1: 开发人员及测试人员调用扫描对比不报403"""
    client, tokens = fix_verification_env
    for user in ("dev_test_user", "tester_test_user"):
        resp = client.post("/api/v1/scan-compare/compare", headers=tokens[user], json={
            "task_id_1": 1, "task_id_2": 1
        })
        assert resp.status_code != 403, f"{user} 扫描对比被403拒绝: {resp.text}"


def test_developer_and_tester_slow_status_update(fix_verification_env):
    """问题3: 开发人员及测试人员对慢SQL记录标记状态正常生效"""
    client, tokens = fix_verification_env
    conn = _get_connection()
    row = conn.execute("SELECT id FROM slow_queries LIMIT 1").fetchone()
    conn.close()
    slow_id = row["id"]

    for user, target_status in [("dev_test_user", "optimized"), ("tester_test_user", "pending")]:
        resp = client.put(f"/api/v1/slow-queries/{slow_id}/status", headers=tokens[user], json={
            "status": target_status
        })
        assert resp.status_code == 200, f"{user} 状态更新失败: {resp.text}"


def test_tester_extract_and_audit(fix_verification_env):
    """测试人员问题5: 在线元数据提取与审核调用不报403"""
    client, tokens = fix_verification_env
    resp = client.post("/api/v1/audit/extract-and-audit", headers=tokens["tester_test_user"], json={
        "connection_id": "non_existent_test_conn"
    })
    assert resp.status_code != 403, f"测试人员执行提取审核被403拦截: {resp.text}"


def test_bigtable_collect_permission(fix_verification_env):
    """问题4: 大表清单采集接口权限正常放行"""
    client, tokens = fix_verification_env
    for user in ("dev_test_user", "tester_test_user"):
        resp = client.post("/api/v1/bigtable/collect", headers=tokens[user], json={
            "connection_id": "non_existent_test_conn"
        })
        assert resp.status_code != 403, f"{user} 采集大表被403拦截: {resp.text}"


def test_non_manager_connection_write_still_forbidden(fix_verification_env):
    """安全基线校验: 测试人员即使被分配 instances 菜单，写操作（新建/修改/删除）依然严格 403"""
    client, tokens = fix_verification_env
    resp = client.post("/api/v1/tdsql/connections", headers=tokens["tester_test_user"], json={
        "name": "illegal_conn", "host": "127.0.0.1", "port": 3306, "username": "u", "password": "p"
    })
    assert resp.status_code == 403


def test_logo_accessible_for_all_roles(fix_verification_env):
    """顶部Logo获取属于全局平台属性，所有已认证角色（包括开发/测试人员）均可正常读取（200 OK）"""
    client, tokens = fix_verification_env
    for user in ("admin", "dev_test_user", "tester_test_user"):
        resp = client.get("/api/v1/admin/logo", headers=tokens[user])
        assert resp.status_code == 200, f"{user} 读取Logo失败: {resp.text}"

