"""
慢SQL扫描 - UAT 用户验收测试

验证前端交互所需的API契约和前端HTML结构。
覆盖用户操作流程：数据源选择 → 扫描 → 结果展示 → SET筛选 → 跨SET分析
"""
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def test_db():
    """创建临时测试数据库"""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    with patch("backend.services.slow_query_service.DB_PATH", Path(db_path)), \
         patch("backend.services.database.DB_PATH", Path(db_path)):
        import backend.services.database as db_mod
        db_mod._db_initialized = False
        from backend.services.database import ensure_db
        ensure_db()
    yield db_path
    os.unlink(db_path)


@pytest.fixture
def client(test_db):
    """每个测试独立的客户端"""
    conn = sqlite3.connect(test_db)
    conn.execute("DELETE FROM slow_queries")
    conn.execute("DELETE FROM scan_tasks")
    conn.commit()
    conn.close()

    with patch("backend.services.slow_query_service.DB_PATH", Path(test_db)), \
         patch("backend.services.database.DB_PATH", Path(test_db)):
        import backend.services.database as db_mod
        db_mod._db_initialized = True

        from backend.main import app
        from backend.api import tdsql_manage
        mock_pool = MagicMock()
        mock_pool.config = MagicMock()
        mock_pool.config.database = "tdsql_check"
        mock_pool.config.host = "192.168.1.100"
        mock_pool.config.port = 3306
        tdsql_manage._pool = mock_pool

        with TestClient(app) as c:
            yield c
        # 清理V1.0兼容测试席位，避免污染后续"未连接"用例
        tdsql_manage._pool = None


# ── UAT-1: 前端HTML结构验证 ─────────────────────────────

class TestUATFrontendStructure:
    """UAT: 前端HTML结构包含正确的慢SQL扫描元素"""

    @pytest.fixture(scope="class")
    def html_content(self):
        """读取前端HTML文件"""
        html_path = Path(__file__).parent.parent / "frontend" / "index.html"
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_no_scan_all_sets_toggle(self, html_content):
        """UAT-1a: 前端不再包含scan_all_sets开关"""
        assert "scan_all_sets" not in html_content

    def test_no_slow_log_option(self, html_content):
        """UAT-1b: 数据源选项不包含slow_log"""
        assert 'value="slow_log"' not in html_content

    def test_digest_source_option_exists(self, html_content):
        """UAT-1c: 数据源包含性能摘要分析（推荐）"""
        assert "性能摘要分析" in html_content
        assert "推荐" in html_content

    def test_processlist_source_option_exists(self, html_content):
        """UAT-1d: 数据源包含实时进程快照"""
        assert "实时进程快照" in html_content

    def test_set_filter_exists(self, html_content):
        """UAT-1e: 慢SQL列表筛选栏包含SET筛选"""
        assert "setIds" in html_content
        assert "slowFilters.set_id" in html_content

    def test_set_column_in_list(self, html_content):
        """UAT-1f: 慢SQL列表项显示SET信息"""
        assert "item.set_id" in html_content

    def test_cross_set_analysis_section_exists(self, html_content):
        """UAT-1g: 跨SET分析区域存在"""
        assert "crossSetData" in html_content
        assert "跨SET对比分析" in html_content
        assert "set_distribution" in html_content
        assert "hot_sets" in html_content
        assert "cross_set_sqls" in html_content

    def test_load_set_ids_function_exists(self, html_content):
        """UAT-1h: loadSetIds函数存在"""
        assert "loadSetIds" in html_content

    def test_load_cross_set_analysis_function_exists(self, html_content):
        """UAT-1i: loadCrossSetAnalysis函数存在"""
        assert "loadCrossSetAnalysis" in html_content

    def test_default_min_time(self, html_content):
        """UAT-1j: 默认min_time为0.1"""
        assert "min_time: 0.1" in html_content

    def test_tdsql_architecture_notice(self, html_content):
        """UAT-1k: 包含TDSQL架构说明"""
        assert "Proxy" in html_content


# ── UAT-2: API契约验证（前端调用的接口） ───────────────

class TestUATAPIContracts:
    """UAT: 验证前端依赖的API契约"""

    def test_set_ids_endpoint_format(self, client):
        """UAT-2a: /set-ids 端点返回 {set_ids: []} 格式"""
        resp = client.get("/api/v1/slow-queries/set-ids")
        assert resp.status_code == 200
        data = resp.json()
        assert "set_ids" in data
        assert isinstance(data["set_ids"], list)

    def test_cross_set_analysis_endpoint_format(self, client):
        """UAT-2b: /cross-set-analysis 端点返回完整分析结构"""
        from backend.api import tdsql_manage

        tdsql_manage._pool.get_slow_queries_from_digest = MagicMock(return_value=[
            {"DIGEST_TEXT": "SELECT * FROM t1 WHERE id=?", "SCHEMA_NAME": "db",
             "COUNT_STAR": 100, "total_seconds": 5.0, "avg_seconds": 0.5,
             "max_seconds": 2.0, "SUM_ROWS_EXAMINED": 10000, "SUM_ROWS_SENT": 10,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"}
        ])

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 10, "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        task_id = resp.json()["scan_task_id"]

        resp = client.get(f"/api/v1/slow-queries/cross-set-analysis?scan_task_id={task_id}")
        assert resp.status_code == 200
        data = resp.json()
        # 前端依赖的字段全部存在
        assert "set_distribution" in data
        assert "hot_sets" in data
        assert "cross_set_sqls" in data
        assert "advice" in data

    def test_fetch_response_contract(self, client):
        """UAT-2c: fetch响应包含正确的字段（新架构）"""
        from backend.api import tdsql_manage
        tdsql_manage._pool.get_slow_queries_from_digest = MagicMock(return_value=[
            {"DIGEST_TEXT": "SELECT 1", "SCHEMA_NAME": "db", "COUNT_STAR": 1,
             "total_seconds": 0.1, "avg_seconds": 0.1, "max_seconds": 0.1,
             "SUM_ROWS_EXAMINED": 1, "SUM_ROWS_SENT": 1,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"}
        ])

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 10, "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        data = resp.json()
        # 新架构的响应字段
        assert "source" in data
        assert "fetched" in data
        assert "scan_task_id" in data
        assert "errors" in data
        assert "results" in data
        # 旧架构字段不应存在
        assert "sets_scanned" not in data
        assert "set_count" not in data
        assert "errors_per_set" not in data

    def test_slow_query_list_includes_set_id(self, client):
        """UAT-2d: 慢SQL列表项包含set_id字段"""
        from backend.api import tdsql_manage
        tdsql_manage._pool.get_slow_queries_from_digest = MagicMock(return_value=[
            {"DIGEST_TEXT": "SELECT * FROM t1", "SCHEMA_NAME": "db", "COUNT_STAR": 1,
             "total_seconds": 0.1, "avg_seconds": 0.1, "max_seconds": 0.1,
             "SUM_ROWS_EXAMINED": 1, "SUM_ROWS_SENT": 1,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"}
        ])

        client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 10, "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })

        resp = client.get("/api/v1/slow-queries?limit=100")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) > 0
        assert "set_id" in items[0]

    def test_sets_endpoint_format(self, client):
        """UAT-2e: /tdsql/sets 端点返回 {sets: [], total: N} 格式"""
        from backend.api import tdsql_manage
        tdsql_manage._pool.discover_sets = MagicMock(return_value=[
            {"set_id": "set_1", "set_name": "set_1"},
        ])
        resp = client.get("/api/v1/tdsql/sets")
        assert resp.status_code == 200
        data = resp.json()
        assert "sets" in data
        assert "total" in data
        assert isinstance(data["sets"], list)

    def test_set_id_filter_in_query(self, client):
        """UAT-2f: 慢SQL列表支持set_id查询参数"""
        from backend.api import tdsql_manage
        tdsql_manage._pool.get_slow_queries_from_digest = MagicMock(return_value=[
            {"DIGEST_TEXT": "SELECT 1", "SCHEMA_NAME": "db", "COUNT_STAR": 1,
             "total_seconds": 0.1, "avg_seconds": 0.1, "max_seconds": 0.1,
             "SUM_ROWS_EXAMINED": 1, "SUM_ROWS_SENT": 1,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"}
        ])

        client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 10, "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })

        # 使用不存在的set_id筛选
        resp = client.get("/api/v1/slow-queries?set_id=set_999&limit=100")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ── UAT-3: 用户操作流程模拟 ─────────────────────────────

class TestUATUserFlows:
    """UAT: 模拟用户操作流程"""

    def test_flow_scan_and_view(self, client):
        """UAT-3a: 完整用户流程 - 选择digest → 扫描 → 查看结果"""
        from backend.api import tdsql_manage

        # Step 1: 用户查看SET列表（拓扑展示）
        tdsql_manage._pool.discover_sets = MagicMock(return_value=[
            {"set_id": "set_1", "set_name": "set_1"},
            {"set_id": "set_2", "set_name": "set_2"},
        ])
        resp = client.get("/api/v1/tdsql/sets")
        assert resp.json()["total"] == 2

        # Step 2: 用户选择digest数据源发起扫描
        tdsql_manage._pool.get_slow_queries_from_digest = MagicMock(return_value=[
            {"DIGEST_TEXT": "SELECT * FROM t1 WHERE id=?", "SCHEMA_NAME": "db",
             "COUNT_STAR": 100, "total_seconds": 5.0, "avg_seconds": 0.5,
             "max_seconds": 2.0, "SUM_ROWS_EXAMINED": 10000, "SUM_ROWS_SENT": 10,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"},
            {"DIGEST_TEXT": "SELECT * FROM t2 WHERE id=?", "SCHEMA_NAME": "db",
             "COUNT_STAR": 200, "total_seconds": 10.0, "avg_seconds": 1.0,
             "max_seconds": 3.0, "SUM_ROWS_EXAMINED": 20000, "SUM_ROWS_SENT": 20,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"},
        ])

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 50, "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        assert resp.status_code == 200
        scan_data = resp.json()
        assert scan_data["fetched"] == 2
        task_id = scan_data["scan_task_id"]

        # Step 3: 用户查看扫描结果
        resp = client.get(f"/api/v1/slow-queries?scan_task_id={task_id}&limit=100")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2

        # Step 4: 用户查看跨SET分析
        resp = client.get(f"/api/v1/slow-queries/cross-set-analysis?scan_task_id={task_id}")
        assert resp.status_code == 200
        analysis = resp.json()
        assert "advice" in analysis

    def test_flow_processlist_scan(self, client):
        """UAT-3b: 用户选择processlist数据源扫描"""
        from backend.api import tdsql_manage

        # processlist扫描自slow_log重构后走poll_processlist（多次轮询采样）
        tdsql_manage._pool.poll_processlist = MagicMock(return_value=[
            {"id": 1, "user": "root", "host": "localhost", "db": "test",
             "command": "Query", "time": 60, "state": "Sending data",
             "info": "SELECT * FROM huge_table"},
        ])

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "processlist",
            "limit": 50,
            "min_time": 5,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "processlist"
        assert data["fetched"] == 1

    def test_flow_slow_log_rejected_with_clear_message(self, client):
        """UAT-3c: 用户尝试slow_log时收到明确错误"""
        from backend.api import tdsql_manage
        tdsql_manage._pool.get_slow_queries_from_digest = MagicMock(return_value=[])

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "slow_log",
            "limit": 50,
            "min_time": 1.0,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        # 错误消息应该解释原因
        assert "Proxy" in detail or "不可用" in detail or "不支持" in detail


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
