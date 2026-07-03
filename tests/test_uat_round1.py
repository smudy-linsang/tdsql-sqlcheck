"""
TDSQL SQL审核工具 - 第一轮UAT测试

用户验收测试：从用户视角验证功能正确性，记录发现的问题。
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

# 问题清单收集器
issues = []


def record_issue(severity, module, description, expected, actual):
    """记录UAT问题"""
    issues.append({
        "severity": severity,  # P0/P1/P2/P3
        "module": module,
        "description": description,
        "expected": expected,
        "actual": actual,
    })


# ═══════════════════════════════════════════════════════════
# UAT-01: 开发人员提交SQL审核
# ═══════════════════════════════════════════════════════════

class TestUAT01_DevSQLReview:
    """开发人员SQL审核场景"""

    def test_uat01_01_good_select(self):
        """UAT-01-01: 开发人员提交合规SELECT应通过"""
        sql = "SELECT id, user_name, email FROM t_user WHERE status = 1 AND dept_id = 100"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not True:
            record_issue("P1", "SQL审核", "合规SELECT被误判为不通过", "passed=True", f"passed={data['passed']}, violations={data['violations']}")
        assert data["passed"] is True

    def test_uat01_02_select_star_rejected(self):
        """UAT-01-02: SELECT * 应被拦截并给出明确建议"""
        sql = "SELECT * FROM t_user WHERE id = 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not False:
            record_issue("P1", "SQL审核", "SELECT * 未被拦截", "passed=False", f"passed={data['passed']}")
        assert data["passed"] is False
        # 检查建议是否可操作
        r012 = [v for v in data["violations"] if v["rule_id"] == "R012"]
        if not r012:
            record_issue("P2", "SQL审核", "SELECT * 缺少R012违规", "R012 in violations", f"violations={data['violations']}")
        elif not r012[0].get("suggestion"):
            record_issue("P2", "SQL审核", "R012缺少修复建议", "suggestion非空", "suggestion为空")

    def test_uat01_03_create_table_compliance(self):
        """UAT-01-03: 合规建表语句应通过"""
        sql = """
        CREATE TABLE t_user_address (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
            user_id BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
            address VARCHAR(256) NOT NULL COMMENT '地址',
            is_default TINYINT NOT NULL DEFAULT 0 COMMENT '是否默认',
            is_deleted TINYINT NOT NULL DEFAULT 0 COMMENT '逻辑删除标记',
            create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
            INDEX idx_user_id (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户地址表' SHARDKEY=id
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not True:
            record_issue("P1", "SQL审核", "合规建表被误判", "passed=True", f"passed={data['passed']}, violations={data['violations']}")
        assert data["passed"] is True

    def test_uat01_04_bad_create_table_report(self):
        """UAT-01-04: 不合规建表应给出完整违规清单"""
        sql = """
        CREATE TABLE test_table (
            id INT,
            status ENUM('a','b'),
            ts TIMESTAMP,
            amount DOUBLE,
            bio TEXT,
            name VARCHAR(5000)
        )
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = {v["rule_id"] for v in data["violations"]}
        # 检查是否遗漏关键规则
        expected_rules = {"R003", "R004", "R005", "R006", "R007", "R009", "R011"}
        missing = expected_rules - rule_ids
        if missing:
            record_issue("P2", "SQL审核", f"不合规建表遗漏规则: {missing}", f"应包含{expected_rules}", f"实际={rule_ids}")

    def test_uat01_05_dangerous_delete(self):
        """UAT-01-05: 无WHERE的DELETE应被拦截"""
        sql = "DELETE FROM t_order"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False
        # 检查是否有足够的警告信息
        error_violations = [v for v in data["violations"] if v["severity"] == "ERROR"]
        if len(error_violations) == 0:
            record_issue("P1", "SQL审核", "无WHERE的DELETE缺少ERROR级别违规", "至少1个ERROR", "0个ERROR")

    def test_uat01_06_update_without_where(self):
        """UAT-01-06: 无WHERE的UPDATE应被拦截"""
        sql = "UPDATE t_user SET status = 0"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False

    def test_uat01_07_insert_ok(self):
        """UAT-01-07: 合规INSERT应通过"""
        sql = "INSERT INTO t_user (id, user_name, email) VALUES (1, 'zhangsan', 'zhang@test.com')"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not True:
            record_issue("P2", "SQL审核", "合规INSERT被误判", "passed=True", f"passed={data['passed']}")

    def test_uat01_08_timestamp_field_rejected(self):
        """UAT-01-08: TIMESTAMP字段应被拦截"""
        sql = "CREATE TABLE t_log (id BIGINT PRIMARY KEY, created_at TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        if "R007" not in rule_ids:
            record_issue("P1", "SQL审核", "TIMESTAMP字段未被R007拦截", "R017 in violations", f"rule_ids={rule_ids}")

    def test_uat01_09_float_finance_rejected(self):
        """UAT-01-09: 财务字段FLOAT应被拦截"""
        sql = "CREATE TABLE t_order (id BIGINT PRIMARY KEY, amount FLOAT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        if "R009" not in rule_ids:
            record_issue("P1", "SQL审核", "财务FLOAT未被R009拦截", "R009 in violations", f"rule_ids={rule_ids}")


# ═══════════════════════════════════════════════════════════
# UAT-02: DBA审核MyBatis XML文件
# ═══════════════════════════════════════════════════════════

class TestUAT02_DBAFileReview:
    """DBA文件审核场景"""

    def test_uat02_01_mybatis_xml_review(self):
        """UAT-02-01: 审核MyBatis XML应提取所有SQL"""
        xml = """
        <mapper namespace="com.example.OrderMapper">
            <select id="getOrder" resultType="Order">
                SELECT id, order_no, amount FROM t_order WHERE id = #{orderId}
            </select>
            <select id="listOrders" resultType="Order">
                SELECT * FROM t_order WHERE user_id = #{userId}
            </select>
            <insert id="createOrder">
                INSERT INTO t_order (order_no, user_id, amount) VALUES (#{orderNo}, #{userId}, #{amount})
            </insert>
            <update id="updateStatus">
                UPDATE t_order SET status = #{status} WHERE id = #{orderId}
            </update>
            <delete id="deleteOrder">
                DELETE FROM t_order WHERE id = #{orderId}
            </delete>
        </mapper>
        """
        resp = client.post("/api/v1/audit/file", json={"content": xml, "file_path": "OrderMapper.xml"})
        data = resp.json()
        if data["summary"]["total_sql"] < 4:
            record_issue("P1", "文件审核", "MyBatis XML未提取足够SQL", ">=4条SQL", f"total_sql={data['summary']['total_sql']}")

        # 检查SELECT *被检测到
        select_star_found = False
        for r in data["results"]:
            for v in r.get("violations", []):
                if v["rule_id"] == "R012":
                    select_star_found = True
        if not select_star_found:
            record_issue("P2", "文件审核", "MyBatis XML中SELECT *未被检测", "R012触发", "未触发")

    def test_uat02_02_mybatis_dynamic_sql(self):
        """UAT-02-02: MyBatis动态SQL应正确清理"""
        xml = """
        <mapper namespace="com.example.UserMapper">
            <select id="searchUsers">
                SELECT id, user_name FROM t_user
                <where>
                    <if test="name != null">AND user_name LIKE CONCAT('%', #{name}, '%')</if>
                    <if test="status != null">AND status = #{status}</if>
                </where>
            </select>
        </mapper>
        """
        resp = client.post("/api/v1/audit/file", json={"content": xml, "file_path": "UserMapper.xml"})
        data = resp.json()
        if data["summary"]["total_sql"] == 0:
            record_issue("P1", "文件审核", "MyBatis动态SQL未被提取", "total_sql>=1", "total_sql=0")

    def test_uat02_03_sql_file_review(self):
        """UAT-02-03: SQL脚本文件审核"""
        content = """
-- 建表脚本
CREATE TABLE t_test (
    id BIGINT PRIMARY KEY,
    name VARCHAR(50)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 查询
SELECT id, name FROM t_test WHERE id = 1;

-- 危险操作
SELECT * FROM t_test;
"""
        resp = client.post("/api/v1/audit/file", json={"content": content, "file_path": "V1__init.sql"})
        data = resp.json()
        if data["summary"]["total_sql"] < 2:
            record_issue("P1", "文件审核", "SQL文件未提取足够SQL", ">=2条", f"total_sql={data['summary']['total_sql']}")

    def test_uat02_04_file_summary_accuracy(self):
        """UAT-02-04: 文件审核汇总数据准确性"""
        content = "SELECT id FROM t_ok WHERE id = 1;\nSELECT * FROM t_bad;"
        resp = client.post("/api/v1/audit/file", json={"content": content, "file_path": "test.sql"})
        data = resp.json()
        summary = data["summary"]
        if summary["total_sql"] != 2:
            record_issue("P1", "文件审核", "汇总total_sql不准确", "2", f"{summary['total_sql']}")
        if summary["passed"] != 1:
            record_issue("P2", "文件审核", "汇总passed不准确", "1", f"{summary['passed']}")
        if summary["failed"] != 1:
            record_issue("P2", "文件审核", "汇总failed不准确", "1", f"{summary['failed']}")


# ═══════════════════════════════════════════════════════════
# UAT-03: 慢SQL分析
# ═══════════════════════════════════════════════════════════

class TestUAT03_SlowQueryAnalysis:
    """慢SQL分析场景"""

    def test_uat03_01_slow_sql_auto_analysis(self):
        """UAT-03-01: 添加慢SQL应自动分析并给出问题类型"""
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "SELECT * FROM t_order WHERE user_id = ? AND status = ?",
            "sql_text": "SELECT * FROM t_order WHERE user_id = 123 AND status = 1",
            "db_name": "order_db",
            "exec_count": 10000,
            "avg_time_ms": 500,
            "rows_examined": 2000000,
            "rows_sent": 50,
        })
        data = resp.json()
        if "problem_type" not in data:
            record_issue("P0", "慢SQL分析", "慢SQL缺少problem_type", "有problem_type", f"keys={list(data.keys())}")
        if not data.get("analyses"):
            record_issue("P0", "慢SQL分析", "慢SQL缺少分析结果", "有analyses", "analyses为空")
        # 应该检测到SELECT *
        problem_types = [a["problem_type"] for a in data.get("analyses", [])]
        if "SELECT *" not in problem_types:
            record_issue("P2", "慢SQL分析", "SELECT * 未被慢SQL分析器检测", "包含SELECT *", f"problem_types={problem_types}")

    def test_uat03_02_scan_ratio_detection(self):
        """UAT-03-02: 扫描/返回行数比过大应检测"""
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "SELECT id FROM t WHERE status = ?",
            "sql_text": "SELECT id FROM t WHERE status = 1",
            "exec_count": 1000,
            "avg_time_ms": 200,
            "rows_examined": 1000000,
            "rows_sent": 10,
        })
        data = resp.json()
        problem_types = [a["problem_type"] for a in data.get("analyses", [])]
        if "索引使用不充分" not in problem_types:
            record_issue("P1", "慢SQL分析", "扫描/返回比100000:1未检测", "包含索引使用不充分", f"problem_types={problem_types}")

    def test_uat03_03_explain_all_scan(self):
        """UAT-03-03: EXPLAIN全表扫描应检测"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "ALL", "possible_keys": None, "key": None,
                "rows": 500000, "filtered": 5.0, "extra": "Using where"
            }]
        })
        data = resp.json()
        problem_types = [a["problem_type"] for a in data.get("analyses", [])]
        if "全表扫描" not in problem_types:
            record_issue("P1", "慢SQL分析", "EXPLAIN ALL未检测为全表扫描", "包含全表扫描", f"problem_types={problem_types}")

    def test_uat03_04_explain_good_plan(self):
        """UAT-03-04: 良好EXPLAIN不应报错"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "ref", "possible_keys": "idx_user_id", "key": "idx_user_id",
                "rows": 5, "filtered": 100.0, "extra": "Using index"
            }]
        })
        data = resp.json()
        errors = [a for a in data.get("analyses", []) if a["severity"] == "ERROR"]
        if errors:
            record_issue("P1", "慢SQL分析", "良好EXPLAIN被误报为ERROR", "0个ERROR", f"errors={errors}")

    def test_uat03_05_slow_query_list(self):
        """UAT-03-05: 慢SQL列表应支持分页"""
        resp = client.get("/api/v1/slow-queries?limit=5&offset=0")
        data = resp.json()
        if "items" not in data:
            record_issue("P1", "慢SQL列表", "缺少items字段", "有items", f"keys={list(data.keys())}")
        if "total" not in data:
            record_issue("P1", "慢SQL列表", "缺少total字段", "有total", f"keys={list(data.keys())}")

    def test_uat03_06_explain_filesort_detected(self):
        """UAT-03-06: EXPLAIN filesort应检测"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "ALL", "rows": 100000, "filtered": 100.0,
                "extra": "Using where; Using filesort"
            }]
        })
        data = resp.json()
        problem_types = [a["problem_type"] for a in data.get("analyses", [])]
        if "Using filesort" not in problem_types:
            record_issue("P1", "慢SQL分析", "filesort未检测", "包含Using filesort", f"problem_types={problem_types}")

    def test_uat03_07_deep_pagination_detected(self):
        """UAT-03-07: 深度分页应检测"""
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "SELECT * FROM t ORDER BY id LIMIT ?, ?",
            "sql_text": "SELECT * FROM t ORDER BY id LIMIT 500000, 20",
            "exec_count": 100,
            "avg_time_ms": 300,
        })
        data = resp.json()
        problem_types = [a["problem_type"] for a in data.get("analyses", [])]
        if "深度分页" not in problem_types:
            record_issue("P2", "慢SQL分析", "深度分页LIMIT 500000未检测", "包含深度分页", f"problem_types={problem_types}")


# ═══════════════════════════════════════════════════════════
# UAT-04: Dashboard
# ═══════════════════════════════════════════════════════════

class TestUAT04_Dashboard:
    """Dashboard场景"""

    def test_uat04_01_summary_data(self):
        """UAT-04-01: Dashboard概览数据完整性"""
        resp = client.get("/api/v1/dashboard/summary")
        data = resp.json()
        required_keys = ["audit", "slow_queries", "rules"]
        for key in required_keys:
            if key not in data:
                record_issue("P1", "Dashboard", f"概览缺少{key}字段", f"包含{key}", f"keys={list(data.keys())}")

        # 检查rules数据
        if data.get("rules", {}).get("total") != 22:
            record_issue("P2", "Dashboard", "规则总数不正确", "22", f"{data.get('rules', {}).get('total')}")

    def test_uat04_02_audit_trend(self):
        """UAT-04-02: 审核趋势数据"""
        resp = client.get("/api/v1/dashboard/audit-trend?days=7")
        data = resp.json()
        if "dates" not in data:
            record_issue("P2", "Dashboard", "趋势缺少dates", "有dates", f"keys={list(data.keys())}")


# ═══════════════════════════════════════════════════════════
# UAT-05: GitLab集成
# ═══════════════════════════════════════════════════════════

class TestUAT05_GitLabIntegration:
    """GitLab集成场景"""

    def test_uat05_01_config_complete(self):
        """UAT-05-01: GitLab配置说明完整"""
        resp = client.get("/api/v1/gitlab/config")
        data = resp.json()
        if "webhook_url" not in data:
            record_issue("P2", "GitLab集成", "配置缺少webhook_url", "有webhook_url", f"keys={list(data.keys())}")
        if "setup_steps" not in data:
            record_issue("P2", "GitLab集成", "配置缺少setup_steps", "有setup_steps", f"keys={list(data.keys())}")

    def test_uat05_02_diff_audit(self):
        """UAT-05-02: Diff审核功能"""
        resp = client.post("/api/v1/gitlab/audit/diff", json={
            "diff": "+SELECT * FROM t_user WHERE id = 1",
            "file_path": "test.sql"
        })
        assert resp.status_code == 200

    def test_uat05_03_repo_audit(self):
        """UAT-05-03: 仓库审核功能"""
        resp = client.post("/api/v1/gitlab/audit/repository", json={
            "files": [
                {"path": "UserMapper.xml", "content": "<mapper><select id='test'>SELECT * FROM t_user</select></mapper>"},
            ]
        })
        data = resp.json()
        if "total_sql" not in data:
            record_issue("P1", "GitLab集成", "仓库审核缺少total_sql", "有total_sql", f"keys={list(data.keys())}")


# ═══════════════════════════════════════════════════════════
# UAT-06: TDSQL管理
# ═══════════════════════════════════════════════════════════

class TestUAT06_TDSQLManage:
    """TDSQL管理场景"""

    def test_uat06_01_connection_status(self):
        """UAT-06-01: 连接状态查询"""
        resp = client.get("/api/v1/tdsql/status")
        data = resp.json()
        if "connected" not in data:
            record_issue("P1", "TDSQL管理", "状态缺少connected字段", "有connected", f"keys={list(data.keys())}")

    def test_uat06_02_no_connection_error_msg(self):
        """UAT-06-02: 未连接时错误信息应明确"""
        client.post("/api/v1/tdsql/disconnect")
        resp = client.get("/api/v1/tdsql/tables")
        if resp.status_code != 400:
            record_issue("P2", "TDSQL管理", "未连接时tables应返回400", "400", f"{resp.status_code}")
        data = resp.json()
        if "detail" not in data:
            record_issue("P2", "TDSQL管理", "未连接错误缺少detail", "有detail", f"keys={list(data.keys())}")


# ═══════════════════════════════════════════════════════════
# UAT-07: API文档
# ═══════════════════════════════════════════════════════════

class TestUAT07_APIDocumentation:
    """API文档验证"""

    def test_uat07_01_swagger_accessible(self):
        """UAT-07-01: Swagger文档可访问"""
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_uat07_02_openapi_has_all_paths(self):
        """UAT-07-02: OpenAPI包含所有API路径"""
        resp = client.get("/openapi.json")
        schema = resp.json()
        paths = schema.get("paths", {})
        required_paths = [
            "/api/v1/audit/sql",
            "/api/v1/audit/file",
            "/api/v1/slow-queries",
            "/api/v1/slow-queries/analyze-explain",
            "/api/v1/dashboard/summary",
            "/api/v1/gitlab/config",
            "/api/v1/tdsql/status",
        ]
        for path in required_paths:
            if path not in paths:
                record_issue("P2", "API文档", f"OpenAPI缺少路径: {path}", f"包含{path}", "缺失")


# ═══════════════════════════════════════════════════════════
# 问题清单输出
# ═══════════════════════════════════════════════════════════

class TestUAT_IssueReport:
    """问题清单汇总"""

    def test_generate_report(self):
        """生成UAT问题清单"""
        # 运行所有UAT测试后，检查是否有记录的问题
        if issues:
            print("\n" + "=" * 80)
            print("UAT 问题清单")
            print("=" * 80)
            for i, issue in enumerate(issues, 1):
                print(f"\n[{issue['severity']}] UAT-{i:03d}")
                print(f"  模块: {issue['module']}")
                print(f"  描述: {issue['description']}")
                print(f"  期望: {issue['expected']}")
                print(f"  实际: {issue['actual']}")
            print("\n" + "=" * 80)
            print(f"共发现 {len(issues)} 个问题")
            p0 = sum(1 for i in issues if i['severity'] == 'P0')
            p1 = sum(1 for i in issues if i['severity'] == 'P1')
            p2 = sum(1 for i in issues if i['severity'] == 'P2')
            print(f"  P0(阻塞): {p0}")
            print(f"  P1(严重): {p1}")
            print(f"  P2(一般): {p2}")
            print("=" * 80)
        # 此测试始终通过，仅用于输出报告
        assert True
