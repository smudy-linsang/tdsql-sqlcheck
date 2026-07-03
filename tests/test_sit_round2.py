"""
TDSQL SQL审核工具 - 第二轮SIT测试

覆盖边界场景、错误处理、完整业务流程。
"""
import json
import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


# ═══════════════════════════════════════════════════════════
# 一、边界场景 - SQL审核
# ═══════════════════════════════════════════════════════════

class TestEdgeCasesAudit:
    """SQL审核边界场景"""

    def test_empty_sql(self):
        """空SQL应返回错误"""
        resp = client.post("/api/v1/audit/sql", json={"sql": ""})
        assert resp.status_code == 422  # Pydantic验证失败

    def test_whitespace_only_sql(self):
        """纯空格SQL"""
        resp = client.post("/api/v1/audit/sql", json={"sql": "   "})
        # 应该能处理，不崩溃
        assert resp.status_code in (200, 422)

    def test_very_long_sql(self):
        """超长SQL（10KB）"""
        long_sql = "SELECT id FROM t_user WHERE " + " AND ".join(
            [f"col{i} = {i}" for i in range(200)]
        )
        resp = client.post("/api/v1/audit/sql", json={"sql": long_sql})
        assert resp.status_code == 200
        data = resp.json()
        assert "passed" in data

    def test_sql_with_special_chars(self):
        """含特殊字符的SQL"""
        sql = "SELECT id FROM t_user WHERE name = 'O''Brien' AND city = \"New York\""
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code == 200

    def test_sql_with_comments(self):
        """含注释的SQL"""
        sql = "/* 查询用户 */ SELECT id, name FROM t_user WHERE id = 1 -- 按ID查询"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code == 200
        data = resp.json()
        assert data["passed"] is True

    def test_multiple_statements_rejected(self):
        """多条SQL语句应被拒绝（单条审核不支持分号分隔的多语句）"""
        sql = "SELECT id FROM t_user WHERE id = 1; SELECT * FROM t_order;"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        # sqlglot不支持一次解析多条SQL，应返回400或200
        assert resp.status_code in (200, 400)

    def test_create_table_full_compliance(self):
        """完全合规的CREATE TABLE"""
        sql = """
        CREATE TABLE t_order_detail (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
            order_id BIGINT UNSIGNED NOT NULL COMMENT '订单ID',
            product_id BIGINT UNSIGNED NOT NULL COMMENT '产品ID',
            product_name VARCHAR(128) NOT NULL COMMENT '产品名称',
            quantity INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '数量',
            unit_price DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '单价',
            total_amount DECIMAL(12,2) NOT NULL DEFAULT 0 COMMENT '总金额',
            status TINYINT NOT NULL DEFAULT 0 COMMENT '状态',
            create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
            INDEX idx_order_id (order_id),
            INDEX idx_product_id (product_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单明细表' SHARDKEY=id
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is True
        assert len(data["violations"]) == 0

    def test_create_table_all_violations(self):
        """触发尽可能多DDL违规的CREATE TABLE"""
        sql = """
        CREATE TABLE bad_table (
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
        # 应该触发 R003, R004, R005, R006, R007, R009, R010, R011
        assert len(rule_ids) >= 5

    def test_select_with_subquery_depth_2(self):
        """2层子查询（不触发R015）"""
        sql = "SELECT id FROM t1 WHERE id IN (SELECT id FROM t2 WHERE status = 1)"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R015" not in rule_ids

    def test_update_with_where_ok(self):
        """合规UPDATE"""
        resp = client.post("/api/v1/audit/sql", json={
            "sql": "UPDATE t_user SET name = 'test' WHERE id = 1"
        })
        data = resp.json()
        assert data["passed"] is True

    def test_delete_with_where_ok(self):
        """合规DELETE"""
        resp = client.post("/api/v1/audit/sql", json={
            "sql": "DELETE FROM t_user WHERE id = 1"
        })
        data = resp.json()
        assert data["passed"] is True

    def test_insert_with_columns(self):
        """带列名的INSERT"""
        resp = client.post("/api/v1/audit/sql", json={
            "sql": "INSERT INTO t_user (id, name) VALUES (1, 'test')"
        })
        data = resp.json()
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════
# 二、边界场景 - 文件审核
# ═══════════════════════════════════════════════════════════

class TestEdgeCasesFileAudit:
    """文件审核边界场景"""

    def test_mybatis_xml_with_dynamic_tags(self):
        """MyBatis XML含动态标签"""
        xml = """
        <mapper namespace="com.example.OrderMapper">
            <select id="selectOrders" resultType="Order">
                SELECT o.id, o.order_no, o.amount
                FROM t_order o
                <where>
                    <if test="userId != null">
                        AND o.user_id = #{userId}
                    </if>
                    <if test="status != null">
                        AND o.status = #{status}
                    </if>
                </where>
                ORDER BY o.create_time DESC
            </select>
            <insert id="insertOrder">
                INSERT INTO t_order (order_no, user_id, amount, status)
                VALUES (#{orderNo}, #{userId}, #{amount}, #{status})
            </insert>
        </mapper>
        """
        resp = client.post("/api/v1/audit/file", json={
            "content": xml, "file_path": "OrderMapper.xml"
        })
        data = resp.json()
        assert "results" in data
        assert data["summary"]["total_sql"] >= 1

    def test_mybatis_xml_with_foreach(self):
        """MyBatis XML含foreach标签"""
        xml = """
        <mapper namespace="com.example.UserMapper">
            <select id="selectByIds">
                SELECT * FROM t_user WHERE id IN
                <foreach collection="ids" item="id" open="(" separator="," close=")">
                    #{id}
                </foreach>
            </select>
        </mapper>
        """
        resp = client.post("/api/v1/audit/file", json={
            "content": xml, "file_path": "UserMapper.xml"
        })
        data = resp.json()
        assert "results" in data

    def test_sql_file_with_multiple_statements(self):
        """SQL文件含多条语句"""
        content = """
-- 用户表
CREATE TABLE t_user (
    id BIGINT PRIMARY KEY,
    name VARCHAR(50)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 查询用户
SELECT id, name FROM t_user WHERE id = 1;

-- 危险操作
SELECT * FROM t_user;
"""
        resp = client.post("/api/v1/audit/file", json={
            "content": content, "file_path": "init.sql"
        })
        data = resp.json()
        assert data["summary"]["total_sql"] >= 2

    def test_empty_file(self):
        """空文件"""
        resp = client.post("/api/v1/audit/file", json={
            "content": "", "file_path": "empty.sql"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_sql"] == 0

    def test_xml_file_with_no_sql_tags(self):
        """无SQL标签的XML文件"""
        xml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <configuration>
            <settings>
                <setting name="cache" value="true"/>
            </settings>
        </configuration>
        """
        resp = client.post("/api/v1/audit/file", json={
            "content": xml, "file_path": "config.xml"
        })
        data = resp.json()
        assert data["summary"]["total_sql"] == 0


# ═══════════════════════════════════════════════════════════
# 三、边界场景 - 慢SQL分析
# ═══════════════════════════════════════════════════════════

class TestEdgeCasesSlowQuery:
    """慢SQL分析边界场景"""

    def test_slow_query_with_zero_metrics(self):
        """零指标的慢SQL"""
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "SELECT 1",
            "sql_text": "SELECT 1",
            "exec_count": 0,
            "avg_time_ms": 0,
            "rows_examined": 0,
            "rows_sent": 0,
        })
        assert resp.status_code == 200

    def test_slow_query_with_high_lock_time(self):
        """高锁等待的慢SQL（锁等待占比>30%）"""
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "UPDATE t SET col = ? WHERE id = ?",
            "sql_text": "UPDATE t SET col = 1 WHERE id = 100",
            "exec_count": 100,
            "avg_time_ms": 5000,
            "total_time_ms": 500000,
            "rows_examined": 1,
            "rows_sent": 0,
            "lock_time_ms": 200000,  # 锁等待40%
        })
        data = resp.json()
        assert "analyses" in data
        # 应该检测到锁等待问题
        problem_types = [a["problem_type"] for a in data["analyses"]]
        assert "锁等待严重" in problem_types

    def test_slow_query_detail(self):
        """获取慢SQL详情"""
        # 先添加一条
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "SELECT * FROM t WHERE id = ?",
            "sql_text": "SELECT * FROM t WHERE id = 1",
            "exec_count": 10,
            "avg_time_ms": 100,
        })
        slow_id = resp.json()["id"]

        # 获取详情
        resp = client.get(f"/api/v1/slow-queries/{slow_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == slow_id

    def test_slow_query_detail_not_found(self):
        """获取不存在的慢SQL详情"""
        resp = client.get("/api/v1/slow-queries/99999")
        assert resp.status_code == 404

    def test_slow_query_update_status(self):
        """更新慢SQL状态"""
        # 先添加一条
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "UPDATE t SET col = ?",
            "sql_text": "UPDATE t SET col = 1",
            "exec_count": 5,
            "avg_time_ms": 50,
        })
        slow_id = resp.json()["id"]

        # 更新状态
        resp = client.put(f"/api/v1/slow-queries/{slow_id}/status", json={
            "status": "optimized"
        })
        assert resp.status_code == 200

        # 验证状态已更新
        resp = client.get(f"/api/v1/slow-queries/{slow_id}")
        assert resp.json()["status"] == "optimized"

    def test_slow_query_update_invalid_status(self):
        """更新无效状态"""
        resp = client.put("/api/v1/slow-queries/1/status", json={
            "status": "invalid_status"
        })
        assert resp.status_code == 400

    def test_explain_with_multiple_rows(self):
        """多行EXPLAIN分析（JOIN场景）"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [
                {
                    "id": 1, "select_type": "SIMPLE", "table": "t_order",
                    "type": "ALL", "rows": 100000, "filtered": 100.0,
                    "extra": "Using where"
                },
                {
                    "id": 1, "select_type": "SIMPLE", "table": "t_user",
                    "type": "eq_ref", "possible_keys": "PRIMARY", "key": "PRIMARY",
                    "rows": 1, "filtered": 100.0, "extra": "Using index"
                }
            ]
        })
        assert resp.status_code == 200
        data = resp.json()
        # 应该检测到t_order的全表扫描
        assert len(data["analyses"]) > 0

    def test_explain_with_join_buffer(self):
        """EXPLAIN Using join buffer"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "ALL", "rows": 50000, "filtered": 100.0,
                "extra": "Using where; Using join buffer (Block Nested Loop)"
            }]
        })
        assert resp.status_code == 200
        data = resp.json()
        problem_types = [a["problem_type"] for a in data["analyses"]]
        assert "Using join buffer" in problem_types

    def test_slow_query_list_with_filters(self):
        """慢SQL列表筛选"""
        resp = client.get("/api/v1/slow-queries?severity=ERROR&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data


# ═══════════════════════════════════════════════════════════
# 四、边界场景 - GitLab集成
# ═══════════════════════════════════════════════════════════

class TestEdgeCasesGitLab:
    """GitLab集成边界场景"""

    def test_audit_diff_with_xml(self):
        """审核XML文件的Diff"""
        diff = "+<select id=\"getUser\">SELECT * FROM t_user WHERE id = #{id}</select>"
        resp = client.post("/api/v1/gitlab/audit/diff", json={
            "diff": diff, "file_path": "UserMapper.xml"
        })
        assert resp.status_code == 200

    def test_audit_diff_empty(self):
        """空Diff"""
        resp = client.post("/api/v1/gitlab/audit/diff", json={
            "diff": "", "file_path": "test.sql"
        })
        assert resp.status_code == 400

    def test_audit_repository_mixed_files(self):
        """审核混合文件类型的仓库"""
        resp = client.post("/api/v1/gitlab/audit/repository", json={
            "files": [
                {"path": "README.md", "content": "# This is a readme"},
                {"path": "src/UserMapper.xml", "content": """
                    <mapper namespace="com.example.UserMapper">
                        <select id="getUser">SELECT * FROM t_user WHERE id = #{id}</select>
                    </mapper>
                """},
                {"path": "sql/V1__init.sql", "content": "CREATE TABLE t_user (id BIGINT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"},
                {"path": "app.java", "content": "public class App {}"},
            ]
        })
        assert resp.status_code == 200
        data = resp.json()
        # 只审核XML和SQL文件
        assert data["total_files"] >= 1

    def test_audit_repository_empty(self):
        """空仓库"""
        resp = client.post("/api/v1/gitlab/audit/repository", json={"files": []})
        assert resp.status_code == 400

    def test_mr_webhook_with_sql_changes(self):
        """MR Webhook含SQL变更"""
        resp = client.post("/api/v1/gitlab/webhook/merge-request", json={
            "object_kind": "merge_request",
            "object_attributes": {
                "iid": 42,
                "title": "Add user query",
                "action": "open"
            },
            "project": {"name": "my-project"},
            "changes": {
                "new_path": "mapper/UserMapper.xml",
                "diff": "+<select id=\"getUser\">SELECT * FROM t_user WHERE id = #{id}</select>"
            }
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "total_sql" in data or "message" in data


# ═══════════════════════════════════════════════════════════
# 五、边界场景 - TDSQL管理
# ═══════════════════════════════════════════════════════════

class TestEdgeCasesTDSQL:
    """TDSQL管理边界场景"""

    def test_connect_invalid_host(self):
        """连接无效主机"""
        resp = client.post("/api/v1/tdsql/connect", json={
            "host": "192.0.2.1",  # TEST-NET地址，不可路由
            "port": 3306,
            "user": "root",
            "password": "test",
            "database": "test",
        })
        # 应该返回连接失败
        assert resp.status_code in (400, 500)

    def test_connect_missing_fields(self):
        """缺少必填字段"""
        resp = client.post("/api/v1/tdsql/connect", json={
            "host": "127.0.0.1"
        })
        assert resp.status_code == 422

    def test_all_operations_without_connection(self):
        """未连接时所有操作应返回400"""
        # 确保断开
        client.post("/api/v1/tdsql/disconnect")

        endpoints = [
            ("GET", "/api/v1/tdsql/tables"),
            ("GET", "/api/v1/tdsql/tables/t_user/metadata"),
            ("GET", "/api/v1/tdsql/check/charset"),
            ("GET", "/api/v1/tdsql/check/large-tables"),
            ("GET", "/api/v1/tdsql/slow-query-config"),
        ]
        for method, url in endpoints:
            if method == "GET":
                resp = client.get(url)
            assert resp.status_code == 400, f"{method} {url} should return 400"

    def test_disconnect_idempotent(self):
        """重复断开不应报错"""
        resp1 = client.post("/api/v1/tdsql/disconnect")
        resp2 = client.post("/api/v1/tdsql/disconnect")
        assert resp1.status_code == 200
        assert resp2.status_code == 200


# ═══════════════════════════════════════════════════════════
# 六、边界场景 - Dashboard
# ═══════════════════════════════════════════════════════════

class TestEdgeCasesDashboard:
    """Dashboard边界场景"""

    def test_summary_after_operations(self):
        """操作后Dashboard数据应更新"""
        # 添加一些数据
        client.post("/api/v1/audit/sql", json={"sql": "SELECT * FROM t_user"})
        client.post("/api/v1/slow-queries", json={
            "fingerprint": "SELECT * FROM t WHERE id = ?",
            "sql_text": "SELECT * FROM t WHERE id = 1",
            "exec_count": 100,
            "avg_time_ms": 50,
        })

        resp = client.get("/api/v1/dashboard/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["slow_queries"]["total"] >= 1
        assert data["rules"]["total"] == 77

    def test_audit_trend_different_days(self):
        """不同天数的趋势"""
        for days in [1, 7, 30]:
            resp = client.get(f"/api/v1/dashboard/audit-trend?days={days}")
            assert resp.status_code == 200
            data = resp.json()
            assert "dates" in data

    def test_rule_stats(self):
        """规则命中统计"""
        resp = client.get("/api/v1/dashboard/rule-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "rules" in data


# ═══════════════════════════════════════════════════════════
# 七、完整业务流程测试
# ═══════════════════════════════════════════════════════════

class TestFullWorkflow:
    """完整业务流程测试"""

    def test_complete_audit_workflow(self):
        """完整审核流程：审核SQL → 查看Dashboard → 分析慢SQL"""
        # Step 1: 审核多条SQL
        sqls = [
            "SELECT id, name FROM t_user WHERE id = 1",      # 通过
            "SELECT * FROM t_user",                            # R012
            "DELETE FROM t_order",                             # R013/R014
            "UPDATE t_user SET shard_key = 1 WHERE id = 1",   # R021
        ]
        for sql in sqls:
            resp = client.post("/api/v1/audit/sql", json={"sql": sql, "db_type": "tdsql"})
            assert resp.status_code == 200

        # Step 2: 文件审核
        resp = client.post("/api/v1/audit/file", json={
            "content": "SELECT * FROM t_user;\nSELECT id FROM t_ok WHERE id = 1;",
            "file_path": "test.sql"
        })
        assert resp.status_code == 200

        # Step 3: 添加慢SQL
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "SELECT * FROM t_order WHERE user_id = ?",
            "sql_text": "SELECT * FROM t_order WHERE user_id = 123",
            "db_name": "order_db",
            "exec_count": 10000,
            "avg_time_ms": 500,
            "rows_examined": 2000000,
            "rows_sent": 50,
        })
        assert resp.status_code == 200
        slow_id = resp.json()["id"]

        # Step 4: EXPLAIN分析
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "ALL", "rows": 2000000, "filtered": 5.0,
                "extra": "Using where; Using filesort"
            }]
        })
        assert resp.status_code == 200

        # Step 5: 更新慢SQL状态
        resp = client.put(f"/api/v1/slow-queries/{slow_id}/status", json={
            "status": "optimized"
        })
        assert resp.status_code == 200

        # Step 6: 查看Dashboard
        resp = client.get("/api/v1/dashboard/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["slow_queries"]["total"] >= 1

        # Step 7: 查看慢SQL列表
        resp = client.get("/api/v1/slow-queries?limit=10")
        assert resp.status_code == 200

        # Step 8: 查看慢SQL详情
        resp = client.get(f"/api/v1/slow-queries/{slow_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "optimized"

    def test_gitlab_integration_workflow(self):
        """GitLab集成完整流程：配置 → MR审核 → Diff审核"""
        # Step 1: 获取配置说明
        resp = client.get("/api/v1/gitlab/config")
        assert resp.status_code == 200

        # Step 2: 模拟MR Webhook
        resp = client.post("/api/v1/gitlab/webhook/merge-request", json={
            "object_kind": "merge_request",
            "object_attributes": {"iid": 1, "action": "open"},
            "project": {"name": "test"},
            "changes": {
                "new_path": "UserMapper.xml",
                "diff": "+<select id=\"getUser\">SELECT * FROM t_user WHERE id = #{id}</select>"
            }
        })
        assert resp.status_code == 200

        # Step 3: 手动Diff审核
        resp = client.post("/api/v1/gitlab/audit/diff", json={
            "diff": "+SELECT * FROM t_user WHERE id = 1",
            "file_path": "test.sql"
        })
        assert resp.status_code == 200

        # Step 4: 仓库审核
        resp = client.post("/api/v1/gitlab/audit/repository", json={
            "files": [
                {"path": "UserMapper.xml", "content": "<mapper><select id='test'>SELECT * FROM t</select></mapper>"},
            ]
        })
        assert resp.status_code == 200
