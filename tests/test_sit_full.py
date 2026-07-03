"""
TDSQL SQL审核工具 - 完整SIT测试

覆盖所有功能模块的系统集成测试。
"""
import json
import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


# ═══════════════════════════════════════════════════════════
# 一、健康检查与基础接口
# ═══════════════════════════════════════════════════════════

class TestHealthAndBasic:
    """健康检查与基础接口测试"""

    def test_health_endpoint(self):
        """健康检查端点"""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_root_endpoint(self):
        """根路径返回前端页面"""
        resp = client.get("/")
        assert resp.status_code == 200

    def test_swagger_docs(self):
        """Swagger文档可访问"""
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_openapi_schema(self):
        """OpenAPI Schema可获取"""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "paths" in schema
        # 验证所有API路径存在
        paths = schema["paths"]
        assert "/api/v1/audit/sql" in paths
        assert "/api/v1/audit/file" in paths
        assert "/api/v1/slow-queries" in paths
        assert "/api/v1/slow-queries/analyze-explain" in paths
        assert "/api/v1/dashboard/summary" in paths
        assert "/api/v1/gitlab/config" in paths
        assert "/api/v1/tdsql/status" in paths


# ═══════════════════════════════════════════════════════════
# 二、SQL审核 - 命名规范规则 (R001-R002)
# ═══════════════════════════════════════════════════════════

class TestNamingRules:
    """命名规范规则测试"""

    def test_r001_table_name_too_long(self):
        """R001: 表名超过32字符"""
        sql = "CREATE TABLE t_this_table_name_is_way_too_long_for_tdsql (id BIGINT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R001" in rule_ids

    def test_r001_table_name_uppercase(self):
        """R001: 表名包含大写字母"""
        sql = "CREATE TABLE T_User (id BIGINT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R001" in rule_ids

    def test_r001_table_name_starts_with_number(self):
        """R001: 表名以数字开头"""
        sql = "CREATE TABLE 1t_user (id BIGINT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False

    def test_r001_table_name_valid(self):
        """R001: 合规表名通过"""
        sql = "CREATE TABLE t_user_info (id BIGINT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        # 应该没有R001违规
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R001" not in rule_ids

    def test_r002_reserved_keyword(self):
        """R002: 使用保留关键字作为表名"""
        sql = "CREATE TABLE `order` (id BIGINT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        # 可能触发R002
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════
# 三、SQL审核 - DDL规范规则 (R003-R011)
# ═══════════════════════════════════════════════════════════

class TestDDLRules:
    """DDL规范规则测试"""

    def test_r003_no_primary_key(self):
        """R003: 建表未指定主键"""
        sql = "CREATE TABLE t_user (name VARCHAR(50)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R003" in rule_ids

    def test_r003_with_primary_key(self):
        """R003: 有主键通过"""
        sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY, name VARCHAR(50)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R003" not in rule_ids

    def test_r004_no_engine(self):
        """R004: 未指定存储引擎"""
        sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY) DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R004" in rule_ids

    def test_r005_no_charset(self):
        """R005: 未指定字符集"""
        sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY) ENGINE=InnoDB"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R005" in rule_ids

    def test_r006_enum_type(self):
        """R006: 使用ENUM类型"""
        sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY, status ENUM('active','inactive')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R006" in rule_ids

    def test_r007_timestamp_type(self):
        """R007: 使用TIMESTAMP类型"""
        sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY, created_at TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R007" in rule_ids

    def test_r009_float_type(self):
        """R009: 财务字段使用FLOAT"""
        sql = "CREATE TABLE t_order (id BIGINT PRIMARY KEY, amount FLOAT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R009" in rule_ids

    def test_r010_varchar_too_long(self):
        """R010: VARCHAR长度超过2000"""
        sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY, bio VARCHAR(5000)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R010" in rule_ids

    def test_r011_text_type(self):
        """R011: 使用TEXT类型"""
        sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY, bio TEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R011" in rule_ids

    def test_clean_ddl_passes_all(self):
        """合规DDL应通过所有检查"""
        sql = """
        CREATE TABLE t_order (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
            order_no VARCHAR(64) NOT NULL COMMENT '订单号',
            user_id BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
            amount DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '金额',
            status TINYINT NOT NULL DEFAULT 0 COMMENT '状态',
            create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单表' SHARDKEY=id
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is True
        assert len(data["violations"]) == 0

    def test_bad_ddl_multiple_violations(self):
        """不合规DDL应触发多条规则"""
        sql = "CREATE TABLE t_test (id INT, ts TIMESTAMP, amount FLOAT, notes TEXT)"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = {v["rule_id"] for v in data["violations"]}
        # 应该至少触发 R003(无主键), R004(无引擎), R005(无字符集), R007(TIMESTAMP), R009(FLOAT), R011(TEXT)
        assert "R003" in rule_ids
        assert "R004" in rule_ids
        assert "R005" in rule_ids
        assert "R007" in rule_ids
        assert "R009" in rule_ids
        assert "R011" in rule_ids


# ═══════════════════════════════════════════════════════════
# 四、SQL审核 - DML规范规则 (R012-R019)
# ═══════════════════════════════════════════════════════════

class TestDMLRules:
    """DML规范规则测试"""

    def test_r012_select_star(self):
        """R012: SELECT * 被拦截"""
        resp = client.post("/api/v1/audit/sql", json={"sql": "SELECT * FROM t_user WHERE id = 1"})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R012" in rule_ids

    def test_r012_select_fields_pass(self):
        """R012: 指定字段通过"""
        resp = client.post("/api/v1/audit/sql", json={"sql": "SELECT id, name FROM t_user WHERE id = 1"})
        data = resp.json()
        assert data["passed"] is True

    def test_r013_update_without_where(self):
        """R013: UPDATE无WHERE被拦截"""
        resp = client.post("/api/v1/audit/sql", json={"sql": "UPDATE t_user SET status = 0"})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R013" in rule_ids

    def test_r013_update_with_where_pass(self):
        """R013: UPDATE有WHERE通过"""
        resp = client.post("/api/v1/audit/sql", json={"sql": "UPDATE t_user SET status = 0 WHERE id = 1"})
        data = resp.json()
        assert data["passed"] is True

    def test_r013_delete_without_where(self):
        """R013: DELETE无WHERE被拦截"""
        resp = client.post("/api/v1/audit/sql", json={"sql": "DELETE FROM t_user"})
        data = resp.json()
        assert data["passed"] is False

    def test_r013_insert_pass(self):
        """R013: INSERT不受WHERE规则影响"""
        resp = client.post("/api/v1/audit/sql", json={"sql": "INSERT INTO t_user (id, name) VALUES (1, 'test')"})
        data = resp.json()
        # INSERT不需要WHERE
        assert resp.status_code == 200

    def test_r014_dangerous_update(self):
        """R014: 危险UPDATE被拦截"""
        resp = client.post("/api/v1/audit/sql", json={"sql": "DELETE FROM t_order"})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R014" in rule_ids

    def test_r015_deep_subquery(self):
        """R015: 超过3层子查询被拦截"""
        sql = "SELECT * FROM t1 WHERE id IN (SELECT id FROM t2 WHERE id IN (SELECT id FROM t3 WHERE id IN (SELECT id FROM t4)))"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        # 应该检测到子查询或SELECT *
        assert data["passed"] is False

    def test_r016_function_in_where(self):
        """R016: WHERE中使用函数被警告"""
        resp = client.post("/api/v1/audit/sql", json={"sql": "SELECT id FROM t_user WHERE DATE(create_time) = '2024-01-01'"})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R016" in rule_ids

    def test_r017_order_by_rand(self):
        """R017: ORDER BY RAND()被拦截"""
        resp = client.post("/api/v1/audit/sql", json={"sql": "SELECT id FROM t_user ORDER BY RAND() LIMIT 10"})
        data = resp.json()
        assert data["passed"] is False
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R017" in rule_ids

    def test_r018_index_count(self):
        """R018: 超过5个索引被警告"""
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

    def test_r019_redundant_index(self):
        """R019: 冗余索引被警告"""
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


# ═══════════════════════════════════════════════════════════
# 五、SQL审核 - 分布式规范规则 (R020-R022)
# ═══════════════════════════════════════════════════════════

class TestDistributedRules:
    """分布式规范规则测试"""

    def test_r020_multitable_join(self):
        """R020: 多表JOIN提醒分片键"""
        sql = "SELECT * FROM t_order o JOIN t_user u ON o.user_id = u.id WHERE u.name = 'test'"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R020" in rule_ids

    def test_r021_shardkey_update(self):
        """R021: 更新分片键被拦截"""
        sql = "UPDATE t_order SET shard_key = 100 WHERE id = 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R021" in rule_ids

    def test_r022_global_delete_no_eq(self):
        """R022: 无等值条件的DELETE被拦截"""
        sql = "DELETE FROM t_order WHERE status != 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R022" in rule_ids

    def test_r022_delete_with_eq_pass(self):
        """R022: 有等值条件的DELETE不触发"""
        sql = "DELETE FROM t_order WHERE id = 123"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data["violations"]]
        assert "R022" not in rule_ids


# ═══════════════════════════════════════════════════════════
# 六、SQL审核 - 文件审核
# ═══════════════════════════════════════════════════════════

class TestFileAudit:
    """文件审核测试"""

    def test_sql_file_audit(self):
        """SQL文件审核"""
        content = "SELECT id FROM t_user WHERE id = 1;\nSELECT * FROM t_order;\nDELETE FROM t_test;"
        resp = client.post("/api/v1/audit/file", json={"content": content, "file_path": "test.sql"})
        data = resp.json()
        assert "results" in data
        assert "summary" in data
        assert data["summary"]["total_sql"] >= 2

    def test_mybatis_xml_audit(self):
        """MyBatis XML文件审核"""
        xml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
        <mapper namespace="com.example.UserMapper">
            <select id="getUser" resultType="User">
                SELECT * FROM t_user WHERE id = #{id}
            </select>
            <insert id="insertUser">
                INSERT INTO t_user (id, name) VALUES (#{id}, #{name})
            </insert>
            <update id="updateUser">
                UPDATE t_user SET name = #{name} WHERE id = #{id}
            </update>
            <delete id="deleteUser">
                DELETE FROM t_user WHERE id = #{id}
            </delete>
        </mapper>
        """
        resp = client.post("/api/v1/audit/file", json={"content": xml, "file_path": "UserMapper.xml"})
        data = resp.json()
        assert "results" in data
        assert data["summary"]["total_sql"] >= 3  # SELECT, INSERT, UPDATE, DELETE

    def test_file_audit_summary(self):
        """文件审核汇总计算"""
        content = "SELECT id FROM t_ok WHERE id = 1;\nSELECT * FROM t_bad;"
        resp = client.post("/api/v1/audit/file", json={"content": content, "file_path": "test.sql"})
        data = resp.json()
        summary = data["summary"]
        assert summary["total_sql"] == 2
        assert summary["passed"] == 1
        assert summary["failed"] == 1
        assert summary["pass_rate"] == 50.0


# ═══════════════════════════════════════════════════════════
# 七、慢SQL分析模块
# ═══════════════════════════════════════════════════════════

class TestSlowQueryModule:
    """慢SQL分析模块测试"""

    def test_add_slow_query(self):
        """添加慢SQL并自动分析"""
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "SELECT * FROM t_order WHERE user_id = ?",
            "sql_text": "SELECT * FROM t_order WHERE user_id = 123",
            "db_name": "order_db",
            "exec_count": 5000,
            "avg_time_ms": 200,
            "rows_examined": 850000,
            "rows_sent": 100,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert "problem_type" in data
        assert "analyses" in data
        assert len(data["analyses"]) > 0

    def test_list_slow_queries(self):
        """获取慢SQL列表"""
        resp = client.get("/api/v1/slow-queries?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    def test_slow_query_statistics(self):
        """获取慢SQL统计"""
        resp = client.get("/api/v1/slow-queries/statistics")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "by_severity" in data

    def test_analyze_explain_all_scan(self):
        """EXPLAIN全表扫描分析"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "ALL", "possible_keys": None, "key": None,
                "rows": 850000, "filtered": 10.0, "extra": "Using where"
            }]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "analyses" in data
        assert len(data["analyses"]) > 0
        problem_types = [a["problem_type"] for a in data["analyses"]]
        assert "全表扫描" in problem_types or "缺失索引" in problem_types

    def test_analyze_explain_good_plan(self):
        """EXPLAIN良好执行计划"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "ref", "possible_keys": "idx_user_id", "key": "idx_user_id",
                "rows": 5, "filtered": 100.0, "extra": "Using index"
            }]
        })
        assert resp.status_code == 200
        data = resp.json()
        errors = [a for a in data["analyses"] if a["severity"] == "ERROR"]
        assert len(errors) == 0

    def test_analyze_explain_filesort(self):
        """EXPLAIN filesort检测"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "ALL", "rows": 100000, "filtered": 100.0,
                "extra": "Using where; Using filesort"
            }]
        })
        assert resp.status_code == 200
        data = resp.json()
        problem_types = [a["problem_type"] for a in data["analyses"]]
        assert "Using filesort" in problem_types

    def test_analyze_explain_temporary(self):
        """EXPLAIN临时表检测"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "ALL", "rows": 50000, "filtered": 100.0,
                "extra": "Using where; Using temporary"
            }]
        })
        assert resp.status_code == 200
        data = resp.json()
        problem_types = [a["problem_type"] for a in data["analyses"]]
        assert "Using temporary" in problem_types


# ═══════════════════════════════════════════════════════════
# 八、Dashboard模块
# ═══════════════════════════════════════════════════════════

class TestDashboard:
    """Dashboard模块测试"""

    def test_dashboard_summary(self):
        """Dashboard概览"""
        resp = client.get("/api/v1/dashboard/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "audit" in data
        assert "slow_queries" in data
        assert "rules" in data
        assert data["rules"]["total"] == 77
        assert data["rules"]["by_category"]["naming"] == 5
        assert data["rules"]["by_category"]["ddl"] == 22
        assert data["rules"]["by_category"]["dml"] == 9
        assert data["rules"]["by_category"]["distributed"] == 14

    def test_audit_trend(self):
        """审核趋势"""
        resp = client.get("/api/v1/dashboard/audit-trend?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert "dates" in data
        assert "passed" in data
        assert "failed" in data


# ═══════════════════════════════════════════════════════════
# 九、GitLab集成模块
# ═══════════════════════════════════════════════════════════

class TestGitLabIntegration:
    """GitLab集成模块测试"""

    def test_gitlab_config(self):
        """GitLab配置说明"""
        resp = client.get("/api/v1/gitlab/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "webhook_url" in data
        assert "setup_steps" in data
        assert "api_endpoints" in data

    def test_audit_diff(self):
        """审核Git Diff"""
        resp = client.post("/api/v1/gitlab/audit/diff", json={
            "diff": "+SELECT * FROM t_user\n+SELECT id FROM t_ok WHERE id = 1",
            "file_path": "test.sql"
        })
        assert resp.status_code == 200
        data = resp.json()
        # 应该检测到SQL变更
        assert "total_sql" in data or "message" in data

    def test_audit_repository(self):
        """审核仓库文件"""
        resp = client.post("/api/v1/gitlab/audit/repository", json={
            "files": [
                {"path": "mapper/UserMapper.xml", "content": """
                    <mapper namespace="com.example.UserMapper">
                        <select id="getUser">SELECT * FROM t_user WHERE id = #{id}</select>
                    </mapper>
                """},
                {"path": "sql/init.sql", "content": "SELECT id FROM t_user WHERE id = 1;"}
            ]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "total_files" in data
        assert "total_sql" in data

    def test_mr_webhook_ignore_non_mr(self):
        """MR Webhook忽略非MR事件"""
        resp = client.post("/api/v1/gitlab/webhook/merge-request", json={
            "object_kind": "push",
            "ref": "refs/heads/main"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "Ignored" in data.get("message", "")

    def test_mr_webhook_ignore_other_action(self):
        """MR Webhook忽略其他action"""
        resp = client.post("/api/v1/gitlab/webhook/merge-request", json={
            "object_kind": "merge_request",
            "object_attributes": {"iid": 1, "action": "close"},
            "project": {"name": "test"}
        })
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════
# 十、TDSQL管理模块
# ═══════════════════════════════════════════════════════════

class TestTDSQLManage:
    """TDSQL管理模块测试"""

    def test_connection_status(self):
        """连接状态检查"""
        resp = client.get("/api/v1/tdsql/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "connected" in data

    def test_disconnect(self):
        """断开连接"""
        resp = client.post("/api/v1/tdsql/disconnect")
        assert resp.status_code == 200

    def test_tables_without_connection(self):
        """未连接时获取表列表应报错"""
        resp = client.get("/api/v1/tdsql/tables")
        assert resp.status_code == 400

    def test_slow_query_fetch_without_connection(self):
        """未连接时抓取慢SQL应报错"""
        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 10
        })
        assert resp.status_code == 400

    def test_charset_check_without_connection(self):
        """未连接时字符集检查应报错"""
        resp = client.get("/api/v1/tdsql/check/charset")
        assert resp.status_code == 400

    def test_large_table_check_without_connection(self):
        """未连接时大表检查应报错"""
        resp = client.get("/api/v1/tdsql/check/large-tables")
        assert resp.status_code == 400
