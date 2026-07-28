# -*- coding: utf-8 -*-
"""
V1.4 全局规则集 + 实例级质量门禁 功能验收测试
================================================
依据：docs/DETAIL-v1.4 §9 测试用例清单（T01-T24）
说明：测试服务以单 worker 启动，规则集切换即时生效（生产双 worker 为最长 30s，
      属设计承诺，非自动化覆盖项）。切换全局规则集的用例结束后统一恢复 default。
"""
import pytest

from conftest import auth, rid


# ════════════════════════════════════════════════════════════
# 工具：全局规则集切换（自动恢复）
# ════════════════════════════════════════════════════════════
@pytest.fixture
def restore_active(client, admin_token):
    """用例结束后把全局生效规则集恢复为 default，避免污染其它测试。"""
    yield
    client.post("/api/v1/rulesets/default/activate", headers=auth(admin_token))


def _activate(client, admin_token, rs_id):
    return client.post(f"/api/v1/rulesets/{rs_id}/activate", headers=auth(admin_token))


# ════════════════════════════════════════════════════════════
# 一、全局规则集解析（T01-T07）
# ════════════════════════════════════════════════════════════
class TestActiveRuleSet:

    def test_t01_active_returns_config(self, client, admin_token):
        """T01 查询当前生效规则集正常返回"""
        r = client.get("/api/v1/rulesets/active", headers=auth(admin_token))
        assert r.status_code == 200
        body = r.json()
        assert body["rule_set_id"]
        assert "cache_ttl_seconds" in body

    def test_t05_list_has_is_active(self, client, admin_token):
        """T05/列表 规则集列表返回派生 is_active 与 active_rule_set_id"""
        r = client.get("/api/v1/rulesets", headers=auth(admin_token))
        assert r.status_code == 200
        body = r.json()
        assert "active_rule_set_id" in body
        actives = [x for x in body["rulesets"] if x.get("is_active")]
        assert len(actives) == 1, "有且仅有一个生效规则集（INV-1）"

    def test_t06_switch_takes_effect(self, client, admin_token, restore_active):
        """T06 切换后本进程立即生效"""
        rs_id = rid("t3p_rs_")
        client.post("/api/v1/rulesets", headers=auth(admin_token),
                    json={"id": rs_id, "name": "T06规则集", "items": []})
        r = _activate(client, admin_token, rs_id)
        assert r.status_code == 200
        cur = client.get("/api/v1/rulesets/active", headers=auth(admin_token)).json()
        assert cur["rule_set_id"] == rs_id
        client.delete(f"/api/v1/rulesets/{rs_id}", headers=auth(admin_token))

    def test_t21_non_admin_cannot_switch(self, client, tokens):
        """T21 非管理员切换生效规则集 → 403"""
        r = client.post("/api/v1/rulesets/default/activate", headers=auth(tokens["dba"]))
        assert r.status_code == 403


# ════════════════════════════════════════════════════════════
# 二、核心反作弊：尺度全局唯一（T08/T09/T23）
# ════════════════════════════════════════════════════════════
class TestScaleGlobal:

    def test_t08_project_id_does_not_change_result(self, client, admin_token, restore_active):
        """T08【核心验收】传不同 project_id，审核结果必须完全一致"""
        _activate(client, admin_token, "default")
        sql = "SELECT * FROM t_order WHERE DATE(create_time) = '2026-01-01'"
        results = []
        for pid in ("proj_A", "proj_B", ""):
            body = {"sql": sql}
            if pid:
                body["project_id"] = pid
            r = client.post("/api/v1/audit/sql", headers=auth(admin_token), json=body)
            assert r.status_code == 200
            vio = {(v["rule_id"], v["severity"]) for v in r.json()["violations"]}
            results.append(vio)
        assert results[0] == results[1] == results[2], \
            f"尺度随 project_id 变化，反作弊失败: {[len(x) for x in results]}"
        assert results[0], "该 SQL 应命中规则（确保比对有效）"

    def test_t09_switch_changes_result(self, client, admin_token, restore_active):
        """T09 切换全局规则集后审核结果随之改变"""
        sql = "SELECT * FROM t_order"
        _activate(client, admin_token, "default")
        r1 = client.post("/api/v1/audit/sql", headers=auth(admin_token), json={"sql": sql})
        rules_default = {v["rule_id"] for v in r1.json()["violations"]}
        assert "R012" in rules_default, "default 下 SELECT * 应命中 R012"

        # 建一个禁用 R012 的规则集并激活
        rs_id = rid("t3p_noR012_")
        client.post("/api/v1/rulesets", headers=auth(admin_token),
                    json={"id": rs_id, "name": "禁用R012", "items": [
                        {"rule_id": "R012", "enabled": False}]})
        _activate(client, admin_token, rs_id)
        r2 = client.post("/api/v1/audit/sql", headers=auth(admin_token), json={"sql": sql})
        rules_after = {v["rule_id"] for v in r2.json()["violations"]}
        assert "R012" not in rules_after, "禁用 R012 后不应再命中"
        client.delete(f"/api/v1/rulesets/{rs_id}", headers=auth(admin_token))

    def test_t23_deprecated_project_id_hint(self, client, admin_token):
        """T23 兼容期：传 project_id 返回 deprecated 提示且不影响结果"""
        r = client.post("/api/v1/audit/sql", headers=auth(admin_token),
                        json={"sql": "SELECT 1", "project_id": "legacy_proj"})
        body = r.json()
        assert body.get("deprecated_params"), "应返回 deprecated_params 提示"
        assert "project_id" in body["deprecated_params"]
        assert body.get("rule_set_id"), "响应应标注本次生效规则集"

    def test_t10_audit_record_has_rule_set_id(self, client, admin_token):
        """T10 审核记录落 rule_set_id（尺度可追溯）"""
        r = client.post("/api/v1/audit/sql", headers=auth(admin_token),
                        json={"sql": "SELECT * FROM t_x"})
        assert r.json().get("rule_set_id") == "default"


# ════════════════════════════════════════════════════════════
# 三、规则集删除约束（T19/T20）
# ════════════════════════════════════════════════════════════
class TestRuleSetDelete:

    def test_t19_active_ruleset_cannot_delete(self, client, admin_token, restore_active):
        """T19 启用中的规则集不可删除 → 409 E5003"""
        rs_id = rid("t3p_active_")
        client.post("/api/v1/rulesets", headers=auth(admin_token),
                    json={"id": rs_id, "name": "启用中", "items": []})
        _activate(client, admin_token, rs_id)
        r = client.delete(f"/api/v1/rulesets/{rs_id}", headers=auth(admin_token))
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "E5003"
        _activate(client, admin_token, "default")
        client.delete(f"/api/v1/rulesets/{rs_id}", headers=auth(admin_token))

    def test_t20_builtin_cannot_delete(self, client, admin_token):
        """T20 内置 default 规则集不可删除 → 409 E5004"""
        r = client.delete("/api/v1/rulesets/default", headers=auth(admin_token))
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "E5004"


# ════════════════════════════════════════════════════════════
# 四、实例级质量门禁（T11-T15）
# ════════════════════════════════════════════════════════════
class TestInstanceGate:

    def test_t11b_default_equivalent_to_v13(self, client, admin_token):
        """T11b 门禁默认值与 V1.3 等价：未配置实例走 0 / -1"""
        r = client.get("/api/v1/gate/instances", headers=auth(admin_token))
        assert r.status_code == 200
        body = r.json()
        assert body["default_rule"]["max_error_count"] == 0
        assert body["default_rule"]["max_warning_count"] == -1

    def test_t15c_connection_list_has_gate_fields(self, client, admin_token):
        """T15c 实例列表响应含门禁字段"""
        r = client.get("/api/v1/tdsql/connections", headers=auth(admin_token))
        assert r.status_code == 200
        conns = r.json().get("connections", [])
        if conns:
            c = conns[0]
            assert "max_error_count" in c
            assert "max_warning_count" in c
            assert "gate_is_default" in c

    def test_t14_invalid_limit_rejected(self, client, admin_token):
        """T14 非法上限 -2 被拒 → 400 E5013"""
        # 需要一个真实存在的实例；若无实例则用不存在的实例测 E5012
        conns = client.get("/api/v1/tdsql/connections", headers=auth(admin_token)).json().get("connections", [])
        if not conns:
            pytest.skip("无可用实例，跳过非法上限测试")
        cid = conns[0]["id"]
        r = client.put(f"/api/v1/gate/instances/{cid}", headers=auth(admin_token),
                       json={"max_error_count": -2, "max_warning_count": -1})
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "E5013"

    def test_t12_t13_save_and_observe(self, client, admin_token):
        """T12/T13 保存门禁 + observe 模式 + 删除回落默认"""
        conns = client.get("/api/v1/tdsql/connections", headers=auth(admin_token)).json().get("connections", [])
        if not conns:
            pytest.skip("无可用实例，跳过门禁保存测试")
        cid = conns[0]["id"]
        # 保存 error=0/warning=0/observe
        r = client.put(f"/api/v1/gate/instances/{cid}", headers=auth(admin_token),
                       json={"max_error_count": 0, "max_warning_count": 0, "mode": "observe"})
        assert r.status_code == 200
        got = client.get(f"/api/v1/gate/instances/{cid}", headers=auth(admin_token)).json()
        assert got["max_warning_count"] == 0
        assert got["mode"] == "observe"
        assert got["is_default"] is False
        # 删除回落默认
        client.delete(f"/api/v1/gate/instances/{cid}", headers=auth(admin_token))
        got2 = client.get(f"/api/v1/gate/instances/{cid}", headers=auth(admin_token)).json()
        assert got2["is_default"] is True
        assert got2["max_warning_count"] == -1

    def test_t15b_non_admin_cannot_save_gate(self, client, tokens, admin_token):
        """T15b 非 admin 保存实例门禁 → 403"""
        conns = client.get("/api/v1/tdsql/connections", headers=auth(admin_token)).json().get("connections", [])
        if not conns:
            pytest.skip("无可用实例")
        cid = conns[0]["id"]
        r = client.put(f"/api/v1/gate/instances/{cid}", headers=auth(tokens["dba"]),
                       json={"max_error_count": 0, "max_warning_count": 0})
        assert r.status_code == 403


# ════════════════════════════════════════════════════════════
# 五、菜单隐藏（T15d）
# ════════════════════════════════════════════════════════════
class TestMenuHidden:

    def test_t15d_projects_gate_menu_hidden(self, client, tokens):
        """T15d 隐藏菜单后 dba 的可见菜单不含 projects/gate"""
        for role in ("dba", "developer", "auditor"):
            r = client.get("/api/v1/auth/visible-menus", headers=auth(tokens[role]))
            assert r.status_code == 200
            menus = set(r.json().get("menus", []))
            assert "projects" not in menus, f"{role} 仍可见 projects 菜单"
            assert "gate" not in menus, f"{role} 仍可见 gate 菜单"
            assert "rulesets" in menus or role == "developer", f"{role} 应可见 rulesets"
