"""
V2.0 SIT 系统集成测试

在认证开启模式下验证跨模块集成链路:
- 登录 → 审核 → 历史 → 门禁 → 报表 的完整数据流
- 规则集 → 项目 → 审核 → 门禁 联动
- 连接配置 → 加密存储 → 列表脱敏 → 扫描计划 联动
- 中间件链（认证→RBAC→审计→指标）与业务API的集成
"""
import pytest
from fastapi.testclient import TestClient

STRONG_PW = "Sit@2026Test"


@pytest.fixture(scope="module")
def sit():
    """SIT环境: 认证开启 + 预置角色账户，模块级共享"""
    import os
    os.environ["AUTH_ENABLED"] = "true"
    from backend.main import app
    from backend.services.auth_service import auth_service
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    auth_service.ensure_bootstrap_admin()
    auth_service.reset_password("admin", STRONG_PW, operator="sit")
    conn = _get_connection()
    conn.execute("UPDATE users SET must_change_password = 0 WHERE username = 'admin'")
    conn.commit()
    conn.close()
    for name, role in (("sit_dba", "dba"), ("sit_dev", "developer"),
                       ("sit_aud", "auditor")):
        auth_service.delete_user(name, operator="sit")
        auth_service.create_user(name, STRONG_PW, role, operator="sit")
    client = TestClient(app)

    tokens = {}
    for name in ("admin", "sit_dba", "sit_dev", "sit_aud"):
        resp = client.post("/api/v1/auth/login",
                           json={"username": name, "password": STRONG_PW})
        assert resp.status_code == 200, f"{name}登录失败: {resp.text}"
        tokens[name] = {"Authorization": f"Bearer {resp.json()['token']}"}

    yield client, tokens
    os.environ["AUTH_ENABLED"] = "false"


class TestSITAuditFlow:
    """SIT-1: 审核全链路（开发提交 → 审核 → 历史落库带用户）"""

    def test_audit_and_history_records_user(self, sit):
        client, tokens = sit
        resp = client.post("/api/v1/audit/sql",
                           json={"sql": "DELETE FROM t_order"},
                           headers=tokens["sit_dev"])
        assert resp.status_code == 200
        assert resp.json()["passed"] is False  # R014 无WHERE

        # 审核历史记录了操作用户（按用户过滤，避免与其他用例的写入交错）
        from backend.services.database import _get_connection
        conn = _get_connection()
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_history WHERE created_by = 'sit_dev'"
        ).fetchone()
        conn.close()
        assert row["c"] >= 1, "审核历史应记录操作用户身份"

    def test_file_audit_mybatis_flow(self, sit):
        client, tokens = sit
        xml = """<mapper>
          <select id="q1">SELECT * FROM t_user WHERE name = #{name}</select>
          <update id="u1">UPDATE t_order SET status = 1 WHERE order_id = #{id}</update>
        </mapper>"""
        resp = client.post("/api/v1/audit/file",
                           json={"content": xml, "file_path": "UserMapper.xml"},
                           headers=tokens["sit_dev"])
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_sql"] == 2
        assert data["summary"]["failed"] >= 1  # SELECT *

    def test_dashboard_reflects_audits(self, sit):
        client, tokens = sit
        resp = client.get("/api/v1/dashboard/summary", headers=tokens["sit_aud"])
        assert resp.status_code == 200


class TestSITGateFlow:
    """SIT-2: 规则集 → 项目 → 审核 → 门禁 联动链路"""

    def test_project_gate_blocks_error(self, sit):
        client, tokens = sit
        from backend.services.database import _get_connection, ensure_db
        ensure_db()
        conn = _get_connection()
        conn.execute("DELETE FROM projects WHERE project_id = 'sit_proj'")
        conn.execute(
            "INSERT INTO projects(project_id, project_name, rule_set_id, gate_rule_id) "
            "VALUES ('sit_proj', 'SIT项目', 'default', 'sit_proj')")
        conn.commit()
        conn.close()
        # DBA配置严格门禁
        resp = client.post("/api/v1/gate/strategy/sit_proj?strategy=normal",
                           headers=tokens["sit_dba"])
        assert resp.status_code == 200, resp.text

        # 含ERROR违规的SQL → 门禁不通过
        resp = client.post("/api/v1/audit/sql",
                           json={"sql": "SELECT * FROM t_user ORDER BY RAND()",
                                 "project_id": "sit_proj"},
                           headers=tokens["sit_dev"])
        assert resp.status_code == 200
        data = resp.json()
        assert data["gate_result"] is not None
        assert data["gate_result"]["passed"] is False

        # 合规SQL → 门禁通过
        resp = client.post("/api/v1/audit/sql",
                           json={"sql": "SELECT id, name FROM t_user WHERE id = 1",
                                 "project_id": "sit_proj"},
                           headers=tokens["sit_dev"])
        data = resp.json()
        assert data["gate_result"]["passed"] is True

    def test_ruleset_project_gate_integration(self, sit):
        """自定义规则集降级R012后，门禁放行SELECT*"""
        client, tokens = sit
        # 幂等: 先解除历史运行的项目引用，规则集才可删除重建
        from backend.services.database import _get_connection
        conn = _get_connection()
        conn.execute("DELETE FROM projects WHERE project_id = 'sit_proj2'")
        conn.commit()
        conn.close()
        # DBA创建规则集: R012禁用
        client.delete("/api/v1/rulesets/sit_loose", headers=tokens["sit_dba"])
        resp = client.post("/api/v1/rulesets", headers=tokens["sit_dba"], json={
            "id": "sit_loose", "name": "SIT宽松集",
            "items": [{"rule_id": "R012", "enabled": False},
                      {"rule_id": "R051", "enabled": False}]})
        assert resp.status_code == 200, resp.text
        conn = _get_connection()
        conn.execute(
            "INSERT INTO projects(project_id, project_name, rule_set_id) "
            "VALUES ('sit_proj2', 'SIT项目2', 'sit_loose')")
        conn.commit()
        conn.close()

        resp = client.post("/api/v1/audit/sql",
                           json={"sql": "SELECT * FROM t_user WHERE id = 1",
                                 "project_id": "sit_proj2"},
                           headers=tokens["sit_dev"])
        rule_ids = [v["rule_id"] for v in resp.json()["violations"]]
        assert "R012" not in rule_ids


class TestSITConnectionFlow:
    """SIT-3: 连接配置 → 加密存储 → 扫描计划 联动"""

    def test_dba_saves_connection_and_schedule(self, sit):
        client, tokens = sit
        # DBA保存连接配置
        resp = client.post("/api/v1/tdsql/connections", headers=tokens["sit_dba"],
                           json={"host": "192.168.1.100", "port": 15000,
                                 "username": "scan_user", "password": "Scan@Pw123",
                                 "database": "biz_db", "name": "SIT业务库"})
        assert resp.status_code == 200
        conn_id = resp.json()["id"]

        # 列表可见且脱敏
        resp = client.get("/api/v1/tdsql/connections", headers=tokens["sit_dev"])
        found = [c for c in resp.json()["connections"] if c["id"] == conn_id]
        assert found and found[0]["password"] == "***"

        # DBA为该连接创建扫描计划
        resp = client.post("/api/v1/tdsql/scan-schedules", headers=tokens["sit_dba"],
                           json={"connection_id": conn_id, "source": "digest",
                                 "cron_hour": 2, "cron_minute": 30})
        assert resp.status_code == 200
        sched_id = resp.json()["id"]

        resp = client.get("/api/v1/tdsql/scan-schedules", headers=tokens["sit_aud"])
        scheds = [s for s in resp.json()["schedules"] if s["id"] == sched_id]
        assert scheds and scheds[0]["connection_id"] == conn_id

        # 清理
        client.delete(f"/api/v1/tdsql/scan-schedules/{sched_id}",
                      headers=tokens["sit_dba"])
        client.delete(f"/api/v1/tdsql/connections/{conn_id}",
                      headers=tokens["sit_dba"])

    def test_schedule_for_unknown_connection_rejected(self, sit):
        client, tokens = sit
        resp = client.post("/api/v1/tdsql/scan-schedules", headers=tokens["sit_dba"],
                           json={"connection_id": "ghost_conn", "source": "digest",
                                 "cron_hour": 1, "cron_minute": 0})
        assert resp.status_code == 404

    def test_scan_without_connection_returns_400(self, sit):
        client, tokens = sit
        from backend.api import tdsql_manage
        from backend.services.connection_registry import registry
        registry.disconnect()  # 清空活跃连接
        tdsql_manage._pool = None  # 清空V1.0兼容测试席位（其他用例可能注入过）
        resp = client.post("/api/v1/tdsql/slow-queries/fetch",
                           headers=tokens["sit_dba"],
                           json={"source": "digest",
                                 "time_window_start": "2026-07-01 00:00:00",
                                 "time_window_end": "2026-07-02 00:00:00"})
        assert resp.status_code == 400


class TestSITMiddlewareChain:
    """SIT-4: 中间件链与业务API集成"""

    def test_rbac_denied_recorded_in_metrics(self, sit):
        client, tokens = sit
        client.post("/api/v1/tdsql/connections", headers=tokens["sit_aud"],
                    json={"host": "x", "port": 1, "username": "u", "password": "p"})
        resp = client.get("/metrics")
        assert "tdsql_rbac_denied_total" in resp.text

    def test_mutating_request_writes_operation_log(self, sit):
        client, tokens = sit
        client.post("/api/v1/audit/sql", json={"sql": "SELECT 1 FROM t_a"},
                    headers=tokens["sit_dba"])
        resp = client.get("/api/v1/admin/operation-logs?operator=sit_dba",
                          headers=tokens["admin"])
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_login_metrics_counted(self, sit):
        client, tokens = sit
        client.post("/api/v1/auth/login",
                    json={"username": "no_such_user", "password": "Xx@12345"})
        resp = client.get("/metrics")
        assert 'tdsql_login_total{result="failed"}' in resp.text
        assert 'tdsql_login_total{result="success"}' in resp.text
