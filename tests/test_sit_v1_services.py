"""
TDSQL SQL审核工具 V1.0 - SIT测试 第三部分：服务层+API+集成场景

覆盖6个新服务CRUD、5个新API路由、CLI工具、端到端集成场景。
"""
import json
import pytest
from fastapi.testclient import TestClient
from click.testing import CliRunner

from backend.main import app
from backend.cli import cli

client = TestClient(app)


# ═══════════════════════════════════════════════════════════
# 一、项目管理服务+API
# ═══════════════════════════════════════════════════════════

class TestProjectService:
    """项目管理服务测试"""

    def setup_method(self):
        """每个测试前清理已有测试数据"""
        from backend.services.database import _get_connection, ensure_db
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute("DELETE FROM projects WHERE project_id IN ('sit测试项目', '获取测试', '删除测试')")
            conn.commit()
        finally:
            conn.close()

    def test_create_project(self):
        """创建项目"""
        resp = client.post("/api/v1/projects", json={
            "project_name": "SIT测试项目",
            "description": "SIT测试用",
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["project_name"] == "SIT测试项目"
        assert data["status"] == "active"
        assert data["project_id"] == "sit测试项目"

    def test_list_projects(self):
        """列出项目"""
        resp = client.get("/api/v1/projects")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)

    def test_get_project(self):
        """获取项目详情"""
        # 先创建
        resp = client.post("/api/v1/projects", json={"project_name": "获取测试"})
        pid = resp.json()["data"]["project_id"]
        # 再获取
        resp = client.get(f"/api/v1/projects/{pid}")
        assert resp.status_code == 200
        assert resp.json()["data"]["project_id"] == pid

    def test_get_nonexistent_project(self):
        """获取不存在的项目返回404"""
        resp = client.get("/api/v1/projects/nonexistent_project_xyz")
        assert resp.status_code == 404

    def test_delete_project(self):
        """删除项目（物理删除）"""
        resp = client.post("/api/v1/projects", json={"project_name": "删除测试"})
        pid = resp.json()["data"]["project_id"]
        resp = client.delete(f"/api/v1/projects/{pid}")
        assert resp.status_code == 200
        # 验证已物理删除：再次 GET 应 404
        resp = client.get(f"/api/v1/projects/{pid}")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════
# 二、质量门禁服务+API
# ═══════════════════════════════════════════════════════════

class TestGateService:
    """质量门禁服务测试"""

    def test_get_default_gate_rule(self):
        """获取默认门禁规则"""
        resp = client.get("/api/v1/gate/rules/default")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["project_id"] == "default"
        assert data["max_error_count"] == 0

    def test_set_gate_rule(self):
        """设置门禁规则"""
        resp = client.post("/api/v1/gate/rules", json={
            "project_id": "sit_gate_test",
            "max_error_count": 5,
            "max_warning_count": 10,
            "description": "SIT测试门禁规则",
        })
        assert resp.status_code == 200
        # 验证已设置
        resp = client.get("/api/v1/gate/rules/sit_gate_test")
        assert resp.json()["data"]["max_error_count"] == 5

    def test_apply_strict_strategy(self):
        """应用strict策略"""
        resp = client.post("/api/v1/gate/strategy/sit_strict?strategy=strict")
        assert resp.status_code == 200
        rule = client.get("/api/v1/gate/rules/sit_strict").json()["data"]
        assert rule["max_error_count"] == 0
        assert rule["max_warning_count"] == 0

    def test_apply_normal_strategy(self):
        """应用normal策略"""
        resp = client.post("/api/v1/gate/strategy/sit_normal?strategy=normal")
        assert resp.status_code == 200
        rule = client.get("/api/v1/gate/rules/sit_normal").json()["data"]
        assert rule["max_error_count"] == 0
        assert rule["max_warning_count"] == -1

    def test_apply_loose_strategy(self):
        """应用loose策略"""
        resp = client.post("/api/v1/gate/strategy/sit_loose?strategy=loose")
        assert resp.status_code == 200
        rule = client.get("/api/v1/gate/rules/sit_loose").json()["data"]
        assert rule["max_error_count"] == -1
        assert rule["max_warning_count"] == -1

    def test_apply_invalid_strategy(self):
        """无效策略返回400"""
        resp = client.post("/api/v1/gate/strategy/sit_invalid?strategy=invalid")
        assert resp.status_code == 400

    def test_list_strategies(self):
        """列出可用策略"""
        resp = client.get("/api/v1/gate/strategies")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "strict" in data
        assert "normal" in data
        assert "loose" in data

    def test_gate_evaluation_with_violations(self):
        """门禁评估：有ERROR违规时strict策略阻断"""
        from backend.services.gate_service import GateService
        from backend.models import Violation, Severity, RuleCategory
        svc = GateService()
        svc.apply_strategy("sit_eval", "strict")
        violations = [
            Violation(rule_id="R001", category=RuleCategory.NAMING, severity=Severity.ERROR, message="test"),
        ]
        result = svc.evaluate(violations, svc.get_gate_rule("sit_eval"))
        assert result.passed is False
        assert result.error_count == 1

    def test_gate_evaluation_no_violations(self):
        """门禁评估：无违规时通过"""
        from backend.services.gate_service import GateService
        svc = GateService()
        svc.apply_strategy("sit_pass", "strict")
        result = svc.evaluate([], svc.get_gate_rule("sit_pass"))
        assert result.passed is True


# ═══════════════════════════════════════════════════════════
# 三、大表治理服务+API
# ═══════════════════════════════════════════════════════════

class TestBigTableService:
    """大表治理服务测试"""

    def test_save_inventory(self):
        """保存大表盘点结果"""
        tables_info = [
            {"schema": "test_db", "table": "t_order_log", "size_gb": 80, "rows": 90000000, "is_partitioned": False},
            {"schema": "test_db", "table": "t_config", "size_gb": 0.1, "rows": 100, "is_partitioned": False},
        ]
        resp = client.post("/api/v1/bigtable/inventory/sit_conn", json=tables_info)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_big_tables"] >= 1

    def test_get_inventory(self):
        """获取大表清单"""
        # 先保存
        tables_info = [{"schema": "db1", "table": "t_big", "size_gb": 100, "rows": 80000000}]
        client.post("/api/v1/bigtable/inventory/sit_conn2", json=tables_info)
        # 再获取
        resp = client.get("/api/v1/bigtable/inventory/sit_conn2")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) >= 1

    def test_get_inventory_with_level_filter(self):
        """按级别过滤大表清单"""
        tables_info = [
            {"schema": "db1", "table": "t_l1", "size_gb": 60, "rows": 60000000},
            {"schema": "db1", "table": "t_l3", "size_gb": 600, "rows": 800000000},
        ]
        client.post("/api/v1/bigtable/inventory/sit_conn3", json=tables_info)
        resp = client.get("/api/v1/bigtable/inventory/sit_conn3?level=L3")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert all(t["level"] == "L3" for t in data)

    def test_get_governance_report(self):
        """获取治理报告"""
        tables_info = [{"schema": "db1", "table": "t_report", "size_gb": 100, "rows": 80000000}]
        client.post("/api/v1/bigtable/inventory/sit_conn4", json=tables_info)
        resp = client.get("/api/v1/bigtable/report/sit_conn4")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "total_big_tables" in data
        assert "by_level" in data

    def test_classify_table(self):
        """分类表类型"""
        resp = client.get("/api/v1/bigtable/classify/t_transaction_log")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["table_type"] == "transaction_log"
        assert data["retention_days"] == 365

    def test_save_classification(self):
        """保存表分类"""
        resp = client.post(
            "/api/v1/bigtable/classification/sit_conn5?schema=db1&table=t_audit_log&table_type=audit_log&retention_days=1095"
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════
# 四、监控告警服务+API
# ═══════════════════════════════════════════════════════════

class TestMonitorService:
    """监控告警服务测试"""

    def test_get_alert_rules(self):
        """获取告警规则"""
        resp = client.get("/api/v1/monitor/rules")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) >= 4  # 4条默认规则

    def test_set_alert_rule(self):
        """设置告警规则"""
        resp = client.post("/api/v1/monitor/rules", json={
            "metric_name": "test_metric",
            "warning_threshold": 50,
            "urgent_threshold": 100,
            "check_interval_sec": 30,
            "enabled": True,
        })
        assert resp.status_code == 200
        # 验证已设置
        rules = client.get("/api/v1/monitor/rules").json()["data"]
        assert any(r["metric_name"] == "test_metric" for r in rules)

    def test_evaluate_metric_warning(self):
        """评估指标触发WARNING"""
        # 先确保规则存在
        client.post("/api/v1/monitor/rules", json={
            "metric_name": "sit_test_metric",
            "warning_threshold": 50,
            "urgent_threshold": 100,
            "enabled": True,
        })
        resp = client.post("/api/v1/monitor/evaluate?connection_id=sit_conn&metric_name=sit_test_metric&value=75")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("data") is not None
        assert data["data"]["level"] == "WARNING"

    def test_evaluate_metric_critical(self):
        """评估指标触发CRITICAL"""
        client.post("/api/v1/monitor/rules", json={
            "metric_name": "sit_critical_metric",
            "warning_threshold": 50,
            "urgent_threshold": 100,
            "enabled": True,
        })
        resp = client.post("/api/v1/monitor/evaluate?connection_id=sit_conn&metric_name=sit_critical_metric&value=150")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["level"] == "CRITICAL"

    def test_evaluate_metric_normal(self):
        """评估指标正常"""
        client.post("/api/v1/monitor/rules", json={
            "metric_name": "sit_normal_metric",
            "warning_threshold": 50,
            "urgent_threshold": 100,
            "enabled": True,
        })
        resp = client.post("/api/v1/monitor/evaluate?connection_id=sit_conn&metric_name=sit_normal_metric&value=10")
        assert resp.status_code == 200
        assert "正常" in resp.json()["message"]

    def test_get_active_alerts(self):
        """获取活跃告警"""
        resp = client.get("/api/v1/monitor/alerts")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)


# ═══════════════════════════════════════════════════════════
# 五、巡检服务+API
# ═══════════════════════════════════════════════════════════

class TestInspectionService:
    """巡检服务测试"""

    def test_create_task(self):
        """创建巡检任务"""
        resp = client.post("/api/v1/inspection/tasks?connection_id=sit_conn&inspection_type=charset_check")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "task_id" in data
        assert isinstance(data["task_id"], int)

    def test_list_tasks(self):
        """列出巡检任务"""
        # 先创建
        client.post("/api/v1/inspection/tasks?connection_id=sit_conn&inspection_type=bigtable_check")
        resp = client.get("/api/v1/inspection/tasks")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_get_task(self):
        """获取巡检任务详情"""
        create_resp = client.post("/api/v1/inspection/tasks?connection_id=sit_conn&inspection_type=index_check")
        task_id = create_resp.json()["data"]["task_id"]
        resp = client.get(f"/api/v1/inspection/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == task_id
        assert data["connection_id"] == "sit_conn"

    def test_get_nonexistent_task(self):
        """获取不存在的任务返回404"""
        resp = client.get("/api/v1/inspection/tasks/99999")
        assert resp.status_code == 404

    def test_update_task_status(self):
        """更新任务状态"""
        create_resp = client.post("/api/v1/inspection/tasks?connection_id=sit_conn&inspection_type=test")
        task_id = create_resp.json()["data"]["task_id"]
        resp = client.post(f"/api/v1/inspection/tasks/{task_id}/status?status=running")
        assert resp.status_code == 200
        # 验证状态已更新
        task = client.get(f"/api/v1/inspection/tasks/{task_id}").json()["data"]
        assert task["status"] == "running"
        assert task["started_at"] is not None

    def test_complete_task_with_error(self):
        """完成任务（带错误信息）"""
        create_resp = client.post("/api/v1/inspection/tasks?connection_id=sit_conn&inspection_type=test")
        task_id = create_resp.json()["data"]["task_id"]
        resp = client.post(f"/api/v1/inspection/tasks/{task_id}/status?status=failed&error_message=连接超时")
        assert resp.status_code == 200
        task = client.get(f"/api/v1/inspection/tasks/{task_id}").json()["data"]
        assert task["status"] == "failed"
        assert task["error_message"] == "连接超时"

    def test_save_inspection_result(self):
        """保存巡检结果"""
        create_resp = client.post("/api/v1/inspection/tasks?connection_id=sit_conn&inspection_type=charset_check")
        task_id = create_resp.json()["data"]["task_id"]
        resp = client.post(f"/api/v1/inspection/tasks/{task_id}/results", json={
            "category": "charset",
            "severity": "WARNING",
            "schema_name": "test_db",
            "table_name": "t_user",
            "metric_name": "table_collation",
            "metric_value": "latin1_swedish_ci",
            "threshold": "utf8mb4_general_ci",
            "message": "表字符集不符合规范",
            "suggestion": "ALTER TABLE t_user CONVERT TO CHARACTER SET utf8mb4",
        })
        assert resp.status_code == 200
        # 验证结果已保存
        task = client.get(f"/api/v1/inspection/tasks/{task_id}").json()["data"]
        assert len(task["results"]) >= 1
        assert task["results"][0]["category"] == "charset"


# ═══════════════════════════════════════════════════════════
# 六、安全服务测试
# ═══════════════════════════════════════════════════════════

class TestSecurityService:
    """安全服务测试"""

    def test_encrypt_decrypt_roundtrip(self):
        """加密解密往返"""
        from backend.services.security_service import encrypt_password, decrypt_password
        password = "my_secret_password_123!@#"
        encrypted = encrypt_password(password)
        assert encrypted != password
        decrypted = decrypt_password(encrypted)
        assert decrypted == password

    def test_encrypt_empty_password(self):
        """空密码加密"""
        from backend.services.security_service import encrypt_password, decrypt_password
        assert encrypt_password("") == ""
        assert decrypt_password("") == ""

    def test_mask_password(self):
        """密码脱敏"""
        from backend.services.security_service import SecurityService
        assert SecurityService.mask_password("password123") == "p*********3"
        assert SecurityService.mask_password("ab") == "a*"
        assert SecurityService.mask_password("a") == "*"
        assert SecurityService.mask_password("") == ""

    def test_different_passwords_different_encrypted(self):
        """不同密码产生不同加密结果"""
        from backend.services.security_service import encrypt_password
        enc1 = encrypt_password("password1")
        enc2 = encrypt_password("password2")
        assert enc1 != enc2


# ═══════════════════════════════════════════════════════════
# 七、CLI工具测试
# ═══════════════════════════════════════════════════════════

class TestCLIV1:
    """CLI工具V1.0测试"""

    def setup_method(self):
        self.runner = CliRunner()

    def test_cli_rules(self):
        """CLI rules命令"""
        result = self.runner.invoke(cli, ["rules"])
        assert result.exit_code == 0
        assert "76" in result.output

    def test_cli_audit_pass(self):
        """CLI audit命令 - 通过的SQL"""
        sql = "SELECT id, name FROM t_user WHERE id = 1"
        result = self.runner.invoke(cli, ["audit", sql])
        assert result.exit_code == 0

    def test_cli_audit_fail(self):
        """CLI audit命令 - 违规SQL"""
        result = self.runner.invoke(cli, ["audit", "SELECT * FROM t_user WHERE id = 1"])
        assert result.exit_code == 1

    def test_cli_audit_with_gate(self):
        """CLI audit命令 - 带门禁检查"""
        result = self.runner.invoke(cli, ["audit", "SELECT * FROM t_user WHERE id = 1", "--gate"])
        # 有违规+门禁检查，应exit 1
        assert result.exit_code == 1

    def test_cli_fingerprint(self):
        """CLI fingerprint命令"""
        result = self.runner.invoke(cli, ["fingerprint", "SELECT * FROM t WHERE id = 1"])
        assert result.exit_code == 0
        assert "指纹" in result.output

    def test_cli_index_advise(self):
        """CLI index-advise命令"""
        result = self.runner.invoke(cli, ["index-advise", "SELECT * FROM t_order WHERE cust_id = 1"])
        assert result.exit_code == 0

    def test_cli_rewrite(self):
        """CLI rewrite命令"""
        result = self.runner.invoke(cli, ["rewrite", "SELECT * FROM t_order LIMIT 50000, 20"])
        assert result.exit_code == 0
        assert "deep_pagination" in result.output or "分页" in result.output

    def test_cli_gate(self):
        """CLI gate命令"""
        result = self.runner.invoke(cli, ["gate", "sit_cli_project", "strict"])
        assert result.exit_code == 0
        assert "strict" in result.output

    def test_cli_version(self):
        """CLI版本"""
        result = self.runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "2.0.0" in result.output


# ═══════════════════════════════════════════════════════════
# 八、端到端集成场景
# ═══════════════════════════════════════════════════════════

class TestEndToEndScenarios:
    """端到端集成场景测试"""

    def test_scenario_audit_to_gate_workflow(self):
        """场景：SQL审核 → 质量门禁评估 → 结果验证"""
        # 1. 设置strict门禁策略
        client.post("/api/v1/gate/strategy/e2e_project?strategy=strict")

        # 2. 审核违规SQL
        audit_resp = client.post("/api/v1/audit/sql", json={
            "sql": "SELECT * FROM t_user WHERE id = 1",
        })
        assert audit_resp.status_code == 200
        audit_data = audit_resp.json()
        violations = audit_data.get("violations", [])
        assert len(violations) > 0

        # 3. 门禁评估
        from backend.services.gate_service import GateService
        from backend.models import Violation, Severity, RuleCategory
        gate_svc = GateService()
        gate_rule = gate_svc.get_gate_rule("e2e_project")
        vlist = [Violation(**v) for v in violations]
        gate_result = gate_svc.evaluate(vlist, gate_rule)
        assert gate_result.passed is False
        assert gate_result.error_count >= 1

    def test_scenario_audit_pass_clean_sql(self):
        """场景：规范SQL审核通过 → 门禁通过"""
        # 设置normal策略
        client.post("/api/v1/gate/strategy/e2e_clean?strategy=normal")

        sql = """SELECT id, name, email FROM t_user WHERE id = 1"""
        audit_resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        audit_data = audit_resp.json()
        violations = audit_data.get("violations", [])

        from backend.services.gate_service import GateService
        from backend.models import Violation
        gate_svc = GateService()
        gate_rule = gate_svc.get_gate_rule("e2e_clean")
        vlist = [Violation(**v) for v in violations]
        gate_result = gate_svc.evaluate(vlist, gate_rule)

        # 无ERROR违规应通过normal门禁
        error_count = sum(1 for v in violations if v.get("severity") == "ERROR")
        if error_count == 0:
            assert gate_result.passed is True

    def test_scenario_bigtable_governance_full(self):
        """场景：大表盘点 → 分类 → 治理报告 → 分区建议"""
        # 1. 盘点
        tables_info = [
            {"schema": "e2e_db", "table": "t_transaction_log", "size_gb": 120, "rows": 100000000, "is_partitioned": False},
            {"schema": "e2e_db", "table": "t_audit_log", "size_gb": 550, "rows": 900000000, "is_partitioned": False},
        ]
        save_resp = client.post("/api/v1/bigtable/inventory/e2e_conn", json=tables_info)
        report_data = save_resp.json()["data"]
        assert report_data["total_big_tables"] == 2
        assert report_data["by_level"]["L1"] == 1
        assert report_data["by_level"]["L3"] == 1

        # 2. 分类
        classify_resp = client.get("/api/v1/bigtable/classify/t_transaction_log")
        classification = classify_resp.json()["data"]
        assert classification["table_type"] == "transaction_log"

        # 3. 保存分类
        client.post("/api/v1/bigtable/classification/e2e_conn?schema=e2e_db&table=t_transaction_log&table_type=transaction_log&retention_days=365")

        # 4. 获取治理报告
        report_resp = client.get("/api/v1/bigtable/report/e2e_conn")
        final_report = report_resp.json()["data"]
        assert final_report["total_big_tables"] >= 2
        assert len(final_report["unpartitioned"]) >= 2

    def test_scenario_inspection_full_lifecycle(self):
        """场景：巡检任务完整生命周期"""
        # 1. 创建任务
        create_resp = client.post("/api/v1/inspection/tasks?connection_id=e2e_conn&inspection_type=full_check")
        task_id = create_resp.json()["data"]["task_id"]

        # 2. 更新为running
        client.post(f"/api/v1/inspection/tasks/{task_id}/status?status=running")

        # 3. 保存巡检结果
        client.post(f"/api/v1/inspection/tasks/{task_id}/results", json={
            "category": "charset", "severity": "WARNING",
            "schema_name": "e2e_db", "table_name": "t_user",
            "message": "字符集不匹配", "suggestion": "改为utf8mb4",
        })
        client.post(f"/api/v1/inspection/tasks/{task_id}/results", json={
            "category": "bigtable", "severity": "CRITICAL",
            "schema_name": "e2e_db", "table_name": "t_audit_log",
            "message": "L3级大表未分区", "suggestion": "添加分区",
        })

        # 4. 完成任务
        client.post(f"/api/v1/inspection/tasks/{task_id}/status?status=completed")

        # 5. 验证结果
        task = client.get(f"/api/v1/inspection/tasks/{task_id}").json()["data"]
        assert task["status"] == "completed"
        assert task["completed_at"] is not None
        assert len(task["results"]) == 2

    def test_scenario_monitor_alert_workflow(self):
        """场景：设置告警规则 → 指标评估 → 触发告警 → 确认告警"""
        # 1. 设置告警规则
        client.post("/api/v1/monitor/rules", json={
            "metric_name": "e2e_threads_running",
            "warning_threshold": 100,
            "urgent_threshold": 200,
            "enabled": True,
        })

        # 2. 评估指标触发CRITICAL
        eval_resp = client.post("/api/v1/monitor/evaluate?connection_id=e2e_conn&metric_name=e2e_threads_running&value=250")
        eval_data = eval_resp.json()
        assert eval_data.get("data") is not None
        assert eval_data["data"]["level"] == "CRITICAL"

        # 3. 创建告警记录
        from backend.services.monitor_service import MonitorService
        from backend.models import AlertInfo
        mon_svc = MonitorService()
        alert_id = mon_svc.create_alert(AlertInfo(
            metric="e2e_threads_running", value=250, level="CRITICAL",
            connection_id="e2e_conn", message="threads_running=250 超过CRITICAL阈值",
        ))
        assert alert_id > 0

        # 4. 确认告警
        ack_resp = client.post(f"/api/v1/monitor/alerts/{alert_id}/acknowledge?acknowledged_by=admin")
        assert ack_resp.status_code == 200

    def test_scenario_sql_fingerprint_aggregation(self):
        """场景：SQL指纹归并 → 相同指纹不同参数SQL聚合"""
        from backend.engine.fingerprint import FingerprintEngine
        fp_engine = FingerprintEngine()

        # 三条参数不同但结构相同的SQL
        sqls = [
            "SELECT * FROM t_order WHERE cust_id = 100 AND status = 1",
            "SELECT * FROM t_order WHERE cust_id = 200 AND status = 2",
            "SELECT * FROM t_order WHERE cust_id = 300 AND status = 3",
        ]
        fingerprints = set(fp_engine.fingerprint_hash(s) for s in sqls)
        # 三条SQL应归并为同一指纹
        assert len(fingerprints) == 1

    def test_scenario_index_advisor_with_audit(self):
        """场景：审核SQL → 索引推荐 → 验证建议"""
        from backend.engine.index_advisor import IndexAdvisor

        sql = "SELECT * FROM t_order WHERE cust_id = 100 AND status = 0 AND create_time > '2024-01-01'"
        advisor = IndexAdvisor()
        recs = advisor.advise_from_sql(sql)

        # 应推荐复合索引
        assert len(recs) >= 1
        rec = recs[0]
        assert rec.type == "composite"
        assert "cust_id" in rec.columns
        assert "status" in rec.columns
        # 等值条件应在范围条件前面
        assert rec.columns.index("cust_id") < rec.columns.index("create_time")

    def test_scenario_sql_rewrite_suggestions(self):
        """场景：SQL改写建议全量验证"""
        from backend.engine.sql_rewriter import SQLRewriter

        rewriter = SQLRewriter()
        sql = "SELECT * FROM t_order WHERE cust_id IN (SELECT id FROM t_customer WHERE status = 1) OR amount > 1000 ORDER BY id LIMIT 50000, 20"

        suggestions = rewriter.rewrite(sql)
        types = [s.type for s in suggestions]

        # 应同时触发多种改写建议
        assert "select_star" in types
        assert "deep_pagination" in types
        assert "or_to_union" in types
        assert "subquery_to_join" in types
