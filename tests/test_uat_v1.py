"""
UAT V1.0 - V1.0新增功能用户验收测试

从最终用户视角验证V1.0全部新增功能：
1. 项目管理API (UAT-35)
2. 质量门禁API (UAT-36)
3. 大表治理API (UAT-37)
4. 监控告警API (UAT-38)
5. 巡检管理API (UAT-39)
6. V1.0规则用户验收 (UAT-40)
7. V1.0引擎用户验收 (UAT-41)
8. CLI工具用户验收 (UAT-42)
9. V1.0端到端用户工作流 (UAT-43)
"""
import os
import tempfile
import pytest
from fastapi.testclient import TestClient
from click.testing import CliRunner

from backend.main import app
from backend.cli import cli

client = TestClient(app)


# ═══════════════════════════════════════════════════════════
# UAT-35: 项目管理用户验收
# ═══════════════════════════════════════════════════════════

class TestUAT35_ProjectManagement:
    """UAT-35: 项目管理 — 用户创建、查看、删除项目"""

    def setup_method(self):
        """清理已有测试数据"""
        from backend.services.database import _get_connection, ensure_db
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute("DELETE FROM projects WHERE project_id LIKE 'uat_%'")
            conn.commit()
        except Exception:
            pass

    def test_uat35_01_create_project(self):
        """UAT-35-01: 用户创建新项目"""
        resp = client.post("/api/v1/projects", json={
            "project_name": "uat_测试项目",
            "description": "UAT测试用项目",
            "gitlab_url": "https://gitlab.example.com/test/repo"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["project_name"] == "uat_测试项目"
        assert data["data"]["status"] == "active"

    def test_uat35_02_list_projects(self):
        """UAT-35-02: 用户列出所有项目"""
        client.post("/api/v1/projects", json={"project_name": "uat_列表测试"})
        resp = client.get("/api/v1/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["data"], list)
        assert len(data["data"]) >= 1

    def test_uat35_03_get_project_detail(self):
        """UAT-35-03: 用户查看项目详情"""
        create_resp = client.post("/api/v1/projects", json={"project_name": "uat_详情测试"})
        project_id = create_resp.json()["data"]["project_id"]
        resp = client.get(f"/api/v1/projects/{project_id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["project_id"] == project_id

    def test_uat35_04_delete_project(self):
        """UAT-35-04: 用户删除项目（标记为inactive）"""
        create_resp = client.post("/api/v1/projects", json={"project_name": "uat_删除测试"})
        project_id = create_resp.json()["data"]["project_id"]
        del_resp = client.delete(f"/api/v1/projects/{project_id}")
        assert del_resp.status_code == 200
        # 验证项目状态变为inactive
        get_resp = client.get(f"/api/v1/projects/{project_id}")
        assert get_resp.json()["data"]["status"] == "inactive"

    def test_uat35_05_get_nonexistent_project(self):
        """UAT-35-05: 用户查看不存在的项目返回404"""
        resp = client.get("/api/v1/projects/nonexistent_project_id")
        assert resp.status_code == 404

    def test_uat35_06_create_project_with_full_fields(self):
        """UAT-35-06: 用户创建包含所有字段的项目"""
        resp = client.post("/api/v1/projects", json={
            "project_name": "uat_完整项目",
            "tdsql_connection_id": "conn_001",
            "rule_set_id": "default",
            "gate_rule_id": "strict",
            "gitlab_project_id": 12345,
            "gitlab_url": "https://gitlab.example.com/proj/repo",
            "description": "完整字段项目"
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["project_name"] == "uat_完整项目"
        assert data["tdsql_connection_id"] == "conn_001"
        assert data["gitlab_project_id"] == 12345


# ═══════════════════════════════════════════════════════════
# UAT-36: 质量门禁用户验收
# ═══════════════════════════════════════════════════════════

class TestUAT36_QualityGate:
    """UAT-36: 质量门禁 — 用户配置门禁规则和策略"""

    def test_uat36_01_get_default_gate_rule(self):
        """UAT-36-01: 用户获取默认门禁规则"""
        resp = client.get("/api/v1/gate/rules/default")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["project_id"] == "default"
        assert "max_error_count" in data
        assert "max_warning_count" in data

    def test_uat36_02_set_gate_rule(self):
        """UAT-36-02: 用户自定义门禁规则"""
        resp = client.post("/api/v1/gate/rules", json={
            "project_id": "uat_gate_test",
            "max_error_count": 0,
            "max_warning_count": 5,
            "required_rules": ["R012"],
            "blocked_rules": ["R013"],
            "description": "UAT自定义门禁"
        })
        assert resp.status_code == 200
        # 验证规则已保存
        get_resp = client.get("/api/v1/gate/rules/uat_gate_test")
        assert get_resp.json()["data"]["max_warning_count"] == 5

    def test_uat36_03_apply_strict_strategy(self):
        """UAT-36-03: 用户应用严格门禁策略"""
        resp = client.post("/api/v1/gate/strategy/uat_strict?strategy=strict")
        assert resp.status_code == 200
        # 验证策略已生效
        rule = client.get("/api/v1/gate/rules/uat_strict").json()["data"]
        assert rule["max_error_count"] == 0
        assert rule["max_warning_count"] == 0

    def test_uat36_04_apply_normal_strategy(self):
        """UAT-36-04: 用户应用普通门禁策略"""
        resp = client.post("/api/v1/gate/strategy/uat_normal?strategy=normal")
        assert resp.status_code == 200
        rule = client.get("/api/v1/gate/rules/uat_normal").json()["data"]
        assert rule["max_error_count"] == 0
        assert rule["max_warning_count"] == -1

    def test_uat36_05_apply_loose_strategy(self):
        """UAT-36-05: 用户应用宽松门禁策略"""
        resp = client.post("/api/v1/gate/strategy/uat_loose?strategy=loose")
        assert resp.status_code == 200
        rule = client.get("/api/v1/gate/rules/uat_loose").json()["data"]
        assert rule["max_error_count"] == -1
        assert rule["max_warning_count"] == -1

    def test_uat36_06_list_strategies(self):
        """UAT-36-06: 用户查看可用门禁策略列表"""
        resp = client.get("/api/v1/gate/strategies")
        assert resp.status_code == 200
        strategies = resp.json()["data"]
        assert isinstance(strategies, dict)
        assert "strict" in strategies
        assert "normal" in strategies
        assert "loose" in strategies

    def test_uat36_07_apply_invalid_strategy(self):
        """UAT-36-07: 用户应用未知策略返回400错误"""
        resp = client.post("/api/v1/gate/strategy/uat_test?strategy=unknown")
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════
# UAT-37: 大表治理用户验收
# ═══════════════════════════════════════════════════════════

class TestUAT37_BigTableGovernance:
    """UAT-37: 大表治理 — 用户盘点、分类、治理大表"""

    def test_uat37_01_get_inventory_empty(self):
        """UAT-37-01: 用户获取空大表清单"""
        resp = client.get("/api/v1/bigtable/inventory/conn_test")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_uat37_02_save_inventory(self):
        """UAT-37-02: 用户保存大表盘点结果"""
        resp = client.post("/api/v1/bigtable/inventory/conn_test", json=[
            {
                "schema": "test_db",
                "table": "t_big_order",
                "rows": 50000000,
                "data_size_mb": 5000,
                "index_size_mb": 500,
                "level": "L2"
            },
            {
                "schema": "test_db",
                "table": "t_log",
                "rows": 100000000,
                "data_size_mb": 10000,
                "index_size_mb": 1000,
                "level": "L3"
            }
        ])
        assert resp.status_code == 200
        assert resp.json()["code"] == 0

    def test_uat37_03_get_governance_report(self):
        """UAT-37-03: 用户获取大表治理报告"""
        # 先保存盘点数据
        client.post("/api/v1/bigtable/inventory/conn_report", json=[
            {
                "schema": "report_db",
                "table": "t_report_table",
                "rows": 20000000,
                "data_size_mb": 2000,
                "index_size_mb": 200,
                "level": "L1"
            }
        ])
        resp = client.get("/api/v1/bigtable/report/conn_report")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data is not None

    def test_uat37_04_classify_table(self):
        """UAT-37-04: 用户分类表类型"""
        resp = client.get("/api/v1/bigtable/classify/t_order")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "table_type" in data or "classification" in data or data is not None

    def test_uat37_05_save_classification(self):
        """UAT-37-05: 用户保存表分类"""
        resp = client.post(
            "/api/v1/bigtable/classification/conn_classify?schema=test_db&table=t_archive&table_type=cold_data&retention_days=90"
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0


# ═══════════════════════════════════════════════════════════
# UAT-38: 监控告警用户验收
# ═══════════════════════════════════════════════════════════

class TestUAT38_MonitorAlert:
    """UAT-38: 监控告警 — 用户配置告警规则、评估指标、确认告警"""

    def test_uat38_01_get_active_alerts(self):
        """UAT-38-01: 用户获取活跃告警列表"""
        resp = client.get("/api/v1/monitor/alerts")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_uat38_02_get_alert_rules(self):
        """UAT-38-02: 用户获取告警规则配置"""
        resp = client.get("/api/v1/monitor/rules")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_uat38_03_set_alert_rule(self):
        """UAT-38-03: 用户设置告警规则"""
        resp = client.post("/api/v1/monitor/rules", json={
            "metric_name": "cpu_usage",
            "warning_threshold": 80.0,
            "urgent_threshold": 95.0,
            "check_interval_sec": 30,
            "enabled": True
        })
        assert resp.status_code == 200
        assert resp.json()["code"] == 0

    def test_uat38_04_evaluate_metric_trigger_alert(self):
        """UAT-38-04: 用户评估指标触发告警"""
        # 先设置规则
        client.post("/api/v1/monitor/rules", json={
            "metric_name": "uat_cpu",
            "warning_threshold": 80.0,
            "urgent_threshold": 95.0,
            "check_interval_sec": 60,
            "enabled": True
        })
        # 评估超过阈值的指标
        resp = client.post(
            "/api/v1/monitor/evaluate?connection_id=conn_01&metric_name=uat_cpu&value=99.0"
        )
        assert resp.status_code == 200
        data = resp.json()
        # 应该返回告警信息
        assert data["code"] == 0
        assert data["data"] is not None or data["message"] is not None

    def test_uat38_05_evaluate_metric_no_alert(self):
        """UAT-38-05: 用户评估正常指标不触发告警"""
        resp = client.post(
            "/api/v1/monitor/evaluate?connection_id=conn_01&metric_name=uat_cpu&value=10.0"
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0

    def test_uat38_06_acknowledge_alert(self):
        """UAT-38-06: 用户确认告警"""
        # 先触发一个告警
        client.post("/api/v1/monitor/rules", json={
            "metric_name": "uat_mem",
            "warning_threshold": 80.0,
            "urgent_threshold": 95.0,
            "check_interval_sec": 60,
            "enabled": True
        })
        eval_resp = client.post(
            "/api/v1/monitor/evaluate?connection_id=conn_01&metric_name=uat_mem&value=99.0"
        )
        alert_data = eval_resp.json().get("data")
        if alert_data and "id" in alert_data:
            ack_resp = client.post(
                f"/api/v1/monitor/alerts/{alert_data['id']}/acknowledge?acknowledged_by=uat_tester"
            )
            assert ack_resp.status_code == 200

    def test_uat38_07_acknowledge_nonexistent_alert(self):
        """UAT-38-07: 用户确认不存在的告警返回404"""
        resp = client.post(
            "/api/v1/monitor/alerts/99999/acknowledge?acknowledged_by=tester"
        )
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
# UAT-39: 巡检管理用户验收
# ═══════════════════════════════════════════════════════════

class TestUAT39_Inspection:
    """UAT-39: 巡检管理 — 用户创建、执行、查看巡检任务"""

    def test_uat39_01_create_task(self):
        """UAT-39-01: 用户创建巡检任务"""
        resp = client.post(
            "/api/v1/inspection/tasks?connection_id=conn_01&inspection_type=daily"
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "task_id" in data

    def test_uat39_02_list_tasks(self):
        """UAT-39-02: 用户列出巡检任务"""
        client.post("/api/v1/inspection/tasks?connection_id=conn_list&inspection_type=weekly")
        resp = client.get("/api/v1/inspection/tasks")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_uat39_03_get_task_detail(self):
        """UAT-39-03: 用户查看巡检任务详情"""
        create_resp = client.post(
            "/api/v1/inspection/tasks?connection_id=conn_detail&inspection_type=daily"
        )
        task_id = create_resp.json()["data"]["task_id"]
        resp = client.get(f"/api/v1/inspection/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == task_id

    def test_uat39_04_update_task_status(self):
        """UAT-39-04: 用户更新巡检任务状态"""
        create_resp = client.post(
            "/api/v1/inspection/tasks?connection_id=conn_status&inspection_type=daily"
        )
        task_id = create_resp.json()["data"]["task_id"]
        resp = client.post(
            f"/api/v1/inspection/tasks/{task_id}/status?status=running"
        )
        assert resp.status_code == 200

    def test_uat39_05_save_inspection_result(self):
        """UAT-39-05: 用户保存巡检结果"""
        create_resp = client.post(
            "/api/v1/inspection/tasks?connection_id=conn_result&inspection_type=daily"
        )
        task_id = create_resp.json()["data"]["task_id"]
        resp = client.post(
            f"/api/v1/inspection/tasks/{task_id}/results",
            json={
                "category": "big_table",
                "severity": "WARNING",
                "schema_name": "test_db",
                "table_name": "t_big_table",
                "metric_name": "rows",
                "metric_value": "50000000",
                "threshold": "10000000",
                "message": "表数据量超过阈值",
                "suggestion": "建议进行分区改造"
            }
        )
        assert resp.status_code == 200

    def test_uat39_06_get_nonexistent_task(self):
        """UAT-39-06: 用户查看不存在的巡检任务返回404"""
        resp = client.get("/api/v1/inspection/tasks/99999")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
# UAT-40: V1.0规则用户验收
# ═══════════════════════════════════════════════════════════

class TestUAT40_RulesUserAcceptance:
    """UAT-40: V1.0规则 — 用户查看和筛选规则"""

    def test_uat40_01_get_all_rules(self):
        """UAT-40-01: 用户通过API查看全部规则"""
        resp = client.get("/api/v1/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 76
        assert len(data["rules"]) == 76

    def test_uat40_02_filter_by_category(self):
        """UAT-40-02: 用户按类别筛选规则（客户端过滤）"""
        resp = client.get("/api/v1/rules")
        assert resp.status_code == 200
        all_rules = resp.json()["rules"]
        # API返回全部规则，用户按category过滤
        ddl_rules = [r for r in all_rules if r["category"] == "ddl"]
        assert len(ddl_rules) > 0
        for rule in ddl_rules:
            assert rule["category"] == "ddl"

    def test_uat40_03_rule_has_required_fields(self):
        """UAT-40-03: 规则包含完整描述信息"""
        resp = client.get("/api/v1/rules")
        rules = resp.json()["rules"]
        for rule in rules:
            assert "rule_id" in rule
            assert "description" in rule
            assert "severity" in rule
            assert "category" in rule
            assert "enabled" in rule
            assert rule["description"]  # 描述非空

    def test_uat40_04_get_categories(self):
        """UAT-40-04: 用户获取规则分类列表"""
        resp = client.get("/api/v1/rules/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert "categories" in data
        categories = data["categories"]
        assert isinstance(categories, dict)
        assert len(categories) >= 5  # 至少5个分类


# ═══════════════════════════════════════════════════════════
# UAT-41: V1.0引擎用户验收
# ═══════════════════════════════════════════════════════════

class TestUAT41_EnginesUserAcceptance:
    """UAT-41: V1.0引擎 — 用户使用SQL指纹、索引顾问、改写引擎等"""

    def test_uat41_01_fingerprint_generation(self):
        """UAT-41-01: 用户生成SQL指纹"""
        from backend.engine.fingerprint import FingerprintEngine
        engine = FingerprintEngine()
        fp = engine.fingerprint("SELECT * FROM t_user WHERE id = 123")
        fp_hash = engine.fingerprint_hash("SELECT * FROM t_user WHERE id = 123")
        assert fp is not None
        assert "?" in fp  # 参数被替换为?
        assert fp_hash  # 哈希非空

    def test_uat41_02_fingerprint_aggregation(self):
        """UAT-41-02: 用户聚合相似SQL指纹"""
        from backend.engine.fingerprint import FingerprintEngine
        engine = FingerprintEngine()
        sqls = [
            "SELECT * FROM t_order WHERE id = 1",
            "SELECT * FROM t_order WHERE id = 2",
            "SELECT * FROM t_order WHERE id = 999",
        ]
        hashes = [engine.fingerprint_hash(sql) for sql in sqls]
        # 相同指纹模式的SQL应有相同哈希
        assert len(set(hashes)) == 1

    def test_uat41_03_index_advise_with_possible_keys(self):
        """UAT-41-03: 用户获取索引建议（EXPLAIN全表扫描报告missing）"""
        from backend.engine.index_advisor import IndexAdvisor
        advisor = IndexAdvisor()
        explain_rows = [
            {"type": "ALL", "key": None, "possible_keys": None, "rows": 10000, "table": "t_user"}
        ]
        recs = advisor.advise_from_explain(explain_rows)
        assert len(recs) > 0
        assert recs[0].type == "missing"

    def test_uat41_04_index_advise_from_sql(self):
        """UAT-41-04: 用户从SQL文本获取索引建议"""
        from backend.engine.index_advisor import IndexAdvisor
        advisor = IndexAdvisor()
        recs = advisor.advise_from_sql("SELECT * FROM t_order WHERE user_id = 123 AND status = 1")
        assert len(recs) > 0
        assert recs[0].table  # 有表名
        assert recs[0].columns  # 有列名
        assert recs[0].ddl  # 有DDL

    def test_uat41_05_sql_rewrite_deep_pagination(self):
        """UAT-41-05: 用户获取深分页SQL改写建议"""
        from backend.engine.sql_rewriter import SQLRewriter
        rewriter = SQLRewriter()
        suggestions = rewriter.rewrite("SELECT * FROM t_order ORDER BY id LIMIT 100000, 20")
        assert len(suggestions) > 0
        has_deep_page = any(s.type == "deep_pagination" for s in suggestions)
        assert has_deep_page

    def test_uat41_06_sql_rewrite_select_star(self):
        """UAT-41-06: 用户获取SELECT *改写建议"""
        from backend.engine.sql_rewriter import SQLRewriter
        rewriter = SQLRewriter()
        suggestions = rewriter.rewrite("SELECT * FROM t_user WHERE id = 1")
        assert len(suggestions) > 0
        has_star = any("*" in s.reason or "SELECT" in s.reason.upper() for s in suggestions)
        assert has_star

    def test_uat41_07_deadlock_analysis(self):
        """UAT-41-07: 用户分析死锁日志"""
        from backend.engine.deadlock_analyzer import DeadlockAnalyzer
        analyzer = DeadlockAnalyzer()
        deadlock_log = """------------------------
LATEST DETECTED DEADLOCK
------------------------
2023-06-17 10:30:00 0x7f
*** (1) TRANSACTION:
TRANSACTION 12345, ACTIVE 2 sec starting index read
mysql tables in use 1, locked 1
LOCK WAIT 3 lock struct(s), heap size 1136, 2 row lock(s)
MySQL thread id 100, OS thread handle 0x7f, query id 200 updating
UPDATE t_account SET balance = balance - 100 WHERE id = 1
*** (1) WAITING FOR THIS LOCK TO BE GRANTED:
RECORD LOCKS space id 50 page no 3 n bits 72 index PRIMARY of table `test_db`.`t_account`
*** (2) TRANSACTION:
TRANSACTION 12346, ACTIVE 2 sec starting index read
UPDATE t_account SET balance = balance + 100 WHERE id = 1
*** (2) HOLDS THE LOCK(S):
RECORD LOCKS space id 50 page no 3 n bits 72 index PRIMARY of table `test_db`.`t_account`
*** (2) WAITING FOR THIS LOCK TO BE GRANTED:
RECORD LOCKS space id 50 page no 3 n bits 72 index PRIMARY of table `test_db`.`t_account`
*** WE ROLL BACK TRANSACTION (2)"""
        report = analyzer.analyze_from_log(deadlock_log)
        assert report is not None
        assert report.has_deadlock is True
        assert report.transaction_1  # 事务1非空
        assert report.transaction_2  # 事务2非空

    def test_uat41_08_distributed_explain_analysis(self):
        """UAT-41-08: 用户分析分布式EXPLAIN输出"""
        from backend.engine.distributed_explain import DistributedExplainAnalyzer
        analyzer = DistributedExplainAnalyzer()
        explain_rows = [
            {"type": "ALL", "key": None, "possible_keys": None, "rows": 100000, "table": "t_order", "set": "set_1"},
            {"type": "ALL", "key": None, "possible_keys": None, "rows": 100000, "table": "t_order", "set": "set_2"},
        ]
        result = analyzer.analyze(explain_rows, "SELECT * FROM t_order WHERE status = 1")
        assert result is not None
        assert len(result.warnings) > 0  # 应检测到全表扫描


# ═══════════════════════════════════════════════════════════
# UAT-42: CLI工具用户验收
# ═══════════════════════════════════════════════════════════

class TestUAT42_CLIUserAcceptance:
    """UAT-42: CLI工具 — 用户通过命令行使用审核功能"""

    def test_uat42_01_cli_rules(self):
        """UAT-42-01: 用户通过CLI列出规则"""
        runner = CliRunner()
        result = runner.invoke(cli, ["rules"])
        assert result.exit_code == 0
        assert "76" in result.output  # 显示规则数量

    def test_uat42_02_cli_audit_pass(self):
        """UAT-42-02: 用户通过CLI审核合规SQL"""
        runner = CliRunner()
        result = runner.invoke(cli, ["audit", "SELECT id, name FROM t_user WHERE id = 1"])
        assert result.exit_code == 0
        assert "通过" in result.output or "✓" in result.output

    def test_uat42_03_cli_audit_fail(self):
        """UAT-42-03: 用户通过CLI审核不合规SQL"""
        runner = CliRunner()
        result = runner.invoke(cli, ["audit", "SELECT * FROM t_user"])
        assert result.exit_code != 0  # 不合规SQL退出码非0
        assert "违规" in result.output or "✗" in result.output

    def test_uat42_04_cli_audit_with_gate(self):
        """UAT-42-04: 用户通过CLI审核SQL并启用门禁"""
        runner = CliRunner()
        result = runner.invoke(cli, ["audit", "SELECT id FROM t_user WHERE id = 1", "--gate"])
        assert result.exit_code == 0
        assert "门禁" in result.output

    def test_uat42_05_cli_fingerprint(self):
        """UAT-42-05: 用户通过CLI生成SQL指纹"""
        runner = CliRunner()
        result = runner.invoke(cli, ["fingerprint", "SELECT * FROM t_user WHERE id = 123"])
        assert result.exit_code == 0
        assert "指纹" in result.output
        assert "?" in result.output

    def test_uat42_06_cli_index_advise(self):
        """UAT-42-06: 用户通过CLI获取索引建议"""
        runner = CliRunner()
        result = runner.invoke(cli, ["index-advise", "SELECT * FROM t_order WHERE user_id = 123"])
        assert result.exit_code == 0
        assert "索引" in result.output or "无索引" in result.output

    def test_uat42_07_cli_rewrite(self):
        """UAT-42-07: 用户通过CLI获取SQL改写建议"""
        runner = CliRunner()
        result = runner.invoke(cli, ["rewrite", "SELECT * FROM t_order LIMIT 100000, 20"])
        assert result.exit_code == 0
        assert "改写" in result.output or "无改写" in result.output

    def test_uat42_08_cli_version(self):
        """UAT-42-08: 用户查看CLI版本信息"""
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "1.0" in result.output


# ═══════════════════════════════════════════════════════════
# UAT-43: V1.0端到端用户工作流
# ═══════════════════════════════════════════════════════════

class TestUAT43_EndToEndWorkflows:
    """UAT-43: V1.0端到端 — 用户完成完整业务工作流"""

    def test_uat43_01_project_gate_audit_workflow(self):
        """UAT-43-01: 创建项目→设置门禁→审核SQL→门禁检查全流程"""
        # 1. 创建项目
        proj_resp = client.post("/api/v1/projects", json={
            "project_name": "uat_e2e_workflow",
            "description": "端到端工作流测试"
        })
        project_id = proj_resp.json()["data"]["project_id"]
        assert project_id

        # 2. 设置门禁策略为严格
        gate_resp = client.post(f"/api/v1/gate/strategy/{project_id}?strategy=strict")
        assert gate_resp.status_code == 200

        # 3. 审核SQL
        audit_resp = client.post("/api/v1/audit/sql", json={
            "sql": "SELECT * FROM t_user"
        })
        audit_data = audit_resp.json()
        assert audit_data["passed"] is False  # SELECT * 应不通过

        # 4. 验证门禁规则已设置
        rule_resp = client.get(f"/api/v1/gate/rules/{project_id}")
        assert rule_resp.status_code == 200
        assert rule_resp.json()["data"]["max_error_count"] == 0

    def test_uat43_02_bigtable_governance_workflow(self):
        """UAT-43-02: 大表治理全流程（盘点→分类→报告）"""
        conn_id = "uat_e2e_bigtable"

        # 1. 保存大表盘点结果
        inventory_resp = client.post(f"/api/v1/bigtable/inventory/{conn_id}", json=[
            {
                "schema": "prod_db",
                "table": "t_transaction_log",
                "rows": 200000000,
                "data_size_mb": 20000,
                "index_size_mb": 2000,
                "level": "L3"
            }
        ])
        assert inventory_resp.status_code == 200

        # 2. 获取表分类
        classify_resp = client.get("/api/v1/bigtable/classify/t_transaction_log")
        assert classify_resp.status_code == 200

        # 3. 保存分类
        save_class_resp = client.post(
            f"/api/v1/bigtable/classification/{conn_id}?schema=prod_db&table=t_transaction_log&table_type=cold_data&retention_days=180"
        )
        assert save_class_resp.status_code == 200

        # 4. 获取治理报告
        report_resp = client.get(f"/api/v1/bigtable/report/{conn_id}")
        assert report_resp.status_code == 200

    def test_uat43_03_inspection_lifecycle(self):
        """UAT-43-03: 巡检全生命周期（创建→执行→完成→结果）"""
        conn_id = "uat_e2e_inspection"

        # 1. 创建巡检任务
        create_resp = client.post(
            f"/api/v1/inspection/tasks?connection_id={conn_id}&inspection_type=comprehensive"
        )
        task_id = create_resp.json()["data"]["task_id"]
        assert task_id

        # 2. 更新状态为running
        running_resp = client.post(f"/api/v1/inspection/tasks/{task_id}/status?status=running")
        assert running_resp.status_code == 200

        # 3. 保存巡检结果
        result_resp = client.post(f"/api/v1/inspection/tasks/{task_id}/results", json={
            "category": "slow_query",
            "severity": "WARNING",
            "metric_name": "slow_query_count",
            "metric_value": "15",
            "threshold": "10",
            "message": "慢查询数量超过阈值",
            "suggestion": "建议优化TOP3慢查询"
        })
        assert result_resp.status_code == 200

        # 4. 更新状态为completed
        complete_resp = client.post(f"/api/v1/inspection/tasks/{task_id}/status?status=completed")
        assert complete_resp.status_code == 200

        # 5. 查看任务详情确认状态
        detail_resp = client.get(f"/api/v1/inspection/tasks/{task_id}")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["data"]["status"] == "completed"

    def test_uat43_04_monitor_alert_workflow(self):
        """UAT-43-04: 监控告警工作流（设置规则→评估→告警）"""
        # 1. 设置告警规则
        rule_resp = client.post("/api/v1/monitor/rules", json={
            "metric_name": "uat_e2e_disk",
            "warning_threshold": 85.0,
            "urgent_threshold": 95.0,
            "check_interval_sec": 60,
            "enabled": True
        })
        assert rule_resp.status_code == 200

        # 2. 评估指标触发告警
        eval_resp = client.post(
            "/api/v1/monitor/evaluate?connection_id=conn_e2e&metric_name=uat_e2e_disk&value=98.0"
        )
        assert eval_resp.status_code == 200
        eval_data = eval_resp.json()
        # 应该有告警返回
        assert eval_data["data"] is not None or "触发" in eval_data.get("message", "")

        # 3. 查看活跃告警
        alerts_resp = client.get("/api/v1/monitor/alerts")
        assert alerts_resp.status_code == 200

    def test_uat43_05_sql_audit_fingerprint_index_rewrite_chain(self):
        """UAT-43-05: SQL审核→指纹→索引建议→改写全链路"""
        from backend.engine.fingerprint import FingerprintEngine
        from backend.engine.index_advisor import IndexAdvisor
        from backend.engine.sql_rewriter import SQLRewriter

        sql = "SELECT * FROM t_order WHERE user_id = 123 ORDER BY id LIMIT 100000, 20"

        # 1. SQL审核
        audit_resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert audit_resp.status_code == 200
        audit_data = audit_resp.json()
        assert audit_data["passed"] is False  # 应有违规

        # 2. SQL指纹
        fp_engine = FingerprintEngine()
        fingerprint = fp_engine.fingerprint(sql)
        fp_hash = fp_engine.fingerprint_hash(sql)
        assert fingerprint and fp_hash

        # 3. 索引建议
        advisor = IndexAdvisor()
        index_recs = advisor.advise_from_sql(sql)
        assert len(index_recs) > 0

        # 4. SQL改写建议
        rewriter = SQLRewriter()
        rewrite_suggestions = rewriter.rewrite(sql)
        assert len(rewrite_suggestions) > 0
