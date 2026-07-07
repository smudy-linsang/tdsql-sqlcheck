"""
SIT 测试 - SQL审核规则功能

测试规则 API 和前端展示功能
"""
import pytest
import requests

API_BASE = "http://localhost:8000/api/v1"


class TestRulesAPI:
    """规则管理 API 测试"""

    def test_get_all_rules(self):
        """测试获取所有规则"""
        resp = requests.get(f"{API_BASE}/rules")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        assert "total" in data, "Response missing 'total' field"
        assert "rules" in data, "Response missing 'rules' field"
        assert data["total"] == 119, f"Expected 119 rules, got {data['total']}"
        assert len(data["rules"]) == 119, f"Expected 119 rules in list, got {len(data['rules'])}"

    def test_rules_structure(self):
        """测试规则数据结构"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        rule = data["rules"][0]
        required_fields = ["rule_id", "category", "severity", "description", "enabled"]
        for field in required_fields:
            assert field in rule, f"Rule missing required field: {field}"

    def test_rules_by_category(self):
        """测试按类别获取规则"""
        resp = requests.get(f"{API_BASE}/rules/categories")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        assert "categories" in data, "Response missing 'categories' field"
        categories = data["categories"]

        # 验证8个分类
        expected_cats = ["naming", "ddl", "dml", "index", "distributed", "security", "performance", "transaction", "oracle_compat"]
        for cat in expected_cats:
            assert cat in categories, f"Missing category: {cat}"

        # 验证每个分类的规则数
        assert len(categories["naming"]) == 5, f"Expected 5 naming rules"
        assert len(categories["ddl"]) == 22, f"Expected 22 DDL rules"
        assert len(categories["dml"]) == 9, f"Expected 9 DML rules"
        assert len(categories["index"]) == 10, f"Expected 10 index rules"
        assert len(categories["distributed"]) == 14, f"Expected 14 distributed rules"
        assert len(categories["security"]) == 8, f"Expected 8 security rules"
        assert len(categories["performance"]) == 5, f"Expected 5 performance rules"
        assert len(categories["transaction"]) == 4, f"Expected 4 transaction rules"

    def test_rules_have_valid_categories(self):
        """测试规则类别有效性"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        valid_categories = {"naming", "ddl", "dml", "index", "distributed", "security", "performance", "transaction", "oracle_compat"}
        for rule in data["rules"]:
            assert rule["category"] in valid_categories, f"Invalid category: {rule['category']}"

    def test_rules_have_valid_severity(self):
        """测试规则严重级别有效性"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        valid_severities = {"ERROR", "WARNING", "INFO"}
        for rule in data["rules"]:
            assert rule["severity"] in valid_severities, f"Invalid severity: {rule['severity']}"

    def test_rules_are_enabled(self):
        """测试规则默认启用"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        for rule in data["rules"]:
            assert rule["enabled"] is True, f"Rule {rule['rule_id']} should be enabled by default"

    def test_naming_rules_have_correct_ids(self):
        """测试命名规则ID"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        naming_rules = [r for r in data["rules"] if r["category"] == "naming"]
        naming_ids = {r["rule_id"] for r in naming_rules}
        assert "R001" in naming_ids, "Missing R001 naming rule"
        assert "R002" in naming_ids, "Missing R002 naming rule"
        assert len(naming_ids) == 5, f"Expected 5 naming rule IDs, got {len(naming_ids)}"

    def test_ddl_rules_have_correct_ids(self):
        """测试DDL规则ID"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        ddl_rules = [r for r in data["rules"] if r["category"] == "ddl"]
        ddl_ids = {r["rule_id"] for r in ddl_rules}
        assert "R003" in ddl_ids, "Missing R003 DDL rule"
        assert len(ddl_ids) == 22, f"Expected 22 DDL rule IDs, got {len(ddl_ids)}"

    def test_dml_rules_have_correct_ids(self):
        """测试DML规则ID"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        dml_rules = [r for r in data["rules"] if r["category"] == "dml"]
        dml_ids = {r["rule_id"] for r in dml_rules}
        assert "R012" in dml_ids, "Missing R012 DML rule"
        assert len(dml_ids) == 9, f"Expected 9 DML rule IDs, got {len(dml_ids)}"

    def test_distributed_rules_have_correct_ids(self):
        """测试分布式规则ID"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        dist_rules = [r for r in data["rules"] if r["category"] == "distributed"]
        dist_ids = {r["rule_id"] for r in dist_rules}
        assert "R020" in dist_ids, "Missing R020 distributed rule"
        assert len(dist_ids) == 14, f"Expected 14 distributed rule IDs, got {len(dist_ids)}"


class TestFrontendIntegration:
    """前端集成测试"""

    def test_frontend_page_accessible(self):
        """测试前端页面可访问"""
        resp = requests.get("http://localhost:8000/")
        assert resp.status_code == 200, f"Frontend not accessible: {resp.status_code}"
        assert "text/html" in resp.headers.get("Content-Type", ""), "Not returning HTML"

    def test_frontend_contains_rules_code(self):
        """测试前端包含规则页面代码（V3.0后JS在app.js中）"""
        resp = requests.get("http://localhost:8000/static/js/app.js")
        assert resp.status_code == 200, "app.js not accessible"
        content = resp.text
        assert "rulesList" in content, "rulesList not found in app.js"
        assert "rulesByCategory" in content, "rulesByCategory not found in app.js"
        assert "loadRules" in content, "loadRules function not found in app.js"

    def test_frontend_has_rules_css(self):
        """测试前端CSS可访问"""
        resp = requests.get("http://localhost:8000/static/css/app.css")
        assert resp.status_code == 200, "app.css not accessible"


class TestEndToEnd:
    """端到端测试"""

    def test_rules_page_loads_from_api(self):
        """测试规则页面能从API加载数据"""
        # 1. 验证 API 可用
        resp = requests.get(f"{API_BASE}/rules")
        assert resp.status_code == 200
        data = resp.json()

        # 2. 验证数据完整性
        assert data["total"] == 119
        assert len(data["rules"]) == 119

        # 3. 验证每条规则都有描述
        for rule in data["rules"]:
            assert rule["description"], f"Rule {rule['rule_id']} has no description"
            assert len(rule["description"]) > 5, f"Rule {rule['rule_id']} description too short"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
