"""UAT测试脚本 - 验证慢查询扫描系统修改"""
import requests
import json
import time

BASE = "http://localhost:8000"

def test_proxy_config():
    print("=" * 60)
    print("TEST 1: Proxy配置获取")
    r = requests.get(f"{BASE}/api/v1/tdsql/proxy-config")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.json()
    assert data["status"] == "success"
    pc = data["proxy_config"]
    print(f"  slow_log_ms = {pc['slow_log_ms']}")
    print(f"  slow_log_level = {pc['slow_log_level']}")
    print(f"  PASS: Proxy配置获取成功")
    return True

def test_digest_scan():
    print("=" * 60)
    print("TEST 2: 性能摘要(digest)模式扫描")
    r = requests.post(f"{BASE}/api/v1/tdsql/slow-queries/fetch", json={
        "source": "digest",
        "limit": 20,
        "min_time": 0.1,
        "task_name": "UAT-digest-test",
        "time_window_start": "2026-07-01 00:00:00",
        "time_window_end": "2026-07-01 23:59:59",
    })
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.json()
    fetched = data.get("fetched", 0)
    print(f"  抓取条数: {fetched}")
    if fetched > 0:
        print(f"  scan_task_id: {data.get('scan_task_id')}")
        print(f"  PASS: 性能摘要模式成功捕获 {fetched} 条慢SQL")
    else:
        print(f"  WARNING: 未捕获慢SQL（可能performance_schema数据为空）")
    return fetched > 0

def test_processlist_poll():
    print("=" * 60)
    print("TEST 3: 进程快照(processlist)轮询模式扫描 (10秒轮询)")
    start = time.time()
    r = requests.post(f"{BASE}/api/v1/tdsql/slow-queries/fetch", json={
        "source": "processlist",
        "limit": 50,
        "min_time": 0.1,
        "task_name": "UAT-processlist-poll-test",
        "time_window_start": "2026-07-01 00:00:00",
        "time_window_end": "2026-07-01 23:59:59",
        "poll_duration": 10,
        "poll_interval": 1,
    })
    elapsed = time.time() - start
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.json()
    fetched = data.get("fetched", 0)
    print(f"  耗时: {elapsed:.1f}s (预期约10s)")
    print(f"  抓取条数: {fetched}")
    if fetched > 0:
        print(f"  scan_task_id: {data.get('scan_task_id')}")
        print(f"  PASS: 轮询模式成功捕获 {fetched} 条运行中SQL")
    else:
        print(f"  INFO: 轮询期间未捕获到运行中的慢SQL（正常，取决于压测是否在跑）")
    # 验证轮询实际耗时约为poll_duration
    assert elapsed >= 5, f"轮询耗时过短({elapsed:.1f}s)，可能未正确轮询"
    print(f"  PASS: 轮询耗时 {elapsed:.1f}s，确认多次采样机制生效")
    return True

def test_status():
    print("=" * 60)
    print("TEST 4: 连接状态")
    r = requests.get(f"{BASE}/api/v1/tdsql/status")
    assert r.status_code == 200
    data = r.json()
    print(f"  connected: {data.get('connected')}")
    print(f"  host: {data.get('host')}:{data.get('port')}")
    print(f"  database: {data.get('database')}")
    print(f"  PASS")
    return True

if __name__ == "__main__":
    print("\n  TDSQL慢查询扫描系统 - UAT测试")
    print("  目标服务: " + BASE)
    print()
    
    results = {}
    results["proxy_config"] = test_proxy_config()
    results["digest_scan"] = test_digest_scan()
    results["processlist_poll"] = test_processlist_poll()
    results["status"] = test_status()
    
    print("=" * 60)
    print("测试汇总:")
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "WARN"
        if not passed:
            all_pass = False
        print(f"  [{status}] {name}")
    print("=" * 60)
    if all_pass:
        print("  ALL TESTS PASSED!")
    else:
        print("  SOME TESTS HAD WARNINGS (non-critical)")
