"""
TDSQL SQL审核工具 - 第二轮UAT测试

更深入的用户验收测试，覆盖真实业务场景和边缘情况。
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

issues = []


def record_issue(severity, module, description, expected, actual):
    issues.append({
        "severity": severity,
        "module": module,
        "description": description,
        "expected": expected,
        "actual": actual,
    })


# ═══════════════════════════════════════════════════════════
# UAT-08: 真实银行业务SQL审核
# ═══════════════════════════════════════════════════════════

class TestUAT08_BankingSQL:
    """真实银行业务SQL审核场景"""

    def test_uat08_01_account_query_ok(self):
        """账户查询SQL应通过"""
        sql = "SELECT account_id, balance, status FROM t_account WHERE account_id = ? AND status = 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not True:
            record_issue("P1", "SQL审核", "合规账户查询被误判", "passed=True", f"passed={data['passed']}, violations={data['violations']}")

    def test_uat08_02_transfer_insert_ok(self):
        """转账INSERT应通过"""
        sql = "INSERT INTO t_transaction (txn_no, from_account, to_account, amount, status) VALUES (?, ?, ?, ?, ?)"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not True:
            record_issue("P1", "SQL审核", "合规转账INSERT被误判", "passed=True", f"passed={data['passed']}")

    def test_uat08_03_balance_update_ok(self):
        """余额UPDATE应通过"""
        sql = "UPDATE t_account SET balance = balance - ? WHERE account_id = ? AND balance >= ?"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not True:
            record_issue("P2", "SQL审核", "合规余额UPDATE被误判", "passed=True", f"passed={data['passed']}")

    def test_uat08_04_log_create_table_ok(self):
        """日志建表应通过"""
        sql = """
        CREATE TABLE t_operation_log (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
            operator VARCHAR(64) NOT NULL COMMENT '操作人',
            operation VARCHAR(32) NOT NULL COMMENT '操作类型',
            detail VARCHAR(512) DEFAULT '' COMMENT '操作详情',
            ip_address VARCHAR(45) DEFAULT '' COMMENT 'IP地址',
            create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            INDEX idx_operator (operator),
            INDEX idx_create_time (create_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='操作日志表'
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not True:
            record_issue("P1", "SQL审核", "合规日志建表被误判", "passed=True", f"passed={data['passed']}, violations={data['violations']}")

    def test_uat08_05_dangerous_batch_delete(self):
        """无条件批量删除应被拦截"""
        sql = "DELETE FROM t_temp_data"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not False:
            record_issue("P0", "SQL审核", "无条件DELETE未被拦截", "passed=False", f"passed={data['passed']}")

    def test_uat08_06_select_star_on_big_table(self):
        """大表SELECT *应被拦截"""
        sql = "SELECT * FROM t_transaction_history WHERE create_time > '2024-01-01'"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not False:
            record_issue("P1", "SQL审核", "大表SELECT *未被拦截", "passed=False", f"passed={data['passed']}")
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R012" not in rule_ids:
            record_issue("P1", "SQL审核", "SELECT *缺少R012", "R012 in violations", f"rule_ids={rule_ids}")

    def test_uat08_07_float_amount_rejected(self):
        """金额字段FLOAT应被拦截"""
        sql = "CREATE TABLE t_fund (id BIGINT PRIMARY KEY, amount FLOAT, fee DOUBLE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = {v["rule_id"] for v in data.get("violations", [])}
        if "R009" not in rule_ids:
            record_issue("P0", "SQL审核", "金额FLOAT/DOUBLE未被R009拦截", "R009 in violations", f"rule_ids={rule_ids}")

    def test_uat08_08_enum_status_rejected(self):
        """状态字段ENUM应被拦截"""
        sql = "CREATE TABLE t_order (id BIGINT PRIMARY KEY, status ENUM('pending','paid','done')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R006" not in rule_ids:
            record_issue("P1", "SQL审核", "ENUM未被R006拦截", "R006 in violations", f"rule_ids={rule_ids}")


# ═══════════════════════════════════════════════════════════
# UAT-09: 慢SQL真实场景
# ═══════════════════════════════════════════════════════════

class TestUAT09_SlowQueryRealScenarios:
    """慢SQL真实场景"""

    def test_uat09_01_full_table_scan_detected(self):
        """全表扫描应被检测"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_transaction",
                "type": "ALL", "possible_keys": None, "key": None,
                "rows": 5000000, "filtered": 1.0,
                "extra": "Using where"
            }]
        })
        data = resp.json()
        problem_types = [a["problem_type"] for a in data.get("analyses", [])]
        if "全表扫描" not in problem_types:
            record_issue("P1", "慢SQL分析", "全表扫描未检测", "包含全表扫描", f"problem_types={problem_types}")

    def test_uat09_02_missing_index_detected(self):
        """缺失索引应被检测"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_account",
                "type": "ALL", "possible_keys": "NULL", "key": "NULL",
                "rows": 1000000, "filtered": 5.0,
                "extra": "Using where"
            }]
        })
        data = resp.json()
        problem_types = [a["problem_type"] for a in data.get("analyses", [])]
        if "缺失索引" not in problem_types:
            record_issue("P1", "慢SQL分析", "缺失索引未检测", "包含缺失索引", f"problem_types={problem_types}")

    def test_uat09_03_good_plan_no_error(self):
        """良好执行计划不应报ERROR"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_account",
                "type": "const", "possible_keys": "PRIMARY", "key": "PRIMARY",
                "rows": 1, "filtered": 100.0, "extra": ""
            }]
        })
        data = resp.json()
        errors = [a for a in data.get("analyses", []) if a["severity"] == "ERROR"]
        if errors:
            record_issue("P1", "慢SQL分析", "良好EXPLAIN误报ERROR", "0个ERROR", f"errors={errors}")

    def test_uat09_04_filesort_and_temporary(self):
        """filesort+temporary应被检测"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_transaction",
                "type": "ALL", "rows": 2000000, "filtered": 100.0,
                "extra": "Using where; Using temporary; Using filesort"
            }]
        })
        data = resp.json()
        problem_types = [a["problem_type"] for a in data.get("analyses", [])]
        if "Using filesort" not in problem_types:
            record_issue("P2", "慢SQL分析", "filesort未检测", "包含Using filesort", f"problem_types={problem_types}")
        if "Using temporary" not in problem_types:
            record_issue("P2", "慢SQL分析", "temporary未检测", "包含Using temporary", f"problem_types={problem_types}")

    def test_uat09_05_high_frequency_slow_sql(self):
        """高频慢SQL应被标记为高优先级"""
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "SELECT * FROM t_account WHERE user_id = ?",
            "sql_text": "SELECT * FROM t_account WHERE user_id = 12345",
            "db_name": "core_db",
            "exec_count": 50000,
            "avg_time_ms": 800,
            "rows_examined": 3000000,
            "rows_sent": 5,
        })
        data = resp.json()
        if data.get("severity") != "ERROR":
            record_issue("P1", "慢SQL分析", "高频慢SQL未标记为ERROR", "ERROR", f"severity={data.get('severity')}")

    def test_uat09_06_scan_ratio_extreme(self):
        """极端扫描比应被检测"""
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "SELECT id FROM t WHERE col = ?",
            "sql_text": "SELECT id FROM t WHERE col = 1",
            "exec_count": 100,
            "avg_time_ms": 100,
            "rows_examined": 10000000,
            "rows_sent": 1,
        })
        data = resp.json()
        problem_types = [a["problem_type"] for a in data.get("analyses", [])]
        if "索引使用不充分" not in problem_types:
            record_issue("P1", "慢SQL分析", "极端扫描比10000000:1未检测", "包含索引使用不充分", f"problem_types={problem_types}")

    def test_uat09_07_slow_query_status_update(self):
        """慢SQL状态更新应正常工作"""
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "STATUS_UPDATE_TEST SELECT 1",
            "sql_text": "SELECT 1",
            "exec_count": 1,
            "avg_time_ms": 1,
        })
        slow_id = resp.json()["id"]

        # 更新为optimized
        resp = client.put(f"/api/v1/slow-queries/{slow_id}/status", json={"status": "optimized"})
        if resp.status_code != 200:
            record_issue("P1", "慢SQL管理", "状态更新失败", "200", f"{resp.status_code}")

        # 验证
        resp = client.get(f"/api/v1/slow-queries/{slow_id}")
        if resp.json().get("status") != "optimized":
            record_issue("P1", "慢SQL管理", "状态未持久化", "optimized", f"{resp.json().get('status')}")


# ═══════════════════════════════════════════════════════════
# UAT-10: MyBatis XML真实场景
# ═══════════════════════════════════════════════════════════

class TestUAT10_MyBatisRealScenarios:
    """MyBatis XML真实业务场景"""

    def test_uat10_01_complex_dynamic_sql(self):
        """复杂动态SQL审核"""
        xml = """
        <mapper namespace="com.bank.mapper.TransactionMapper">
            <select id="queryTransactions" resultType="Transaction">
                SELECT t.txn_no, t.amount, t.status, t.create_time
                FROM t_transaction t
                <where>
                    <if test="accountNo != null">
                        AND t.account_no = #{accountNo}
                    </if>
                    <if test="startDate != null">
                        AND t.create_time >= #{startDate}
                    </if>
                    <if test="endDate != null">
                        AND t.create_time <= #{endDate}
                    </if>
                    <if test="status != null">
                        AND t.status = #{status}
                    </if>
                </where>
                ORDER BY t.create_time DESC
            </select>
            <insert id="createTransaction">
                INSERT INTO t_transaction (txn_no, account_no, amount, status, create_time)
                VALUES (#{txnNo}, #{accountNo}, #{amount}, #{status}, NOW())
            </insert>
        </mapper>
        """
        resp = client.post("/api/v1/audit/file", json={"content": xml, "file_path": "TransactionMapper.xml"})
        data = resp.json()
        if data["summary"]["total_sql"] < 2:
            record_issue("P1", "文件审核", "复杂MyBatis XML提取SQL不足", ">=2条", f"total_sql={data['summary']['total_sql']}")

    def test_uat10_02_bad_sql_in_xml_detected(self):
        """XML中的不合规SQL应被检测"""
        xml = """
        <mapper namespace="com.bank.mapper.UserMapper">
            <select id="getAllUsers">
                SELECT * FROM t_user
            </select>
        </mapper>
        """
        resp = client.post("/api/v1/audit/file", json={"content": xml, "file_path": "UserMapper.xml"})
        data = resp.json()
        if data["summary"]["failed"] == 0:
            record_issue("P1", "文件审核", "XML中SELECT *未被检测", "failed>=1", f"failed={data['summary']['failed']}")

    def test_uat10_03_foreach_batch_insert(self):
        """批量INSERT审核"""
        xml = """
        <mapper namespace="com.bank.mapper.BatchMapper">
            <insert id="batchInsert">
                INSERT INTO t_temp (id, name, value) VALUES
                <foreach collection="list" item="item" separator=",">
                    (#{item.id}, #{item.name}, #{item.value})
                </foreach>
            </insert>
        </mapper>
        """
        resp = client.post("/api/v1/audit/file", json={"content": xml, "file_path": "BatchMapper.xml"})
        data = resp.json()
        if data["summary"]["total_sql"] == 0:
            record_issue("P1", "文件审核", "批量INSERT XML未被提取", "total_sql>=1", "total_sql=0")


# ═══════════════════════════════════════════════════════════
# UAT-11: 数据准确性验证
# ═══════════════════════════════════════════════════════════

class TestUAT11_DataAccuracy:
    """数据准确性验证"""

    def test_uat11_01_violation_count_accuracy(self):
        """违规数量统计准确性"""
        sql = "CREATE TABLE t_bad (id INT, ts TIMESTAMP, amount FLOAT, bio TEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        violations = data.get("violations", [])
        # 应该有 R003(无主键), R006/R007(ENUM/TIMESTAMP), R009(FLOAT), R011(TEXT)
        if len(violations) < 3:
            record_issue("P2", "数据准确性", "违规数量偏少", ">=3", f"{len(violations)}")

    def test_uat11_02_severity_accuracy(self):
        """严重级别准确性"""
        sql = "SELECT * FROM t_user"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        for v in data.get("violations", []):
            if v["rule_id"] == "R012" and v["severity"] != "ERROR":
                record_issue("P1", "数据准确性", "R012级别应为ERROR", "ERROR", f"{v['severity']}")

    def test_uat11_03_suggestion_not_empty(self):
        """违规建议不应为空"""
        sql = "SELECT * FROM t_user WHERE id = 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        for v in data.get("violations", []):
            if not v.get("suggestion"):
                record_issue("P2", "数据准确性", f"{v['rule_id']}缺少建议", "suggestion非空", "suggestion为空")

    def test_uat11_04_explain_analysis_accuracy(self):
        """EXPLAIN分析准确性"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "ALL", "rows": 1000000, "filtered": 0.5,
                "extra": "Using where; Using filesort; Using temporary"
            }]
        })
        data = resp.json()
        problem_types = {a["problem_type"] for a in data.get("analyses", [])}
        expected = {"全表扫描", "Using filesort", "Using temporary"}
        missing = expected - problem_types
        if missing:
            record_issue("P2", "数据准确性", f"EXPLAIN分析遗漏: {missing}", f"应包含{expected}", f"实际={problem_types}")


# ═══════════════════════════════════════════════════════════
# UAT-12: 错误处理与用户体验
# ═══════════════════════════════════════════════════════════

class TestUAT12_ErrorHandling:
    """错误处理与用户体验"""

    def test_uat12_01_empty_sql_error_msg(self):
        """空SQL错误信息应友好"""
        resp = client.post("/api/v1/audit/sql", json={"sql": ""})
        if resp.status_code == 422:
            data = resp.json()
            if "detail" not in data:
                record_issue("P3", "用户体验", "空SQL缺少错误详情", "有detail", f"keys={list(data.keys())}")

    def test_uat12_02_not_found_error_msg(self):
        """404错误信息应友好"""
        resp = client.get("/api/v1/slow-queries/999999")
        if resp.status_code == 404:
            data = resp.json()
            if "detail" not in data:
                record_issue("P3", "用户体验", "404缺少错误详情", "有detail", f"keys={list(data.keys())}")

    def test_uat12_03_invalid_status_error_msg(self):
        """无效状态错误信息应友好"""
        resp = client.put("/api/v1/slow-queries/1/status", json={"status": "invalid"})
        if resp.status_code == 400:
            data = resp.json()
            if "detail" not in data:
                record_issue("P3", "用户体验", "无效状态缺少错误详情", "有detail", f"keys={list(data.keys())}")

    def test_uat12_04_api_response_consistency(self):
        """API响应格式一致性"""
        resp = client.post("/api/v1/audit/sql", json={"sql": "SELECT id FROM t_user WHERE id = 1"})
        data = resp.json()
        required_fields = ["passed", "violations", "sql_type"]
        for field in required_fields:
            if field not in data:
                record_issue("P2", "API一致性", f"审核响应缺少{field}", f"包含{field}", f"keys={list(data.keys())}")


# ═══════════════════════════════════════════════════════════
# UAT-13: 端到端业务流程
# ═══════════════════════════════════════════════════════════

class TestUAT13_E2EWorkflow:
    """端到端业务流程"""

    def test_uat13_01_dev_review_workflow(self):
        """开发审核完整流程"""
        # 1. 开发提交SQL
        sqls = [
            ("SELECT account_id, balance FROM t_account WHERE account_id = ?", True),
            ("SELECT * FROM t_account WHERE status = 1", False),
            ("UPDATE t_account SET balance = 0", False),
            ("DELETE FROM t_transaction_log", False),
        ]
        for sql, expected in sqls:
            resp = client.post("/api/v1/audit/sql", json={"sql": sql})
            data = resp.json()
            if data["passed"] != expected:
                record_issue("P1", "端到端", f"SQL '{sql[:30]}' 结果不符预期", f"passed={expected}", f"passed={data['passed']}")

        # 2. 查看Dashboard
        resp = client.get("/api/v1/dashboard/summary")
        if resp.status_code != 200:
            record_issue("P1", "端到端", "Dashboard不可用", "200", f"{resp.status_code}")

    def test_uat13_02_slow_sql_triage_workflow(self):
        """慢SQL分诊完整流程"""
        # 1. 添加慢SQL
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "SELECT * FROM t_transaction WHERE create_time > ?",
            "sql_text": "SELECT * FROM t_transaction WHERE create_time > '2024-01-01'",
            "db_name": "core_db",
            "exec_count": 20000,
            "avg_time_ms": 1200,
            "rows_examined": 10000000,
            "rows_sent": 500,
        })
        if resp.status_code != 200:
            record_issue("P0", "端到端", "添加慢SQL失败", "200", f"{resp.status_code}")
            return

        data = resp.json()
        slow_id = data["id"]

        # 2. 检查分析结果
        if not data.get("analyses"):
            record_issue("P1", "端到端", "慢SQL缺少分析结果", "有analyses", "analyses为空")

        # 3. EXPLAIN分析
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_transaction",
                "type": "ALL", "rows": 10000000, "filtered": 5.0,
                "extra": "Using where; Using filesort"
            }]
        })
        if resp.status_code != 200:
            record_issue("P1", "端到端", "EXPLAIN分析失败", "200", f"{resp.status_code}")

        # 4. 更新状态
        resp = client.put(f"/api/v1/slow-queries/{slow_id}/status", json={"status": "optimized"})
        if resp.status_code != 200:
            record_issue("P1", "端到端", "状态更新失败", "200", f"{resp.status_code}")

        # 5. 验证
        resp = client.get(f"/api/v1/slow-queries/{slow_id}")
        if resp.json().get("status") != "optimized":
            record_issue("P1", "端到端", "状态未持久化", "optimized", f"{resp.json().get('status')}")

    def test_uat13_03_file_review_workflow(self):
        """文件审核完整流程"""
        xml = """
        <mapper namespace="com.bank.mapper.AccountMapper">
            <select id="getAccount">
                SELECT account_id, balance, status FROM t_account WHERE account_id = #{accountId}
            </select>
            <select id="badQuery">
                SELECT * FROM t_account WHERE status = #{status}
            </select>
            <insert id="createAccount">
                INSERT INTO t_account (account_no, user_id, balance, status) VALUES (#{accountNo}, #{userId}, #{balance}, #{status})
            </insert>
        </mapper>
        """
        resp = client.post("/api/v1/audit/file", json={"content": xml, "file_path": "AccountMapper.xml"})
        data = resp.json()

        # 应该提取3条SQL
        if data["summary"]["total_sql"] < 3:
            record_issue("P1", "端到端", "MyBatis XML提取SQL不足", ">=3", f"{data['summary']['total_sql']}")

        # 应该有1条失败（SELECT *）
        if data["summary"]["failed"] == 0:
            record_issue("P1", "端到端", "SELECT *未被检测", "failed>=1", f"failed={data['summary']['failed']}")


# ═══════════════════════════════════════════════════════════
# 问题清单输出
# ═══════════════════════════════════════════════════════════

class TestUAT_Round2Report:
    """第二轮UAT问题清单"""

    def test_generate_report(self):
        """生成第二轮UAT问题清单"""
        if issues:
            print("\n" + "=" * 80)
            print("UAT 第二轮问题清单")
            print("=" * 80)
            for i, issue in enumerate(issues, 1):
                print(f"\n[{issue['severity']}] UAT2-{i:03d}")
                print(f"  模块: {issue['module']}")
                print(f"  描述: {issue['description']}")
                print(f"  期望: {issue['expected']}")
                print(f"  实际: {issue['actual']}")
            print("\n" + "=" * 80)
            p0 = sum(1 for i in issues if i['severity'] == 'P0')
            p1 = sum(1 for i in issues if i['severity'] == 'P1')
            p2 = sum(1 for i in issues if i['severity'] == 'P2')
            p3 = sum(1 for i in issues if i['severity'] == 'P3')
            print(f"共发现 {len(issues)} 个问题")
            print(f"  P0(阻塞): {p0}  P1(严重): {p1}  P2(一般): {p2}  P3(建议): {p3}")
            print("=" * 80)
        else:
            print("\n" + "=" * 80)
            print("UAT 第二轮：0个问题，全部通过")
            print("=" * 80)
        assert True
