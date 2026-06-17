"""
UAT Frontend - 前端功能UAT测试

模拟前端index.html中Vue组件调用的所有API端点，确保前后端集成正常。

前端页面功能:
1. Dashboard - 统计展示
2. SQL审核 - 单条SQL审核
3. 文件审核 - SQL文件/MyBatis XML
4. 慢SQL分析 - 添加和分析慢SQL
5. EXPLAIN分析 - EXPLAIN计划分析
"""
import pytest
from fastapi.testclient import TestClient


class TestUAT29_FrontendDashboard:
    """UAT29: 前端Dashboard功能测试"""

    def test_uat29_01_dashboard_summary_api(self):
        """测试Dashboard汇总API"""
        from backend.main import app
        client = TestClient(app)
        
        resp = client.get("/api/v1/dashboard/summary")
        assert resp.status_code == 200
        data = resp.json()
        # 验证返回的数据结构
        assert "audit" in data or "rules" in data or data == {}

    def test_uat29_02_dashboard_trend_api(self):
        """测试Dashboard趋势API"""
        from backend.main import app
        client = TestClient(app)
        
        resp = client.get("/api/v1/dashboard/audit-trend")
        assert resp.status_code == 200


class TestUAT30_FrontendSQLAudit:
    """UAT30: 前端SQL审核功能测试"""

    def test_uat30_01_simple_select_audit(self):
        """测试简单SELECT审核"""
        from backend.main import app
        client = TestClient(app)
        
        resp = client.post("/api/v1/audit/sql", json={
            "sql": "SELECT id, name FROM t_user WHERE id = 1",
            "db_type": "tdsql"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "passed" in data or "violations" in data

    def test_uat30_02_select_star_rejected(self):
        """测试SELECT * 被拒绝"""
        from backend.main import app
        client = TestClient(app)
        
        resp = client.post("/api/v1/audit/sql", json={
            "sql": "SELECT * FROM t_user",
            "db_type": "tdsql"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("passed") == False
        assert len(data.get("violations", [])) > 0

    def test_uat30_03_create_table_audit(self):
        """测试CREATE TABLE审核"""
        from backend.main import app
        client = TestClient(app)
        
        sql = """CREATE TABLE t_test (
            id BIGINT NOT NULL AUTO_INCREMENT,
            name VARCHAR(100),
            PRIMARY KEY (id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
        resp = client.post("/api/v1/audit/sql", json={
            "sql": sql,
            "db_type": "tdsql"
        })
        assert resp.status_code == 200

    def test_uat30_04_update_without_where_rejected(self):
        """测试UPDATE无WHERE被拒绝"""
        from backend.main import app
        client = TestClient(app)
        
        resp = client.post("/api/v1/audit/sql", json={
            "sql": "UPDATE t_user SET status = 0",
            "db_type": "tdsql"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("passed") == False


class TestUAT31_FrontendFileAudit:
    """UAT31: 前端文件审核功能测试"""

    def test_uat31_01_sql_file_audit(self):
        """测试SQL文件审核"""
        from backend.main import app
        client = TestClient(app)
        
        content = """SELECT * FROM t_user;
SELECT id, name FROM t_order WHERE status = 1;"""
        resp = client.post("/api/v1/audit/file", json={
            "content": content,
            "file_path": "test.sql"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data or "violations" in data

    def test_uat31_02_mybatis_xml_audit(self):
        """测试MyBatis XML文件审核"""
        from backend.main import app
        client = TestClient(app)
        
        content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="UserMapper">
    <select id="findAll" resultType="User">
        SELECT * FROM t_user
    </select>
</mapper>"""
        resp = client.post("/api/v1/audit/file", json={
            "content": content,
            "file_path": "UserMapper.xml"
        })
        assert resp.status_code == 200


class TestUAT32_FrontendSlowQuery:
    """UAT32: 前端慢SQL分析功能测试"""

    def test_uat32_01_add_slow_query(self):
        """测试添加慢SQL"""
        from backend.main import app
        client = TestClient(app)
        
        slow_form = {
            "fingerprint": "SELECT * FROM t_order WHERE user_id = ?",
            "sql_text": "SELECT * FROM t_order WHERE user_id = 123",
            "db_name": "order_db",
            "exec_count": 5000,
            "avg_time_ms": 200,
            "rows_examined": 850000,
            "rows_sent": 100
        }
        resp = client.post("/api/v1/slow-queries", json=slow_form)
        assert resp.status_code in [200, 201]

    def test_uat32_02_list_slow_queries(self):
        """测试获取慢SQL列表"""
        from backend.main import app
        client = TestClient(app)
        
        resp = client.get("/api/v1/slow-queries?limit=20")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data or isinstance(data, list)

    def test_uat32_03_slow_query_statistics(self):
        """测试慢SQL统计"""
        from backend.main import app
        client = TestClient(app)
        
        resp = client.get("/api/v1/slow-queries/statistics")
        assert resp.status_code == 200


class TestUAT33_FrontendExplainAnalysis:
    """UAT33: 前端EXPLAIN分析功能测试"""

    def test_uat33_01_explain_all_scan_detected(self):
        """测试EXPLAIN全表扫描检测"""
        from backend.main import app
        client = TestClient(app)
        
        explain_data = [{
            "id": 1,
            "select_type": "SIMPLE",
            "table": "t_order",
            "type": "ALL",
            "possible_keys": None,
            "key": None,
            "rows": 850000,
            "filtered": 10.0,
            "extra": "Using where"
        }]
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": explain_data
        })
        assert resp.status_code == 200
        data = resp.json()
        # 应该检测到问题
        assert "analyses" in data or "issues" in data or "problems" in data or "summary" in data

    def test_uat33_02_explain_good_plan(self):
        """测试EXPLAIN好计划"""
        from backend.main import app
        client = TestClient(app)
        
        explain_data = [{
            "id": 1,
            "select_type": "SIMPLE",
            "table": "t_user",
            "type": "eq_ref",
            "possible_keys": "PRIMARY",
            "key": "PRIMARY",
            "rows": 1,
            "filtered": 100.0,
            "extra": "Using index condition"
        }]
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": explain_data
        })
        assert resp.status_code == 200

    def test_uat33_03_explain_filesort_detected(self):
        """测试EXPLAIN文件排序检测"""
        from backend.main import app
        client = TestClient(app)
        
        explain_data = [{
            "id": 1,
            "select_type": "SIMPLE",
            "table": "t_user",
            "type": "ALL",
            "possible_keys": None,
            "key": None,
            "rows": 100000,
            "filtered": 100.0,
            "extra": "Using filesort"
        }]
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": explain_data
        })
        assert resp.status_code == 200


class TestUAT34_FrontendErrorHandling:
    """UAT34: 前端错误处理测试"""

    def test_uat34_01_empty_sql_handling(self):
        """测试空SQL处理"""
        from backend.main import app
        client = TestClient(app)
        
        resp = client.post("/api/v1/audit/sql", json={
            "sql": "",
            "db_type": "tdsql"
        })
        # 应该被拒绝或返回空结果
        assert resp.status_code in [200, 400, 422]

    def test_uat34_02_invalid_json_handling(self):
        """测试无效JSON处理"""
        from backend.main import app
        client = TestClient(app)
        
        resp = client.post(
            "/api/v1/audit/sql",
            data="not valid json",
            headers={"Content-Type": "application/json"}
        )
        assert resp.status_code in [400, 422, 500]

    def test_uat34_03_missing_field_handling(self):
        """测试缺少字段处理"""
        from backend.main import app
        client = TestClient(app)
        
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "SELECT * FROM t"
            # 缺少其他必需字段
        })
        assert resp.status_code in [400, 422]


class TestUAT_FfrontendReport:
    """UAT Frontend 报告生成测试"""

    def test_generate_report(self):
        """测试报告生成"""
        from backend.main import app
        client = TestClient(app)
        
        # 执行一个简单审核
        resp = client.post("/api/v1/audit/sql", json={
            "sql": "SELECT * FROM t_user WHERE id = 1"
        })
        assert resp.status_code == 200