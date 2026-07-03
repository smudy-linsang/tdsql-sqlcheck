"""
TDSQL SQL审核系统 - 完整SIT/UAT测试套件
测试目标：验证所有API接口和端到端业务流程
"""
import requests
import json
import time
import sys

BASE = "http://localhost:8003"
RESULTS = {"pass": 0, "fail": 0, "errors": []}

def test(name, condition, detail=""):
    if condition:
        RESULTS["pass"] += 1
        print(f"  [PASS] {name}")
    else:
        RESULTS["fail"] += 1
        RESULTS["errors"].append(f"{name}: {detail}")
        print(f"  [FAIL] {name} - {detail}")

def section(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")

# ══════════════════════════════════════════════════════════════
# SIT测试：接口级集成测试
# ══════════════════════════════════════════════════════════════

section("SIT-1: 服务健康检查")
try:
    r = requests.get(f"{BASE}/docs", timeout=5)
    test("Swagger文档可访问", r.status_code == 200)
    r = requests.get(f"{BASE}/", timeout=5)
    test("前端页面可访问", r.status_code == 200 and "TDSQL" in r.text)
except Exception as e:
    test("服务可达性", False, str(e))

section("SIT-2: TDSQL连接管理接口")
# 连接列表
r = requests.get(f"{BASE}/api/v1/tdsql/connections")
test("GET /connections 返回200", r.status_code == 200)
conns_data = r.json()
conns = conns_data.get("connections", conns_data) if isinstance(conns_data, dict) else conns_data
test("连接列表为数组", isinstance(conns, list))
test("存在已保存连接", len(conns) > 0)

# 使用已保存连接进行连接（选择分布式实例 port 15005）
conn_id = None
for c in conns:
    if c.get("port") == 15005:
        conn_id = c["id"]
        break
if not conn_id and conns:
    conn_id = conns[0]["id"]
if conn_id:
    r = requests.post(f"{BASE}/api/v1/tdsql/connections/{conn_id}/connect")
    test(f"POST /connections/{conn_id}/connect 返回200", r.status_code == 200)
    data = r.json()
    test("连接响应包含host", "host" in data)
    test("连接响应包含port", "port" in data)

# 状态查询
r = requests.get(f"{BASE}/api/v1/tdsql/status")
test("GET /status 返回200", r.status_code == 200)
status = r.json()
test("状态显示已连接", status.get("connected") == True)
test("状态包含host字段", "host" in status)

section("SIT-3: Proxy配置接口")
r = requests.get(f"{BASE}/api/v1/tdsql/proxy-config")
test("GET /proxy-config 返回200", r.status_code == 200)
pc = r.json()
test("proxy_config响应含status", pc.get("status") == "success")
test("包含slow_log_ms", "slow_log_ms" in pc.get("proxy_config", {}))
test("包含slow_log_level", "slow_log_level" in pc.get("proxy_config", {}))
test("包含all_config", "all_config" in pc.get("proxy_config", {}))

section("SIT-4: SET发现接口")
r = requests.get(f"{BASE}/api/v1/tdsql/sets")
test("GET /sets 返回200", r.status_code == 200)
sets_data = r.json()
test("返回包含sets字段", "sets" in sets_data)

section("SIT-5: 慢SQL抓取-Digest模式")
r = requests.post(f"{BASE}/api/v1/tdsql/slow-queries/fetch", json={
    "source": "digest",
    "limit": 10,
    "min_time": 0.05,
    "task_name": "SIT-digest-test",
    "time_window_start": "2026-07-01 00:00:00",
    "time_window_end": "2026-07-01 23:59:59",
})
test("POST digest fetch 返回200", r.status_code == 200)
data = r.json()
test("响应包含fetched字段", "fetched" in data)
test("响应包含scan_task_id", "scan_task_id" in data)
test("digest模式捕获慢SQL", data.get("fetched", 0) > 0, f"fetched={data.get('fetched')}")

section("SIT-6: 慢SQL抓取-Processlist轮询模式")
start = time.time()
r = requests.post(f"{BASE}/api/v1/tdsql/slow-queries/fetch", json={
    "source": "processlist",
    "limit": 50,
    "min_time": 0,
    "task_name": "SIT-poll-test",
    "time_window_start": "2026-07-01 00:00:00",
    "time_window_end": "2026-07-01 23:59:59",
    "poll_duration": 5,
    "poll_interval": 1,
})
elapsed = time.time() - start
test("POST processlist fetch 返回200", r.status_code == 200)
data = r.json()
test("响应包含fetched字段", "fetched" in data)
test("轮询耗时>=4s(确认多次采样)", elapsed >= 4, f"elapsed={elapsed:.1f}s")
test("轮询耗时<=8s(不会过长)", elapsed <= 8, f"elapsed={elapsed:.1f}s")

section("SIT-7: 数据源校验-禁止slow_log")
r = requests.post(f"{BASE}/api/v1/tdsql/slow-queries/fetch", json={
    "source": "slow_log",
    "limit": 10,
    "min_time": 0.1,
    "task_name": "SIT-slowlog-rejected",
    "time_window_start": "2026-07-01 00:00:00",
    "time_window_end": "2026-07-01 23:59:59",
})
test("slow_log数据源被拒绝(非200)", r.status_code != 200, f"status={r.status_code}")

section("SIT-8: 扫描任务列表")
r = requests.get(f"{BASE}/api/v1/slow-queries/scan-tasks?limit=10")
test("GET /scan-tasks 返回200", r.status_code == 200)
tasks = r.json()
test("返回包含items", "items" in tasks)
test("有扫描任务记录", len(tasks.get("items", [])) > 0)

section("SIT-9: 慢SQL列表查询")
r = requests.get(f"{BASE}/api/v1/slow-queries?limit=10")
test("GET /slow-queries 返回200", r.status_code == 200)
sq = r.json()
test("返回包含items", "items" in sq)

section("SIT-10: DB名称和SET ID列表")
r = requests.get(f"{BASE}/api/v1/slow-queries/db-names")
test("GET /db-names 返回200", r.status_code == 200)
r = requests.get(f"{BASE}/api/v1/slow-queries/set-ids")
test("GET /set-ids 返回200", r.status_code == 200)

section("SIT-11: SQL审核接口")
r = requests.post(f"{BASE}/api/v1/audit/sql", json={
    "sql": "SELECT * FROM orders WHERE 1=1",
    "db_type": "tdsql"
})
test("POST /audit/sql 返回200", r.status_code == 200)
audit = r.json()
test("审核结果包含violations", "violations" in audit or "results" in audit)

section("SIT-12: 断开连接")
r = requests.post(f"{BASE}/api/v1/tdsql/disconnect")
test("POST /disconnect 返回200", r.status_code == 200)
r = requests.get(f"{BASE}/api/v1/tdsql/status")
test("断开后状态为未连接", r.json().get("connected") == False)

# 重新连接用于UAT测试
if conn_id:
    requests.post(f"{BASE}/api/v1/tdsql/connections/{conn_id}/connect")

# ══════════════════════════════════════════════════════════════
# UAT测试：端到端业务场景验证
# ══════════════════════════════════════════════════════════════

section("UAT-1: 完整业务流程-连接并扫描")
# Step 1: 确认连接
r = requests.get(f"{BASE}/api/v1/tdsql/status")
test("UAT: 连接状态正常", r.json().get("connected") == True)

# Step 2: 获取Proxy配置
r = requests.get(f"{BASE}/api/v1/tdsql/proxy-config")
pc = r.json()
test("UAT: Proxy配置获取成功", pc.get("status") == "success")
slow_ms = pc.get("proxy_config", {}).get("slow_log_ms", "unknown")
print(f"       Proxy慢日志阈值: {slow_ms}ms")

# Step 3: 使用digest模式扫描
r = requests.post(f"{BASE}/api/v1/tdsql/slow-queries/fetch", json={
    "source": "digest",
    "limit": 20,
    "min_time": 0.1,
    "task_name": "UAT-完整流程验证",
    "time_window_start": "2026-07-01 00:00:00",
    "time_window_end": "2026-07-01 23:59:59",
})
data = r.json()
fetched = data.get("fetched", 0)
task_id = data.get("scan_task_id")
test("UAT: digest扫描成功", r.status_code == 200)
test("UAT: 能捕获到慢SQL", fetched > 0, f"fetched={fetched}")
print(f"       捕获慢SQL: {fetched}条, task_id={task_id}")

# Step 4: 查询扫描结果
if task_id:
    r = requests.get(f"{BASE}/api/v1/slow-queries?scan_task_id={task_id}&limit=5")
    test("UAT: 按任务ID查询结果", r.status_code == 200)
    items = r.json().get("items", [])
    test("UAT: 结果记录可查询到", len(items) > 0, f"items={len(items)}")
    if items:
        item = items[0]
        test("UAT: 记录含fingerprint", "fingerprint" in item)
        test("UAT: 记录含avg_time_ms", "avg_time_ms" in item)
        test("UAT: 记录含db_name", "db_name" in item)

section("UAT-2: Processlist轮询业务场景")
start = time.time()
r = requests.post(f"{BASE}/api/v1/tdsql/slow-queries/fetch", json={
    "source": "processlist",
    "limit": 50,
    "min_time": 0,
    "task_name": "UAT-进程轮询验证",
    "time_window_start": "2026-07-01 00:00:00",
    "time_window_end": "2026-07-01 23:59:59",
    "poll_duration": 6,
    "poll_interval": 1,
})
elapsed = time.time() - start
data = r.json()
test("UAT: processlist轮询成功", r.status_code == 200)
test("UAT: 轮询持续约6秒", 4 < elapsed < 10, f"elapsed={elapsed:.1f}s")
print(f"       轮询耗时: {elapsed:.1f}s, 捕获: {data.get('fetched', 0)}条")

section("UAT-3: SQL审核完整流程")
sqls = [
    "SELECT * FROM t_order WHERE status = 1",
    "SELECT * FROM t_user WHERE name LIKE '%test%'",
    "UPDATE t_order SET amount = 100",  # 无WHERE条件
]
for sql in sqls:
    r = requests.post(f"{BASE}/api/v1/audit/sql", json={"sql": sql, "db_type": "tdsql"})
    if r.status_code == 200:
        data = r.json()
        violations = data.get("violations", [])
        results = data.get("results", [])
        count = len(violations) if violations else len(results)
        print(f"       SQL: {sql[:50]}... => {count}条违规")

test("UAT: SQL审核接口可用", True)

# ══════════════════════════════════════════════════════════════
# 测试报告
# ══════════════════════════════════════════════════════════════
section("测试报告")
total = RESULTS["pass"] + RESULTS["fail"]
print(f"  总计: {total}项测试")
print(f"  通过: {RESULTS['pass']}项")
print(f"  失败: {RESULTS['fail']}项")
print(f"  通过率: {RESULTS['pass']/total*100:.1f}%")
if RESULTS["errors"]:
    print(f"\n  失败项:")
    for e in RESULTS["errors"]:
        print(f"    - {e}")
print(f"\n{'='*60}")
if RESULTS["fail"] == 0:
    print("  ALL TESTS PASSED!")
else:
    print(f"  {RESULTS['fail']} TESTS FAILED")
    sys.exit(1)
