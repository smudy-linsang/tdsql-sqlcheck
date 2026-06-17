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
        assert data["total"] == 22, f"Expected 22 rules, got {data['total']}"
        assert len(data["rules"]) == 22, f"Expected 22 rules in list, got {len(data['rules'])}"

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
        
        # 验证分类
        expected_cats = ["naming", "ddl", "dml", "distributed"]
        for cat in expected_cats:
            assert cat in categories, f"Missing category: {cat}"
        
        # 验证每个分类的规则数
        assert len(categories["naming"]) == 2, f"Expected 2 naming rules"
        assert len(categories["ddl"]) == 9, f"Expected 9 DDL rules"
        assert len(categories["dml"]) == 8, f"Expected 8 DML rules"
        assert len(categories["distributed"]) == 3, f"Expected 3 distributed rules"

    def test_rules_have_valid_categories(self):
        """测试规则类别有效性"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        valid_categories = {"naming", "ddl", "dml", "distributed"}
        for rule in data["rules"]:
            assert rule["category"] in valid_categories, f"Invalid category: {rule['category']}"

    def test_rules_have_valid_severity(self):
        """测试规则严重级别有效性"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        valid_severities = {"ERROR", "WARNING"}
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

    def test_ddl_rules_have_correct_ids(self):
        """测试DDL规则ID"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        ddl_rules = [r for r in data["rules"] if r["category"] == "ddl"]
        ddl_ids = {r["rule_id"] for r in ddl_rules}
        # DDL 规则是 R003-R011，使用更可靠的生成方式
        expected = set()
        for i in range(3, 12):
            if i < 10:
                expected.add(f"R00{i}")
            else:
                expected.add(f"R0{i}")
        assert ddl_ids == expected, f"Expected DDL IDs {expected}, got {ddl_ids}"

    def test_dml_rules_have_correct_ids(self):
        """测试DML规则ID"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        dml_rules = [r for r in data["rules"] if r["category"] == "dml"]
        dml_ids = {r["rule_id"] for r in dml_rules}
        expected = {f"R01{i}" for i in range(2, 10)}  # R012-R019
        assert dml_ids == expected, f"Expected DML IDs {expected}, got {dml_ids}"

    def test_distributed_rules_have_correct_ids(self):
        """测试分布式规则ID"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        dist_rules = [r for r in data["rules"] if r["category"] == "distributed"]
        dist_ids = {r["rule_id"] for r in dist_rules}
        expected = {"R020", "R021", "R022"}
        assert dist_ids == expected, f"Expected distributed IDs {expected}, got {dist_ids}"


class TestFrontendIntegration:
    """前端集成测试"""

    def test_frontend_page_accessible(self):
        """测试前端页面可访问"""
        resp = requests.get("http://localhost:8000/")
        assert resp.status_code == 200, f"Frontend not accessible: {resp.status_code}"
        assert "text/html" in resp.headers.get("Content-Type", ""), "Not returning HTML"

    def test_frontend_contains_rules_code(self):
        """测试前端包含规则页面代码"""
        resp = requests.get("http://localhost:8000/")
        content = resp.text
        assert 'currentPage === \'rules\'' in content, "Rules page menu item not found"
        assert "rulesList" in content, "rulesList not found in frontend"
        assert "rulesByCategory" in content, "rulesByCategory not found in frontend"
        assert "loadRules" in content, "loadRules function not found in frontend"
        assert "v-for=\"rule in rulesByCategory.naming\"" in content, "Naming rules loop not found"

    def test_frontend_has_rules_css(self):
        """测试前端包含规则样式"""
        resp = requests.get("http://localhost:8000/")
        content = resp.text
        assert ".rules-grid" in content, "rules-grid CSS class not found"
        assert ".rule-card" in content, "rule-card CSS class not found"
        assert ".category-title" in content, "category-title CSS class not found"


class TestEndToEnd:
    """端到端测试"""

    def test_rules_page_loads_from_api(self):
        """测试规则页面能从API加载数据"""
        # 1. 验证 API 可用
        resp = requests.get(f"{API_BASE}/rules")
        assert resp.status_code == 200
        data = resp.json()
        
        # 2. 验证数据完整性
        assert data["total"] == 22
        assert len(data["rules"]) == 22
        
        # 3. 验证每条规则都有描述
        for rule in data["rules"]:
            assert rule["description"], f"Rule {rule['rule_id']} has no description"
            assert len(rule["description"]) > 5, f"Rule {rule['rule_id']} description too short"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
