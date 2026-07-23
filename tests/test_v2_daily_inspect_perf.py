"""
测试每日巡检异步非阻塞、快照缓存与多线程并发性能（覆盖 In-Process 服务层与 FastAPI HTTP 接口层）
"""
import time
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.daily_inspect_service import run_daily, _DAILY_CACHE
from backend.services.connection_registry import registry
from backend.services.tdsql_connector import TDSQLConnectionPool

def test_daily_inspect_cache_and_performance():
    """1. In-Process 服务层 30秒 TTL 快照缓存加速自测"""
    mock_pool = MagicMock()
    mock_pool._monitor_execute.return_value = []
    
    _DAILY_CACHE.clear()
    
    # 首次运行
    start_time = time.time()
    res1 = run_daily(mock_pool, connection_id="test_perf_conn", inspect_date="2026-07-23")
    cost1 = time.time() - start_time
    assert res1["status"] == "SUCCESS"
    assert res1["node_count"] > 0
    
    # 再次运行（命中 30 秒缓存快照，< 50ms）
    start_cache = time.time()
    res2 = run_daily(mock_pool, connection_id="test_perf_conn", inspect_date="2026-07-23")
    cost_cache = time.time() - start_cache
    
    assert res2["status"] == "SUCCESS"
    assert cost_cache < 0.05, "二次点击应当从缓存极速返回，耗时小于50毫秒"


def test_daily_inspect_http_layer_asyncio_threadpool():
    """2. FastAPI HTTP 接口层 asyncio.to_thread 与 端到端缓存测试"""
    _DAILY_CACHE.clear()
    
    # 注册 Mock pool 到 registry 供 HTTP 接口调用
    mock_pool = MagicMock()
    mock_pool.config.host = "127.0.0.1"
    mock_pool.config.port = 3306
    mock_pool.config.database = "test_db"
    mock_pool.config.user = "root"
    mock_pool.monitor_probe.return_value = {"ok": True, "error": "", "columns": [1]}
    mock_pool._monitor_execute.return_value = []
    
    mock_entry = MagicMock()
    mock_entry.pool = mock_pool
    mock_entry.id = "mock_perf_conn"
    mock_entry.last_used = time.time()
    registry._pools["mock_perf_conn"] = mock_entry
    
    try:
        client = TestClient(app)
        
        # 首次 HTTP 请求
        t0 = time.time()
        resp1 = client.post("/api/v1/daily-inspect/run", json={
            "connection_id": "mock_perf_conn",
            "inspect_date": "2026-07-23"
        })
        cost1 = time.time() - t0
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["status"] == "SUCCESS"
        
        # 二次 HTTP 请求 (30秒缓存快照命中，验证 HTTP 响应也是 < 50ms 极速返回)
        t1 = time.time()
        resp2 = client.post("/api/v1/daily-inspect/run", json={
            "connection_id": "mock_perf_conn",
            "inspect_date": "2026-07-23"
        })
        cost_cache = time.time() - t1
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["status"] == "SUCCESS"
        assert cost_cache < 0.05, f"HTTP层二次调用应在50ms内极速响应，实际: {cost_cache:.4f}s"
    finally:
        registry._pools.pop("mock_perf_conn", None)
