"""
慢SQL扫描 - SIT 系统集成测试

全链路集成测试：基于TDSQL Proxy直查架构
- digest数据源扫描 → 分析 → 存储 → 查询
- processlist数据源扫描
- slow_log拒绝验证
- 边界场景和异常处理
- set_id筛选（基于已有数据）
- 跨SET分析（基于已有数据）
"""
import json
import os
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
    """每个测试用例独立的测试客户端（每次重置DB）"""
    # 清空测试数据库中的数据
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


def _make_mock_pool(digest_data=None, processlist_data=None):
    """创建mock连接池（Proxy直查架构，无SET路由）"""
    pool = MagicMock()
    pool.config = MagicMock()
    pool.config.database = "tdsql_check"
    pool.config.host = "192.168.1.100"
    pool.config.port = 3306

    pool.get_slow_queries_from_digest = MagicMock(return_value=digest_data or [])
    # processlist扫描自slow_log重构后走poll_processlist（多次轮询采样）
    pool.poll_processlist = MagicMock(return_value=processlist_data or [])
    return pool


# ── SIT-1: 全链路 - digest 数据源 ────────────────────────

class TestSITDigest:
    """SIT: digest数据源全链路（Proxy直查）"""

    def test_full_chain_digest_scan(self, client):
        """SIT-1a: digest扫描全链路 - 扫描→存储→查询"""
        from backend.api import tdsql_manage

        digest_data = [
            {"DIGEST_TEXT": "SELECT * FROM t1 WHERE id=?", "SCHEMA_NAME": "db1", "COUNT_STAR": 100,
             "total_seconds": 5.0, "avg_seconds": 0.5, "max_seconds": 2.0,
             "SUM_ROWS_EXAMINED": 10000, "SUM_ROWS_SENT": 10, "no_index_count": 5,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"},
            {"DIGEST_TEXT": "SELECT * FROM t2 WHERE name=?", "SCHEMA_NAME": "db1", "COUNT_STAR": 200,
             "total_seconds": 10.0, "avg_seconds": 1.0, "max_seconds": 3.0,
             "SUM_ROWS_EXAMINED": 20000, "SUM_ROWS_SENT": 20, "no_index_count": 0,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"},
            {"DIGEST_TEXT": "UPDATE t3 SET status=? WHERE id=?", "SCHEMA_NAME": "db1", "COUNT_STAR": 50,
             "total_seconds": 15.0, "avg_seconds": 1.5, "max_seconds": 4.0,
             "SUM_ROWS_EXAMINED": 30000, "SUM_ROWS_SENT": 0, "no_index_count": 2,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"},
        ]
        tdsql_manage._pool = _make_mock_pool(digest_data=digest_data)

        # Step 1: 扫描
        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 50, "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        assert resp.status_code == 200
        scan_data = resp.json()
        assert scan_data["source"] == "digest"
        assert scan_data["fetched"] == 3
        task_id = scan_data["scan_task_id"]

        # Step 2: 查询已存储的慢SQL
        resp = client.get(f"/api/v1/slow-queries?scan_task_id={task_id}&limit=100")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 3
        # 所有结果的set_id应为空（Proxy层聚合）
        for item in items:
            assert item["set_id"] == ""

        # Step 3: 扫描任务详情
        resp = client.get(f"/api/v1/slow-queries/scan-tasks/{task_id}")
        assert resp.status_code == 200
        task_detail = resp.json()
        assert task_detail["total_fetched"] == 3
        assert task_detail["total_analyzed"] == 3

    def test_digest_multiple_scans(self, client):
        """SIT-1b: 多次扫描产生独立任务"""
        from backend.api import tdsql_manage

        tdsql_manage._pool = _make_mock_pool(digest_data=[
            {"DIGEST_TEXT": "SELECT 1", "SCHEMA_NAME": "db", "COUNT_STAR": 1,
             "total_seconds": 0.1, "avg_seconds": 0.1, "max_seconds": 0.1,
             "SUM_ROWS_EXAMINED": 1, "SUM_ROWS_SENT": 1,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"},
        ])

        # 第一次扫描
        resp1 = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 10, "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        task_id_1 = resp1.json()["scan_task_id"]

        # 第二次扫描
        resp2 = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 10, "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        task_id_2 = resp2.json()["scan_task_id"]

        # 两次扫描产生不同task_id
        assert task_id_1 != task_id_2


# ── SIT-2: processlist 数据源 ─────────────────────────────

class TestSITProcesslist:
    """SIT: processlist数据源全链路"""

    def test_full_chain_processlist(self, client):
        """SIT-2a: processlist扫描全链路"""
        from backend.api import tdsql_manage

        processlist_data = [
            {"id": 101, "user": "app_user", "host": "10.0.0.1:5678", "db": "tdsql_check",
             "command": "Query", "time": 15, "state": "Sending data",
             "info": "SELECT * FROM t_big_table WHERE status=1"},
            {"id": 102, "user": "app_user", "host": "10.0.0.2:4321", "db": "tdsql_check",
             "command": "Query", "time": 30, "state": "Sorting result",
             "info": "SELECT * FROM t_order ORDER BY create_time DESC LIMIT 100"},
        ]
        tdsql_manage._pool = _make_mock_pool(processlist_data=processlist_data)

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
        assert data["fetched"] == 2
        assert len(data["errors"]) == 0


# ── SIT-3: slow_log拒绝 ──────────────────────────────────

class TestSITSlowLogRejected:
    """SIT: slow_log数据源在新架构下被拒绝"""

    def test_slow_log_returns_400(self, client):
        """SIT-3a: slow_log数据源返回400错误"""
        from backend.api import tdsql_manage
        tdsql_manage._pool = _make_mock_pool()

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "slow_log",
            "limit": 100,
            "min_time": 1.0,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "不支持" in detail or "slow_log" in detail.lower()

    def test_invalid_source_returns_400(self, client):
        """SIT-3b: 无效数据源返回400错误"""
        from backend.api import tdsql_manage
        tdsql_manage._pool = _make_mock_pool()

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "invalid_source",
            "limit": 10,
            "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        assert resp.status_code == 400


# ── SIT-4: 边界场景 ──────────────────────────────────────

class TestSITBoundaryCases:
    """SIT: 边界场景"""

    def test_scan_with_error(self, client):
        """SIT-4a: 扫描过程中发生异常 - 返回错误信息但不500"""
        from backend.api import tdsql_manage

        pool = _make_mock_pool()
        pool.get_slow_queries_from_digest = MagicMock(
            side_effect=RuntimeError("Network timeout"))
        tdsql_manage._pool = pool

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 10, "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["fetched"] == 0
        assert len(data["errors"]) == 1
        assert "Network timeout" in data["errors"][0]["error"]

    def test_empty_result_set(self, client):
        """SIT-4b: 无慢SQL结果"""
        from backend.api import tdsql_manage
        tdsql_manage._pool = _make_mock_pool(digest_data=[])

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 10, "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["fetched"] == 0
        assert len(data["results"]) == 0

    def test_large_result_set(self, client):
        """SIT-4c: 大量结果（50条）"""
        from backend.api import tdsql_manage

        large_data = [
            {"DIGEST_TEXT": f"SELECT * FROM t{i} WHERE id=?", "SCHEMA_NAME": "db",
             "COUNT_STAR": i * 10, "total_seconds": i * 0.5, "avg_seconds": 0.5,
             "max_seconds": 1.0, "SUM_ROWS_EXAMINED": i * 1000, "SUM_ROWS_SENT": i,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"}
            for i in range(1, 51)
        ]
        tdsql_manage._pool = _make_mock_pool(digest_data=large_data)

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 100, "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["fetched"] == 50

    def test_missing_time_window(self, client):
        """SIT-4d: digest缺少时间窗口时返回422"""
        from backend.api import tdsql_manage
        tdsql_manage._pool = _make_mock_pool()

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 10, "min_time": 0.1,
        })
        assert resp.status_code == 422

    def test_cross_set_analysis_no_data(self, client):
        """SIT-4e: 跨SET分析 - 无SET数据时返回提示"""
        from backend.api import tdsql_manage
        tdsql_manage._pool = _make_mock_pool(digest_data=[
            {"DIGEST_TEXT": "SELECT 1", "SCHEMA_NAME": "db", "COUNT_STAR": 1,
             "total_seconds": 0.1, "avg_seconds": 0.1, "max_seconds": 0.1,
             "SUM_ROWS_EXAMINED": 1, "SUM_ROWS_SENT": 1,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"},
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
        assert "未发现带 SET 标识" in data["advice"]


# ── SIT-5: 数据完整性 ─────────────────────────────────────

class TestSITDataIntegrity:
    """SIT: 数据完整性验证"""

    def test_scan_task_records_source(self, client):
        """SIT-5a: 扫描任务正确记录数据源"""
        from backend.api import tdsql_manage
        tdsql_manage._pool = _make_mock_pool(digest_data=[
            {"DIGEST_TEXT": "SELECT * FROM t5", "SCHEMA_NAME": "db5", "COUNT_STAR": 42,
             "total_seconds": 7.0, "avg_seconds": 0.7, "max_seconds": 3.0,
             "SUM_ROWS_EXAMINED": 5000, "SUM_ROWS_SENT": 50,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"},
        ])

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 10, "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        task_id = resp.json()["scan_task_id"]

        # 验证任务详情
        resp = client.get(f"/api/v1/slow-queries/scan-tasks/{task_id}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["source"] == "digest"
        assert detail["total_fetched"] == 1

    def test_results_persisted_correctly(self, client):
        """SIT-5b: 扫描结果正确持久化"""
        from backend.api import tdsql_manage
        tdsql_manage._pool = _make_mock_pool(digest_data=[
            {"DIGEST_TEXT": "SELECT * FROM t_order WHERE uid=?", "SCHEMA_NAME": "db",
             "COUNT_STAR": 100, "total_seconds": 5.0, "avg_seconds": 0.5,
             "max_seconds": 2.0, "SUM_ROWS_EXAMINED": 10000, "SUM_ROWS_SENT": 10,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"},
        ])

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest", "limit": 10, "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        task_id = resp.json()["scan_task_id"]

        # 通过API查询验证
        resp = client.get(f"/api/v1/slow-queries?scan_task_id={task_id}&limit=100")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["exec_count"] == 100
        assert items[0]["fingerprint"] == "SELECT * FROM t_order WHERE uid=?"

    def test_sets_discovery_endpoint(self, client):
        """SIT-5c: SET发现端点返回正确数据"""
        from backend.api import tdsql_manage

        tdsql_manage._pool.discover_sets = MagicMock(return_value=[
            {"set_id": "set_1", "set_name": "set_1"},
            {"set_id": "set_2", "set_name": "set_2"},
            {"set_id": "set_3", "set_name": "set_3"},
        ])
        resp = client.get("/api/v1/tdsql/sets")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert all(s["set_id"].startswith("set_") for s in data["sets"])


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
