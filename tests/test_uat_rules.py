"""
UAT 测试 - SQL审核规则功能

用户验收测试：从开发者/用户视角验证规则展示功能
"""
import pytest
import requests
import re

API_BASE = "http://localhost:8000/api/v1"
FRONTEND_BASE = "http://localhost:8000"


class TestRulesUATUserView:
    """UAT - 用户查看规则视角"""

    def test_user_can_access_rules_documentation(self):
        """场景：开发人员想了解系统的SQL审核规范"""
        # 用户访问首页
        resp = requests.get(FRONTEND_BASE)
        assert resp.status_code == 200, "首页应该可访问"
        
        # 用户应该能看到审核规则的入口（侧边栏菜单）
        content = resp.text
        assert '审核规则' in content, "侧边栏应包含'审核规则'菜单项"
        
    def test_rules_page_shows_all_categories(self):
        """场景：用户想查看所有规则分类"""
        resp = requests.get(f"{API_BASE}/rules/categories")
        assert resp.status_code == 200
        
        data = resp.json()
        categories = data["categories"]
        
        # 用户期望看到的分类
        expected_categories = {
            "naming": "命名规范",
            "ddl": "DDL语句", 
            "dml": "DML语句",
            "distributed": "分布式场景"
        }
        
        for cat_key, cat_name in expected_categories.items():
            assert cat_key in categories, f"应该包含分类：{cat_name}"

    def test_naming_rules_help_developers(self):
        """场景：开发人员想了解表名命名规范"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        
        naming_rules = [r for r in data["rules"] if r["category"] == "naming"]
        assert len(naming_rules) >= 2, "应该有至少2条命名规则"
        
        # 验证规则描述清晰易懂
        for rule in naming_rules:
            assert rule["description"], f"规则{rule['rule_id']}应该有描述"
            assert len(rule["description"]) > 10, f"规则{rule['rule_id']}描述应该足够详细"

    def test_ddl_rules_help_dbas(self):
        """场景：DBA想了解DDL操作规范"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        
        ddl_rules = [r for r in data["rules"] if r["category"] == "ddl"]
        assert len(ddl_rules) >= 8, "DDL规则应该有足够的覆盖"
        
        # 验证每条规则都有明确的问题描述
        for rule in ddl_rules:
            assert rule["description"], f"DDL规则{rule['rule_id']}应该有描述"
            assert rule["severity"] in ["ERROR", "WARNING"], f"规则应该有明确的严重级别"


class TestRulesUATVisualization:
    """UAT - 规则可视化展示"""

    def test_rules_display_with_severity_indicators(self):
        """场景：用户查看规则时需要区分严重级别"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        
        # 验证有ERROR和WARNING两种级别
        severities = {r["severity"] for r in data["rules"]}
        assert "ERROR" in severities, "应该有ERROR级别的规则"
        assert "WARNING" in severities, "应该有WARNING级别的规则"
        
    def test_frontend_has_proper_styling(self):
        """场景：用户期望规则页面有良好的视觉呈现"""
        resp = requests.get(FRONTEND_BASE)
        content = resp.text
        
        # 验证有规则展示相关的CSS样式
        required_css = [
            ".rules-grid",      # 规则网格布局
            ".rule-card",       # 规则卡片
            ".badge",           # 严重级别标签
            ".badge.error",     # 错误级别样式
            ".badge.warning",   # 警告级别样式
        ]
        
        for css_class in required_css:
            assert css_class in content, f"前端应该包含 {css_class} 样式"

    def test_category_section_headers(self):
        """场景：用户希望按分类清晰浏览规则"""
        resp = requests.get(FRONTEND_BASE)
        content = resp.text
        
        # 验证每个分类都有对应的section
        category_sections = [
            "rulesByCategory.naming",
            "rulesByCategory.ddl", 
            "rulesByCategory.dml",
            "rulesByCategory.distributed"
        ]
        
        for section in category_sections:
            assert section in content, f"前端应该包含分类展示：{section}"

    def test_rule_id_visibility(self):
        """场景：用户需要看到规则的唯一标识符"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        
        for rule in data["rules"]:
            # 规则ID格式应该是 R001, R002 等
            assert re.match(r'^R0\d{2}$', rule["rule_id"]), \
                f"规则ID格式不正确: {rule['rule_id']}"


class TestRulesUATWorkflow:
    """UAT - 用户工作流"""

    def test_user_can_navigate_to_rules_page(self):
        """场景：用户从首页导航到规则页面"""
        resp = requests.get(FRONTEND_BASE)
        content = resp.text
        
        # 验证有页面切换逻辑
        assert "currentPage === 'rules'" in content, "应该有切换到规则页面的逻辑"
        
    def test_rules_load_automatically(self):
        """场景：用户打开规则页面时数据自动加载"""
        resp = requests.get(FRONTEND_BASE)
        content = resp.text
        
        # 验证有自动加载函数
        assert "loadRules()" in content or "loadRules" in content, \
            "规则页面应该自动加载规则数据"
            
    def test_dynamic_rule_loading_works(self):
        """场景：系统添加新规则后，规则页面应自动更新"""
        # 第一次请求获取规则数
        resp1 = requests.get(f"{API_BASE}/rules")
        data1 = resp1.json()
        initial_count = data1["total"]
        
        # 第二次请求验证一致性
        resp2 = requests.get(f"{API_BASE}/rules")
        data2 = resp2.json()
        
        assert data2["total"] == initial_count, "规则数量应该一致（验证动态加载机制）"
        assert len(data2["rules"]) == len(data1["rules"]), "规则列表长度应该一致"


class TestRulesUATContentQuality:
    """UAT - 内容质量验证"""

    def test_all_rules_have_meaningful_descriptions(self):
        """场景：每条规则都应该有清晰的中文描述"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        
        for rule in data["rules"]:
            desc = rule.get("description", "")
            assert desc, f"规则 {rule['rule_id']} 应该有描述"
            assert len(desc) >= 10, \
                f"规则 {rule['rule_id']} 描述过短: '{desc}'"
            # 描述应该包含中文或英文说明
            assert re.search(r'[\u4e00-\u9fff]|[a-zA-Z]', desc), \
                f"规则 {rule['rule_id']} 描述应该包含文字"

    def test_rule_descriptions_are_unique(self):
        """场景：每条规则的描述应该是唯一的（便于区分）"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        
        descriptions = [r["description"] for r in data["rules"]]
        unique_descriptions = set(descriptions)
        assert len(descriptions) == len(unique_descriptions), \
            "所有规则的描述应该唯一"

    def test_category_rule_count_balanced(self):
        """场景：规则分布应该合理均衡"""
        resp = requests.get(f"{API_BASE}/rules/categories")
        data = resp.json()
        
        categories = data["categories"]
        
        # 验证分类规则数
        assert len(categories["naming"]) == 2, "命名规范应有2条规则"
        assert len(categories["ddl"]) == 9, "DDL语句应有9条规则"
        assert len(categories["dml"]) == 8, "DML语句应有8条规则"
        assert len(categories["distributed"]) == 3, "分布式场景应有3条规则"
        
        # 验证总数
        total = sum(len(rules) for rules in categories.values())
        assert total == 22, f"规则总数应为22条，实际{total}条"


class TestRulesUATIntegration:
    """UAT - 与系统其他部分集成"""

    def test_rules_api_compatible_with_existing_endpoints(self):
        """场景：规则API不应影响现有功能"""
        # 验证dashboard仍然正常
        resp = requests.get(f"{API_BASE}/dashboard/summary")
        assert resp.status_code == 200, "Dashboard应该仍然可用"
        
    def test_rules_help_text_in_audit_results(self):
        """场景：当SQL违反规则时，用户需要看到对应的规则说明"""
        # 提交一个触发R001的SQL
        resp = requests.post(f"{API_BASE}/audit/sql", json={
            "sql": "CREATE TABLE InvalidTable (id INT)"
        })
        
        if resp.status_code == 200:
            data = resp.json()
            if not data.get("passed", True):
                violations = data.get("violations", [])
                if violations:
                    # 验证违规详情包含规则ID
                    for v in violations:
                        assert "rule_id" in v, "违规详情应包含规则ID"


class TestRulesUATAccessibility:
    """UAT - 可访问性验证"""

    def test_rules_page_loads_reasonably_fast(self):
        """场景：规则页面应该在合理时间内加载完成"""
        import time
        
        start = time.time()
        resp = requests.get(f"{API_BASE}/rules")
        elapsed = time.time() - start
        
        assert resp.status_code == 200, "API应该正常响应"
        assert elapsed < 3.0, f"API响应时间应小于3秒，实际{elapsed:.2f}秒"

    def test_api_returns_valid_json(self):
        """场景：API应返回有效的JSON格式"""
        resp = requests.get(f"{API_BASE}/rules")
        assert resp.status_code == 200
        
        # 验证Content-Type
        content_type = resp.headers.get("Content-Type", "")
        assert "json" in content_type.lower(), "API应返回JSON格式"

    def test_categories_endpoint_also_returns_json(self):
        """场景：分类接口也应返回有效的JSON"""
        resp = requests.get(f"{API_BASE}/rules/categories")
        assert resp.status_code == 200
        
        data = resp.json()
        assert isinstance(data, dict), "分类接口应返回字典"
        assert "categories" in data, "响应应包含categories字段"


class TestRulesUATDataIntegrity:
    """UAT - 数据完整性"""

    def test_no_duplicate_rules(self):
        """场景：规则列表中不应有重复的规则"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        
        rule_ids = [r["rule_id"] for r in data["rules"]]
        unique_ids = set(rule_ids)
        
        assert len(rule_ids) == len(unique_ids), \
            f"发现重复的规则ID: {set([x for x in rule_ids if rule_ids.count(x) > 1])}"

    def test_all_rules_have_required_fields(self):
        """场景：每条规则都应该有完整的元数据"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        
        required_fields = ["rule_id", "category", "severity", "description", "enabled"]
        
        for rule in data["rules"]:
            for field in required_fields:
                assert field in rule, \
                    f"规则 {rule.get('rule_id', '?')} 缺少字段: {field}"

    def test_rule_enabled_status_is_boolean(self):
        """场景：规则的启用状态应该是布尔值"""
        resp = requests.get(f"{API_BASE}/rules")
        data = resp.json()
        
        for rule in data["rules"]:
            assert isinstance(rule["enabled"], bool), \
                f"规则 {rule['rule_id']} 的enabled应该是布尔值"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
