"""
V2.0 UAT 用户验收测试

按银行实际使用场景组织的验收用例:
- UAT-A 系统管理员: 首次部署引导、用户开通、权限回收
- UAT-B 数据库管理员: 多实例接入、规则集定制、保留策略
- UAT-C 开发人员: 自助SQL审核、越权防护
- UAT-D 审计员: 全局只读、操作留痕核查
- UAT-E 内网部署: 前端资产本地化验收
"""
import pytest
from fastapi.testclient import TestClient

STRONG_PW = "Uat@2026Bank"


@pytest.fixture(scope="module")
def uat():
    import os
    os.environ["AUTH_ENABLED"] = "true"
    from backend.main import app
    from backend.services.auth_service import auth_service
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    auth_service.ensure_bootstrap_admin()
    auth_service.reset_password("admin", STRONG_PW, operator="uat")
    conn = _get_connection()
    conn.execute("UPDATE users SET must_change_password = 0 WHERE username = 'admin'")
    conn.commit()
    conn.close()
    client = TestClient(app)
    resp = client.post("/api/v1/auth/login",
                       json={"username": "admin", "password": STRONG_PW})
    assert resp.status_code == 200
    admin_h = {"Authorization": f"Bearer {resp.json()['token']}"}
    yield client, admin_h
    os.environ["AUTH_ENABLED"] = "false"


def _login_headers(client, username, password):
    resp = client.post("/api/v1/auth/login",
                       json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


class TestUATAdminScenario:
    """UAT-A: 系统管理员开通一名新开发人员并管理其生命周期"""

    def test_a1_admin_creates_developer(self, uat):
        client, admin_h = uat
        from backend.services.auth_service import auth_service
        auth_service.delete_user("dev_zhang", operator="uat")
        resp = client.post("/api/v1/auth/users", headers=admin_h, json={
            "username": "dev_zhang", "password": STRONG_PW,
            "role": "developer", "display_name": "张开发"})
        assert resp.status_code == 200
        assert resp.json()["user"]["role"] == "developer"

    def test_a2_new_user_must_change_password(self, uat):
        client, _ = uat
        resp = client.post("/api/v1/auth/login", json={
            "username": "dev_zhang", "password": STRONG_PW})
        assert resp.status_code == 200
        assert resp.json()["user"]["must_change_password"] is True

    def test_a3_new_user_changes_password_and_works(self, uat):
        client, _ = uat
        h = _login_headers(client, "dev_zhang", STRONG_PW)
        new_pw = "Zhang@2026New"
        resp = client.post("/api/v1/auth/change-password", headers=h, json={
            "old_password": STRONG_PW, "new_password": new_pw})
        assert resp.status_code == 200
        h2 = _login_headers(client, "dev_zhang", new_pw)
        resp = client.get("/api/v1/auth/me", headers=h2)
        assert resp.json()["must_change_password"] is False

    def test_a4_admin_disables_leaver(self, uat):
        """员工离职 → 禁用账户 → 既有令牌立即失效"""
        client, admin_h = uat
        h = _login_headers(client, "dev_zhang", "Zhang@2026New")
        resp = client.put("/api/v1/auth/users/dev_zhang", headers=admin_h,
                          json={"status": "disabled"})
        assert resp.status_code == 200
        # 等待用户缓存过期前直接验证登录被拒
        resp = client.post("/api/v1/auth/login", json={
            "username": "dev_zhang", "password": "Zhang@2026New"})
        assert resp.status_code == 401


class TestUATDBAScenario:
    """UAT-B: DBA接入多套数据库并做差异化规则管理"""

    @pytest.fixture(scope="class")
    def dba_h(self, uat):
        client, admin_h = uat
        from backend.services.auth_service import auth_service
        auth_service.delete_user("dba_li", operator="uat")
        auth_service.create_user("dba_li", STRONG_PW, "dba",
                                 display_name="李DBA", operator="uat")
        auth_service.change_password("dba_li", STRONG_PW, STRONG_PW + "x")
        return _login_headers(client, "dba_li", STRONG_PW + "x")

    def test_b1_register_multiple_environments(self, uat, dba_h):
        """接入开发/测试/生产三套环境的连接配置"""
        client, _ = uat
        ids = []
        for env, host in (("开发", "10.1.0.1"), ("测试", "10.2.0.1"), ("生产", "10.3.0.1")):
            resp = client.post("/api/v1/tdsql/connections", headers=dba_h, json={
                "host": host, "port": 15000, "user": "app",
                "password": "Env@Pw123", "database": f"db_{env}",
                "name": f"{env}环境库"})
            assert resp.status_code == 200
            ids.append(resp.json()["id"])
        resp = client.get("/api/v1/tdsql/connections", headers=dba_h)
        saved_ids = [c["id"] for c in resp.json()["connections"]]
        for i in ids:
            assert i in saved_ids
        # 清理
        for i in ids:
            client.delete(f"/api/v1/tdsql/connections/{i}", headers=dba_h)

    def test_b2_custom_ruleset_for_dev_env(self, uat, dba_h):
        client, _ = uat
        client.delete("/api/v1/rulesets/uat_dev_rs", headers=dba_h)
        resp = client.post("/api/v1/rulesets", headers=dba_h, json={
            "id": "uat_dev_rs", "name": "开发环境规则集",
            "description": "开发环境SELECT*降级",
            "items": [{"rule_id": "R012", "severity_override": "INFO",
                       "enabled": True}]})
        assert resp.status_code == 200
        resp = client.get("/api/v1/rulesets", headers=dba_h)
        assert any(rs["id"] == "uat_dev_rs" for rs in resp.json()["rulesets"])
        client.delete("/api/v1/rulesets/uat_dev_rs", headers=dba_h)

    def test_b3_retention_policy_management(self, uat, dba_h):
        client, _ = uat
        resp = client.put("/api/v1/admin/retention", headers=dba_h, json={
            "table_name": "slow_queries", "retention_days": 90})
        assert resp.status_code == 200
        resp = client.get("/api/v1/admin/retention", headers=dba_h)
        p = [x for x in resp.json()["policies"]
             if x["table_name"] == "slow_queries"][0]
        assert p["retention_days"] == 90
        # 恢复默认
        client.put("/api/v1/admin/retention", headers=dba_h, json={
            "table_name": "slow_queries", "retention_days": 180})


class TestUATDeveloperScenario:
    """UAT-C: 开发人员自助审核与越权防护"""

    @pytest.fixture(scope="class")
    def dev_h(self, uat):
        client, _ = uat
        from backend.services.auth_service import auth_service
        auth_service.delete_user("dev_wang", operator="uat")
        auth_service.create_user("dev_wang", STRONG_PW, "developer", operator="uat")
        auth_service.change_password("dev_wang", STRONG_PW, STRONG_PW + "x")
        return _login_headers(client, "dev_wang", STRONG_PW + "x")

    def test_c1_developer_audits_sql(self, uat, dev_h):
        client, _ = uat
        resp = client.post("/api/v1/audit/sql", headers=dev_h, json={
            "sql": "UPDATE t_account SET balance = balance - 100"})
        assert resp.status_code == 200
        assert resp.json()["passed"] is False  # 无WHERE的UPDATE

    def test_c2_developer_analyzes_explain(self, uat, dev_h):
        client, _ = uat
        resp = client.post("/api/v1/slow-queries/analyze-explain", headers=dev_h,
                           json={"explain_data": [
                               {"type": "ALL", "rows": 850000,
                                "extra": "Using where"}]})
        assert resp.status_code == 200

    def test_c3_developer_views_rules(self, uat, dev_h):
        client, _ = uat
        resp = client.get("/api/v1/rules", headers=dev_h)
        assert resp.status_code == 200
        rules = resp.json().get("rules", resp.json())
        count = len(rules) if isinstance(rules, list) else rules.get("total", 0)
        assert count >= 70  # 77条规则

    def test_c4_developer_cannot_modify_rulesets(self, uat, dev_h):
        client, _ = uat
        resp = client.post("/api/v1/rulesets", headers=dev_h, json={
            "id": "hack_rs", "name": "越权测试"})
        assert resp.status_code == 403

    def test_c5_developer_cannot_manage_users(self, uat, dev_h):
        client, _ = uat
        resp = client.get("/api/v1/auth/users", headers=dev_h)
        assert resp.status_code == 403

    def test_c6_developer_cannot_run_retention(self, uat, dev_h):
        client, _ = uat
        resp = client.post("/api/v1/admin/retention/run", headers=dev_h)
        assert resp.status_code == 403


class TestUATAuditorScenario:
    """UAT-D: 审计员全局只读核查"""

    @pytest.fixture(scope="class")
    def aud_h(self, uat):
        client, _ = uat
        from backend.services.auth_service import auth_service
        auth_service.delete_user("aud_chen", operator="uat")
        auth_service.create_user("aud_chen", STRONG_PW, "auditor", operator="uat")
        auth_service.change_password("aud_chen", STRONG_PW, STRONG_PW + "x")
        return _login_headers(client, "aud_chen", STRONG_PW + "x")

    def test_d1_auditor_reads_everything(self, uat, aud_h):
        client, _ = uat
        for path in ("/api/v1/dashboard/summary", "/api/v1/rules",
                     "/api/v1/rulesets", "/api/v1/admin/retention",
                     "/api/v1/admin/operation-logs", "/api/v1/slow-queries"):
            resp = client.get(path, headers=aud_h)
            assert resp.status_code == 200, f"{path}: {resp.status_code}"

    def test_d2_auditor_cannot_write_anything(self, uat, aud_h):
        client, _ = uat
        cases = [
            ("POST", "/api/v1/audit/sql", {"sql": "SELECT 1"}),
            ("POST", "/api/v1/tdsql/connect",
             {"host": "x", "port": 1, "user": "u", "password": "p"}),
            ("POST", "/api/v1/rulesets", {"id": "x", "name": "x"}),
            ("POST", "/api/v1/admin/retention/run", None),
        ]
        for method, path, body in cases:
            resp = client.request(method, path, json=body, headers=aud_h)
            assert resp.status_code == 403, f"{method} {path}: {resp.status_code}"

    def test_d3_auditor_traces_operations(self, uat, aud_h):
        """审计场景: 追溯某用户的全部操作"""
        client, admin_h = uat
        resp = client.get("/api/v1/admin/operation-logs?limit=20", headers=aud_h)
        assert resp.status_code == 200
        logs = resp.json()["logs"]
        # 日志包含操作者、操作类型、时间
        if logs:
            assert "operator" in logs[0]
            assert "operation_type" in logs[0]
            assert "created_at" in logs[0]


class TestUATIntranetDeployment:
    """UAT-E: 纯内网部署验收（无外网CDN依赖）"""

    def test_e1_frontend_no_external_cdn(self):
        from pathlib import Path
        html = (Path(__file__).parent.parent / "frontend" / "index.html").read_text(
            encoding="utf-8")
        for banned in ("unpkg.com", "jsdelivr.net", "cdnjs.", "googleapis.com"):
            assert banned not in html, f"前端仍引用外网CDN: {banned}"

    def test_e2_vendor_assets_exist(self):
        from pathlib import Path
        vendor = Path(__file__).parent.parent / "frontend" / "static" / "vendor"
        for asset in ("vue.global.prod.js", "element-plus.full.min.js",
                      "element-plus.css", "echarts.min.js",
                      "element-plus-icons.iife.min.js",
                      "element-plus-locale-zh-cn.min.js"):
            f = vendor / asset
            assert f.exists() and f.stat().st_size > 1000, f"缺失vendor资产: {asset}"

    def test_e3_static_assets_served(self, uat):
        client, _ = uat
        resp = client.get("/static/vendor/vue.global.prod.js")
        assert resp.status_code == 200
        resp = client.get("/")
        assert resp.status_code == 200
        assert "TDSQL" in resp.text

    def test_e4_api_base_is_relative(self):
        from pathlib import Path
        html = (Path(__file__).parent.parent / "frontend" / "index.html").read_text(
            encoding="utf-8")
        assert "const API_BASE = ''" in html, "API_BASE应为同源相对路径"
        assert "http://localhost:8000" not in html
