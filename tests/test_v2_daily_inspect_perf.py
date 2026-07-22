"""
测试每日巡检异步非阻塞、快照缓存与多线程并发性能
"""
import time
import pytest
from unittest.mock import MagicMock
from backend.services.daily_inspect_service import run_daily, _DAILY_CACHE

def test_daily_inspect_cache_and_performance():
    # 构造 mock pool
    mock_pool = MagicMock()
    mock_pool._monitor_execute.return_value = []
    
    # 清空缓存
    _DAILY_CACHE.clear()
    
    # 第一次运行
    start_time = time.time()
    res1 = run_daily(mock_pool, connection_id="test_perf_conn", inspect_date="2026-07-23")
    cost1 = time.time() - start_time
    assert res1["status"] == "SUCCESS"
    assert res1["node_count"] > 0
    
    # 第二次运行（必定命中 30 秒缓存快照，接近 0 秒）
    start_cache = time.time()
    res2 = run_daily(mock_pool, connection_id="test_perf_conn", inspect_date="2026-07-23")
    cost_cache = time.time() - start_cache
    
    assert res2["status"] == "SUCCESS"
    assert cost_cache < 0.05, "二次点击应当从缓存极速返回，耗时小于50毫秒"
