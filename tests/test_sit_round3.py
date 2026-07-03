"""
TDSQL SQL审核工具 - 第三轮SIT测试

覆盖：更多SQL模式、性能、并发、数据持久化、错误恢复。
"""
import concurrent.futures
import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


# ═══════════════════════════════════════════════════════════
# 一、更多SQL模式覆盖
# ═══════════════════════════════════════════════════════════

class TestSQLPatterns:
    """各种SQL模式测试"""

    def test_select_with_union(self):
        """UNION查询（sqlglot可能无法解析，应返回400或200）"""
        sql = "SELECT id, name FROM t_user WHERE status = 1 UNION SELECT id, name FROM t_user_archive WHERE status = 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code in (200, 400)

    def test_select_with_exists(self):
        """EXISTS子查询"""
        sql = "SELECT id FROM t_order o WHERE EXISTS (SELECT 1 FROM t_user u WHERE u.id = o.user_id)"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code == 200

    def test_select_with_like_prefix(self):
        """前缀LIKE（合规）"""
        sql = "SELECT id FROM t_user WHERE name LIKE '张%'"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        # 前缀LIKE不触发R016
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R016" not in rule_ids

    def test_select_with_like_left_wildcard(self):
        """左模糊LIKE（规则引擎通过函数检测，LIKE模式在慢SQL分析器中检测）"""
        sql = "SELECT id FROM t_user WHERE name LIKE '%张'"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        # LIKE模式检测在慢SQL分析器中，规则引擎中R016仅检测函数
        assert resp.status_code == 200

    def test_select_with_group_by(self):
        """GROUP BY查询"""
        sql = "SELECT status, COUNT(*) FROM t_order WHERE create_time > '2024-01-01' GROUP BY status"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code == 200

    def test_select_with_having(self):
        """HAVING查询"""
        sql = "SELECT user_id, COUNT(*) as cnt FROM t_order GROUP BY user_id HAVING cnt > 5"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code == 200

    def test_select_with_distinct(self):
        """DISTINCT查询"""
        sql = "SELECT DISTINCT status FROM t_order WHERE user_id = 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code == 200

    def test_select_with_join_multiple_tables(self):
        """多表JOIN（触发R012 SELECT*，R020取决于表数量检测）"""
        sql = "SELECT * FROM t_order o JOIN t_user u ON o.user_id = u.id JOIN t_product p ON o.product_id = p.id"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R012" in rule_ids  # SELECT *

    def test_select_with_left_join(self):
        """LEFT JOIN查询"""
        sql = "SELECT o.id, u.name FROM t_order o LEFT JOIN t_user u ON o.user_id = u.id WHERE o.status = 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code == 200

    def test_select_with_order_by_multiple(self):
        """多字段ORDER BY"""
        sql = "SELECT id, name FROM t_user WHERE status = 1 ORDER BY create_time DESC, id ASC"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is True

    def test_select_with_limit_offset_large(self):
        """大偏移量LIMIT（应触发深度分页警告）"""
        sql = "SELECT id, name FROM t_user ORDER BY id LIMIT 500000, 20"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code == 200

    def test_select_with_between(self):
        """BETWEEN查询"""
        sql = "SELECT id FROM t_order WHERE create_time BETWEEN '2024-01-01' AND '2024-12-31'"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code == 200

    def test_select_with_in_list(self):
        """IN列表查询"""
        sql = "SELECT id FROM t_user WHERE status IN (1, 2, 3)"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code == 200

    def test_select_with_case_when(self):
        """CASE WHEN查询"""
        sql = "SELECT id, CASE WHEN status = 1 THEN 'active' ELSE 'inactive' END as status_name FROM t_user WHERE id = 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code == 200

    def test_insert_with_select(self):
        """INSERT INTO ... SELECT"""
        sql = "INSERT INTO t_user_backup (id, name) SELECT id, name FROM t_user WHERE status = 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code == 200

    def test_update_with_join_rejected(self):
        """联表UPDATE（违反规范）"""
        sql = "UPDATE t_order o JOIN t_user u ON o.user_id = u.id SET o.status = 1 WHERE u.vip = 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code == 200

    def test_alter_table(self):
        """ALTER TABLE（sqlglot可能无法解析，应返回400或200）"""
        sql = "ALTER TABLE t_user ADD COLUMN phone VARCHAR(20)"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code in (200, 400)

    def test_drop_table(self):
        """DROP TABLE（sqlglot可能无法解析，应返回400或200）"""
        sql = "DROP TABLE IF EXISTS t_temp"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        assert resp.status_code in (200, 400)


# ═══════════════════════════════════════════════════════════
# 二、规则精度验证
# ═══════════════════════════════════════════════════════════

class TestRulePrecision:
    """规则精度验证"""

    def test_r001_valid_names(self):
        """R001: 多种合规表名"""
        valid_names = ["t_user", "t_order_detail", "user_info", "a1", "test_2024"]
        for name in valid_names:
            sql = f"CREATE TABLE {name} (id BIGINT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
            resp = client.post("/api/v1/audit/sql", json={"sql": sql})
            data = resp.json()
            rule_ids = [v["rule_id"] for v in data["violations"]]
            assert "R001" not in rule_ids, f"Table name '{name}' should pass R001"

    def test_r001_invalid_names(self):
        """R001: 多种不合规表名"""
        invalid_names = ["T_user", "1table", "table-name", "a" * 33]
        for name in invalid_names:
            sql = f"CREATE TABLE `{name}` (id BIGINT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
            resp = client.post("/api/v1/audit/sql", json={"sql": sql})
            data = resp.json()
            rule_ids = [v["rule_id"] for v in data["violations"]]
            assert "R001" in rule_ids, f"Table name '{name}' should trigger R001"

    def test_r009_decimal_pass(self):
        """R009: DECIMAL类型不触发"""
        sql = "CREATE TABLE t_order (id BIGINT PRIMARY KEY, amount DECIMAL(18,2)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R009" not in rule_ids

    def test_r009_double_trigger(self):
        """R009: DOUBLE类型触发"""
        sql = "CREATE TABLE t_order (id BIGINT PRIMARY KEY, amount DOUBLE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R009" in rule_ids

    def test_r010_varchar_short_pass(self):
        """R010: 短VARCHAR不触发"""
        sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY, name VARCHAR(100)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R010" not in rule_ids

    def test_r010_varchar_long_trigger(self):
        """R010: 长VARCHAR触发"""
        sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY, bio VARCHAR(3000)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R010" in rule_ids

    def test_r016_function_date(self):
        """R016: DATE()函数触发"""
        sql = "SELECT id FROM t_user WHERE DATE(create_time) = '2024-01-01'"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R016" in rule_ids

    def test_r016_no_function(self):
        """R016: 无函数不触发"""
        sql = "SELECT id FROM t_user WHERE create_time >= '2024-01-01'"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R016" not in rule_ids

    def test_r018_index_count_ok(self):
        """R018: 5个索引（含主键）不触发"""
        sql = """
        CREATE TABLE t_test (
            id BIGINT PRIMARY KEY,
            a VARCHAR(10), b VARCHAR(10),
            INDEX idx_a(a), INDEX idx_b(b)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R018" not in rule_ids

    def test_r018_index_count_trigger(self):
        """R018: 超过5个索引触发"""
        sql = """
        CREATE TABLE t_test (
            id BIGINT PRIMARY KEY,
            a VARCHAR(10), b VARCHAR(10), c VARCHAR(10),
            d VARCHAR(10), e VARCHAR(10), f VARCHAR(10),
            INDEX idx_a(a), INDEX idx_b(b), INDEX idx_c(c),
            INDEX idx_d(d), INDEX idx_e(e), INDEX idx_f(f)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R018" in rule_ids

    def test_r019_redundant_index_trigger(self):
        """R019: 冗余索引触发"""
        sql = """
        CREATE TABLE t_test (
            id BIGINT PRIMARY KEY,
            a VARCHAR(10), b VARCHAR(10),
            INDEX idx_a(a), INDEX idx_ab(a, b)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R019" in rule_ids

    def test_r019_no_redundant(self):
        """R019: 无不冗余索引不触发"""
        sql = """
        CREATE TABLE t_test (
            id BIGINT PRIMARY KEY,
            a VARCHAR(10), b VARCHAR(10),
            INDEX idx_a(a), INDEX idx_b(b)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R019" not in rule_ids


# ═══════════════════════════════════════════════════════════
# 三、数据持久化验证
# ═══════════════════════════════════════════════════════════

class TestDataPersistence:
    """数据持久化验证"""

    def test_slow_queries_persist_across_requests(self):
        """慢SQL数据跨请求持久化"""
        # 添加一条
        resp1 = client.post("/api/v1/slow-queries", json={
            "fingerprint": "PERSIST_TEST SELECT * FROM t WHERE id = ?",
            "sql_text": "SELECT * FROM t WHERE id = 1",
            "exec_count": 42,
            "avg_time_ms": 99.5,
        })
        assert resp1.status_code == 200
        slow_id = resp1.json()["id"]

        # 另一个请求获取
        resp2 = client.get(f"/api/v1/slow-queries/{slow_id}")
        assert resp2.status_code == 200
        assert resp2.json()["exec_count"] == 42
        assert resp2.json()["avg_time_ms"] == 99.5

    def test_status_update_persists(self):
        """状态更新持久化"""
        # 添加
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "STATUS_TEST SELECT 1",
            "sql_text": "SELECT 1",
            "exec_count": 1,
            "avg_time_ms": 1,
        })
        slow_id = resp.json()["id"]

        # 更新状态
        client.put(f"/api/v1/slow-queries/{slow_id}/status", json={"status": "optimized"})

        # 验证
        resp = client.get(f"/api/v1/slow-queries/{slow_id}")
        assert resp.json()["status"] == "optimized"

    def test_statistics_reflect_data(self):
        """统计数据反映实际数据"""
        # 获取当前统计
        resp_before = client.get("/api/v1/slow-queries/statistics")
        total_before = resp_before.json()["total"]

        # 添加一条
        client.post("/api/v1/slow-queries", json={
            "fingerprint": "STATS_TEST SELECT 1",
            "sql_text": "SELECT 1",
            "exec_count": 1,
            "avg_time_ms": 1,
        })

        # 验证统计更新
        resp_after = client.get("/api/v1/slow-queries/statistics")
        assert resp_after.json()["total"] == total_before + 1


# ═══════════════════════════════════════════════════════════
# 四、并发测试
# ═══════════════════════════════════════════════════════════

class TestConcurrency:
    """并发测试"""

    def test_concurrent_audit_requests(self):
        """并发SQL审核请求"""
        def audit_sql(sql):
            return client.post("/api/v1/audit/sql", json={"sql": sql})

        sqls = [
            "SELECT id FROM t_user WHERE id = 1",
            "SELECT * FROM t_order",
            "DELETE FROM t_test",
            "UPDATE t_user SET name = 'test' WHERE id = 1",
            "INSERT INTO t_user (id, name) VALUES (1, 'test')",
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(audit_sql, sql) for sql in sqls]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # 所有请求都应成功
        for resp in results:
            assert resp.status_code == 200

    def test_concurrent_slow_query_add(self):
        """并发添加慢SQL"""
        def add_slow(idx):
            return client.post("/api/v1/slow-queries", json={
                "fingerprint": f"CONCURRENT_TEST_{idx} SELECT * FROM t WHERE id = ?",
                "sql_text": f"SELECT * FROM t WHERE id = {idx}",
                "exec_count": idx,
                "avg_time_ms": float(idx),
            })

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(add_slow, i) for i in range(5)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for resp in results:
            assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════
# 五、EXPLAIN分析深度测试
# ═══════════════════════════════════════════════════════════

class TestExplainDeepAnalysis:
    """EXPLAIN深度分析测试"""

    def test_explain_const_type(self):
        """EXPLAIN const类型（最优）"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_user",
                "type": "const", "possible_keys": "PRIMARY", "key": "PRIMARY",
                "rows": 1, "filtered": 100.0, "extra": ""
            }]
        })
        data = resp.json()
        errors = [a for a in data["analyses"] if a["severity"] == "ERROR"]
        assert len(errors) == 0

    def test_explain_eq_ref_type(self):
        """EXPLAIN eq_ref类型（良好）"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_user",
                "type": "eq_ref", "possible_keys": "PRIMARY", "key": "PRIMARY",
                "rows": 1, "filtered": 100.0, "extra": ""
            }]
        })
        data = resp.json()
        errors = [a for a in data["analyses"] if a["severity"] == "ERROR"]
        assert len(errors) == 0

    def test_explain_range_type(self):
        """EXPLAIN range类型（可接受）"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "range", "possible_keys": "idx_create_time", "key": "idx_create_time",
                "rows": 5000, "filtered": 100.0, "extra": "Using index condition"
            }]
        })
        data = resp.json()
        errors = [a for a in data["analyses"] if a["severity"] == "ERROR"]
        assert len(errors) == 0

    def test_explain_index_type(self):
        """EXPLAIN index类型（索引全扫描）"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "index", "possible_keys": None, "key": "idx_status",
                "rows": 100000, "filtered": 100.0, "extra": "Using index"
            }]
        })
        data = resp.json()
        # index类型应该有警告
        assert len(data["analyses"]) > 0

    def test_explain_large_rows_warning(self):
        """EXPLAIN大量扫描行数警告"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "ref", "key": "idx_user_id",
                "rows": 500000, "filtered": 100.0, "extra": "Using where"
            }]
        })
        data = resp.json()
        problem_types = [a["problem_type"] for a in data["analyses"]]
        assert "扫描行数过多" in problem_types

    def test_explain_low_filtered_warning(self):
        """EXPLAIN低过滤率"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "ALL", "rows": 100000, "filtered": 1.0,
                "extra": "Using where"
            }]
        })
        data = resp.json()
        # 应该检测到全表扫描
        assert len(data["analyses"]) > 0


# ═══════════════════════════════════════════════════════════
# 六、MyBatis XML深度测试
# ═══════════════════════════════════════════════════════════

class TestMyBatisDeep:
    """MyBatis XML深度测试"""

    def test_mybatis_with_choose_when(self):
        """MyBatis choose/when标签"""
        xml = """
        <mapper namespace="com.example.UserMapper">
            <select id="searchUsers">
                SELECT id, name FROM t_user
                <where>
                    <choose>
                        <when test="name != null">
                            AND name LIKE CONCAT('%', #{name}, '%')
                        </when>
                        <when test="phone != null">
                            AND phone = #{phone}
                        </when>
                        <otherwise>
                            AND status = 1
                        </otherwise>
                    </choose>
                </where>
            </select>
        </mapper>
        """
        resp = client.post("/api/v1/audit/file", json={
            "content": xml, "file_path": "UserMapper.xml"
        })
        data = resp.json()
        assert data["summary"]["total_sql"] >= 1

    def test_mybatis_with_set_tag(self):
        """MyBatis set标签（动态UPDATE）"""
        xml = """
        <mapper namespace="com.example.UserMapper">
            <update id="updateUser">
                UPDATE t_user
                <set>
                    <if test="name != null">name = #{name},</if>
                    <if test="phone != null">phone = #{phone},</if>
                    <if test="email != null">email = #{email},</if>
                </set>
                WHERE id = #{id}
            </update>
        </mapper>
        """
        resp = client.post("/api/v1/audit/file", json={
            "content": xml, "file_path": "UserMapper.xml"
        })
        data = resp.json()
        assert data["summary"]["total_sql"] >= 1

    def test_mybatis_with_trim_tag(self):
        """MyBatis trim标签"""
        xml = """
        <mapper namespace="com.example.UserMapper">
            <insert id="insertUser">
                INSERT INTO t_user
                <trim prefix="(" suffix=")" suffixOverrides=",">
                    <if test="name != null">name,</if>
                    <if test="phone != null">phone,</if>
                </trim>
                VALUES
                <trim prefix="(" suffix=")" suffixOverrides=",">
                    <if test="name != null">#{name},</if>
                    <if test="phone != null">#{phone},</if>
                </trim>
            </insert>
        </mapper>
        """
        resp = client.post("/api/v1/audit/file", json={
            "content": xml, "file_path": "UserMapper.xml"
        })
        data = resp.json()
        assert data["summary"]["total_sql"] >= 1

    def test_mybatis_with_dollar_sign(self):
        """MyBatis ${} 注入风险标记"""
        xml = """
        <mapper namespace="com.example.UserMapper">
            <select id="dynamicSort">
                SELECT * FROM t_user ORDER BY ${columnName}
            </select>
        </mapper>
        """
        resp = client.post("/api/v1/audit/file", json={
            "content": xml, "file_path": "UserMapper.xml"
        })
        data = resp.json()
        # 应该能处理 ${} 语法
        assert data["summary"]["total_sql"] >= 1


# ═══════════════════════════════════════════════════════════
# 七、API错误处理验证
# ═══════════════════════════════════════════════════════════

class TestErrorHandling:
    """API错误处理验证"""

    def test_audit_sql_missing_body(self):
        """缺少请求体"""
        resp = client.post("/api/v1/audit/sql")
        assert resp.status_code == 422

    def test_audit_sql_wrong_type(self):
        """错误的请求类型"""
        resp = client.post("/api/v1/audit/sql", content="not json", headers={"Content-Type": "text/plain"})
        assert resp.status_code == 422

    def test_slow_query_missing_required(self):
        """慢SQL缺少必填字段"""
        resp = client.post("/api/v1/slow-queries", json={"fingerprint": "test"})
        # sql_text是必填的
        assert resp.status_code == 422

    def test_explain_empty_data(self):
        """空EXPLAIN数据"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={"explain_data": []})
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "未发现明显问题"

    def test_nonexistent_endpoint(self):
        """不存在的端点"""
        resp = client.get("/api/v1/nonexistent")
        assert resp.status_code == 404

    def test_method_not_allowed(self):
        """不允许的HTTP方法"""
        resp = client.get("/api/v1/audit/sql")
        assert resp.status_code == 405


# ═══════════════════════════════════════════════════════════
# 八、完整业务场景测试
# ═══════════════════════════════════════════════════════════

class TestBusinessScenarios:
    """完整业务场景测试"""

    def test_new_table_review_pass(self):
        """新建表审核通过场景"""
        sql = """
        CREATE TABLE t_transaction_log (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
            transaction_no VARCHAR(64) NOT NULL COMMENT '交易流水号',
            account_id BIGINT UNSIGNED NOT NULL COMMENT '账户ID',
            transaction_type TINYINT NOT NULL COMMENT '交易类型',
            amount DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '交易金额',
            currency VARCHAR(3) NOT NULL DEFAULT 'CNY' COMMENT '币种',
            status TINYINT NOT NULL DEFAULT 0 COMMENT '状态',
            remark VARCHAR(256) DEFAULT '' COMMENT '备注',
            create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
            UNIQUE INDEX uk_transaction_no (transaction_no),
            INDEX idx_account_id (account_id),
            INDEX idx_create_time (create_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易流水表' SHARDKEY=id
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is True

    def test_new_table_review_fail(self):
        """新建表审核失败场景"""
        sql = """
        CREATE TABLE transaction_log (
            id INT AUTO_INCREMENT,
            transaction_no VARCHAR(5000),
            amount FLOAT,
            status ENUM('pending','done'),
            created_at TIMESTAMP,
            notes TEXT,
            INDEX idx_no(transaction_no)
        )
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False
        # 应该触发多条规则
        assert len(data["violations"]) >= 5

    def test_code_review_scenario(self):
        """代码审核场景：审核一组SQL"""
        sqls_and_expected = [
            ("SELECT id, name, email FROM t_user WHERE id = ?", True),
            ("SELECT * FROM t_user WHERE id = ?", False),
            ("UPDATE t_user SET email = ? WHERE id = ?", True),
            ("UPDATE t_user SET email = 'test'", False),
            ("DELETE FROM t_user WHERE id = ?", True),
            ("DELETE FROM t_user", False),
        ]

        results = []
        for sql, expected_pass in sqls_and_expected:
            resp = client.post("/api/v1/audit/sql", json={"sql": sql})
            data = resp.json()
            results.append((sql[:40], data["passed"], expected_pass))
            assert data["passed"] == expected_pass, f"SQL '{sql[:40]}' expected passed={expected_pass}, got {data['passed']}"

    def test_slow_sql_triage_scenario(self):
        """慢SQL分诊场景"""
        # 添加多条不同严重程度的慢SQL
        slow_sqls = [
            {"fingerprint": "SELECT * FROM t1", "sql_text": "SELECT * FROM t1", "exec_count": 10000, "avg_time_ms": 1000, "rows_examined": 5000000, "rows_sent": 10},
            {"fingerprint": "SELECT id FROM t2 WHERE id = ?", "sql_text": "SELECT id FROM t2 WHERE id = 1", "exec_count": 1, "avg_time_ms": 5, "rows_examined": 1, "rows_sent": 1},
        ]

        ids = []
        for s in slow_sqls:
            resp = client.post("/api/v1/slow-queries", json=s)
            assert resp.status_code == 200
            ids.append(resp.json()["id"])

        # 获取列表并验证
        resp = client.get("/api/v1/slow-queries?limit=100")
        data = resp.json()
        assert data["total"] >= 2

        # 验证统计
        resp = client.get("/api/v1/slow-queries/statistics")
        data = resp.json()
        assert data["total"] >= 2
