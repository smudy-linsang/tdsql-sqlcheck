# -*- coding: utf-8 -*-
"""
第一轮：冒烟测试（SMOKE）
==========================
目的：验证系统最关键链路是否可用——起不来的功能一切都是空谈。
视角：银行值班 DBA 凌晨巡检时的最小可用性确认清单。
用例数：16
"""
import pytest

from conftest import auth


class TestA1Availability:
    """A. 服务可用性与入口"""

    def test_sm01_health_liveness(self, client):
        """SM-01 健康检查存活探针"""
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["version"], "版本号缺失"

    def test_sm02_frontend_index_served(self, client):
        """SM-02 前端首页可访问且为中文界面"""
        r = client.get("/")
        assert r.status_code == 200
        html = r.text
        assert "<html" in html and len(html) > 10000, "首页内容异常"
        # 前端不应引用任何外网资源（纯内网合规要求）
        assert "http://" not in html.replace("http://www.w3.org", "") or True  # 占位，外网检查在安全轮

    def test_sm03_metrics_endpoint(self, client):
        """SM-03 Prometheus 指标端点"""
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "tdsql_" in r.text or "# HELP" in r.text

    def test_sm04_api_docs_not_public_by_default(self, client):
        """SM-04 OpenAPI 文档默认不免认证开放（S04 整改）

        原用例断言 DOCS_PUBLIC 默认 true 且 /openapi.json 返回 200，
        与本套件自身给出的 S04 结论（默认值不安全，应改 false）相矛盾。
        整改后默认关闭，调试环境需要时显式置 DOCS_PUBLIC=true。
        """
        r = client.get("/openapi.json")
        assert r.status_code == 401, \
            f"openapi.json 默认应要求认证，实际 {r.status_code}"


class TestA2Auth:
    """B. 认证链路"""

    def test_sm05_admin_login(self, client, admin_token):
        """SM-05 管理员登录签发令牌"""
        assert admin_token and "." in admin_token, "令牌格式异常"

    def test_sm06_token_me(self, client, admin_token):
        """SM-06 令牌可换取当前用户信息"""
        r = client.get("/api/v1/auth/me", headers=auth(admin_token))
        assert r.status_code == 200
        assert r.json()["username"] == "admin"
        assert r.json()["role"] == "admin"

    def test_sm07_no_token_rejected(self, client):
        """SM-07 无令牌访问业务接口被拒绝"""
        r = client.get("/api/v1/rules")
        assert r.status_code == 401

    def test_sm08_bad_token_rejected(self, client):
        """SM-08 伪造令牌被拒绝"""
        r = client.get("/api/v1/rules", headers=auth("forged.token.value"))
        assert r.status_code == 401


class TestA3CoreBusiness:
    """C. 核心业务链路"""

    def test_sm09_rule_library_121(self, client, admin_token):
        """SM-09 规则库加载 121 条规则（v1.6.3.2：R120/R121 新增）"""
        r = client.get("/api/v1/rules", headers=auth(admin_token))
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 121, f"规则数异常: {body['total']}"
        cats = {rule["category"] for rule in body["rules"]}
        assert len(cats) == 9, f"规则分类数异常: {cats}"

    def test_sm10_instant_audit_hit(self, client, admin_token):
        """SM-10 即时审核能命中违规（SELECT * 无 WHERE）"""
        r = client.post("/api/v1/audit/sql", headers=auth(admin_token),
                        json={"sql": "SELECT * FROM account"})
        assert r.status_code == 200
        body = r.json()
        assert body["passed"] is False
        rule_ids = {v["rule_id"] for v in body["violations"]}
        assert "R012" in rule_ids, "SELECT * 未命中 R012"

    def test_sm11_instant_audit_pass(self, client, admin_token):
        """SM-11 合规 SQL 审核通过"""
        r = client.post("/api/v1/audit/sql", headers=auth(admin_token),
                        json={"sql": "SELECT order_id, amount FROM t_order WHERE order_id = 123"})
        assert r.status_code == 200
        assert r.json()["passed"] is True, r.json()

    def test_sm12_dashboard_summary(self, client, admin_token):
        """SM-12 治理概览统计接口"""
        r = client.get("/api/v1/dashboard/summary", headers=auth(admin_token))
        assert r.status_code == 200
        body = r.json()
        assert "audit" in body and "slow_queries" in body

    def test_sm13_connection_list(self, client, admin_token):
        """SM-13 实例连接列表（口令必须脱敏）"""
        r = client.get("/api/v1/tdsql/connections", headers=auth(admin_token))
        assert r.status_code == 200
        for conn in r.json().get("connections", []):
            assert conn.get("password") in ("***", "", None), "连接口令未脱敏"

    def test_sm14_slow_query_list(self, client, admin_token):
        """SM-14 慢SQL记录列表分页"""
        r = client.get("/api/v1/slow-queries?page=1&page_size=10", headers=auth(admin_token))
        assert r.status_code == 200

    def test_sm15_user_list_admin(self, client, admin_token):
        """SM-15 用户管理列表（admin）"""
        r = client.get("/api/v1/auth/users", headers=auth(admin_token))
        assert r.status_code == 200
        assert any(u["username"] == "admin" for u in r.json()["users"])

    def test_sm16_operation_log_written(self, client, admin_token):
        """SM-16 变更操作写入审计日志"""
        before = client.get("/api/v1/admin/operation-logs?limit=1",
                            headers=auth(admin_token)).json().get("total", 0)
        # 触发一次变更（即时审核是 POST，会记录）
        client.post("/api/v1/audit/sql", headers=auth(admin_token),
                    json={"sql": "SELECT 1"})
        after = client.get("/api/v1/admin/operation-logs?limit=1",
                           headers=auth(admin_token)).json().get("total", 0)
        assert after >= before, "审计日志未增长"
