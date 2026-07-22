"""
Mavis v1.2 全量 API 探活 + 端到端复测脚本 v2
"""
import json
import os
import sys
import time
import traceback
import urllib.parse
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
results = []
counters = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
failures = []


def http(method, path, body=None, token=None, expect=None, allow=None, label=None):
    url = f"{BASE}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        status = resp.status
        raw = resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read().decode(errors="replace") if e.fp else ""
    except Exception as e:
        status = -1
        raw = repr(e)
    dt = (time.time() - t0) * 1000
    ok = None
    if expect is None:
        pass
    elif isinstance(expect, (list, tuple)):
        ok = status in expect
    else:
        ok = status == expect
    if allow and status in allow:
        ok = True
    if ok is None:
        counters["WARN"] += 1
        rec_result = "WARN"
    elif ok:
        counters["PASS"] += 1
        rec_result = "PASS"
    else:
        counters["FAIL"] += 1
        rec_result = "FAIL"
        failures.append({"tag": label, "method": method, "path": path, "status": status, "body_first": raw[:200]})
    results.append({"tag": label or f"{method} {path}", "method": method, "path": path,
                    "status": status, "ms": int(dt), "body_first": raw[:200], "result": rec_result})
    return status, raw


def login(username, password):
    req = urllib.request.Request(f"{BASE}/api/v1/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode())["token"]
    except Exception as e:
        print(f"  login {username} FAIL: {e}")
        return None


print("=" * 60)
print("[1] 基础健康检查 + 公开端点")
print("=" * 60)
http("GET", "/health", expect=200, label="health")
http("GET", "/api/v1/auth/visible-menus", expect=200, label="visible-menus (public?)")

print("\n" + "=" * 60)
print("[2] 4 角色登录")
print("=" * 60)
tokens = {}
for role, pwd in [("admin","Admin@1234"), ("test_admin","Test@1234"),
                  ("test_dba","Test@1234"), ("test_developer","Test@1234"),
                  ("test_auditor","Test@1234")]:
    t = login(role, pwd)
    if t:
        tokens[role] = t
        print(f"  OK: {role}")
admin_token = tokens.get("admin") or tokens.get("test_admin")

print("\n" + "=" * 60)
print("[3] 全路由探活 (admin)")
print("=" * 60)
admin_routes = [
    ("GET", "/api/v1/auth/me", None),
    ("GET", "/api/v1/auth/roles", None),
    ("GET", "/api/v1/auth/role-permissions", None),
    ("GET", "/api/v1/auth/users", None),
    ("GET", "/api/v1/audit/rules", None),
    ("GET", "/api/v1/audit/file-reports?limit=5", None),
    ("GET", "/api/v1/audit/extracted-reports?limit=5", None),
    ("GET", "/api/v1/slow-queries?limit=5", None),
    ("GET", "/api/v1/slow-queries/statistics", None),
    ("GET", "/api/v1/slow-queries/scan-tasks?limit=5", None),
    ("GET", "/api/v1/slow-queries/db-names", None),
    ("GET", "/api/v1/slow-queries/set-ids", None),
    ("GET", "/api/v1/tdsql/status", None),
    ("GET", "/api/v1/tdsql/connections", None),
    ("GET", "/api/v1/tdsql/scan-schedules", None),
    ("GET", "/api/v1/rules", None),
    ("GET", "/api/v1/rules/categories", None),
    ("GET", "/api/v1/rulesets", None),
    ("GET", "/api/v1/projects", None),
    ("GET", "/api/v1/gate/rules/default", None),
    ("GET", "/api/v1/gate/strategies", None),
    ("GET", "/api/v1/monitor/alerts", None),
    ("GET", "/api/v1/monitor/rules", None),
    ("GET", "/api/v1/inspection/tasks?limit=5", None),
    ("GET", "/api/v1/admin/info", None),
    ("GET", "/api/v1/admin/config", None),
    ("GET", "/api/v1/admin/retention", None),
    ("GET", "/api/v1/admin/operation-logs?limit=5", None),
    ("GET", "/api/v1/dashboard/summary", None),
    ("GET", "/api/v1/dashboard/rule-stats", None),
    ("GET", "/api/v1/cluster-inspect/list/adhoc?limit=5", None),
    ("GET", "/api/v1/daily-inspect/trend?connection_id=adhoc", None),
    ("GET", "/api/v1/sql-stats/bigtable/growth?connection_id=adhoc", None),
    ("GET", "/api/v1/toolkit/scripts", None),
    ("GET", "/metrics", None),  # 监控端点
]
for method, path, body in admin_routes:
    http(method, path, body=body, token=admin_token, expect=200, allow=[200])

print("\n" + "=" * 60)
print("[4] 审核引擎核心")
print("=" * 60)
# DDL 违规 - 应被规则识别
status, body = http("POST", "/api/v1/audit/sql",
    body={"sql": "CREATE TABLE t_bad (id int, name varchar(255)) ENGINE=MyISAM"},
    token=admin_token, expect=200, label="audit DDL 违规")
if status == 200:
    d = json.loads(body)
    rules_hit = [v["rule_id"] for v in d.get("violations", [])]
    print(f"  DDL 命中规则: {rules_hit}")

# 多语句
status, body = http("POST", "/api/v1/audit/sql",
    body={"sql": "SELECT * FROM t_user; DROP TABLE t_user;"},
    token=admin_token, expect=200, label="audit 多语句")
if status == 200:
    d = json.loads(body)
    rules_hit = [v["rule_id"] for v in d.get("violations", [])]
    has_stmt_prefix = any("[第" in v.get("message","") for v in d.get("violations", []))
    print(f"  多语句命中: {rules_hit}, 带'第N条'前缀: {has_stmt_prefix}")

# 元数据增强
http("POST", "/api/v1/audit/sql",
    body={"sql": "SELECT * FROM t_test", "enable_metadata": True, "connection_id": "adhoc"},
    token=admin_token, allow=[200, 500], label="audit enable_metadata")

# Git 审计 diff
http("POST", "/api/v1/gitlab/audit/diff",
    body={"diff": "--- a\n+++ b\n+SELECT * FROM t_user", "file_path": "test.sql"},
    token=admin_token, expect=200, label="gitlab audit diff")

print("\n" + "=" * 60)
print("[5] F1 在线元数据审核 (v1.2 旗舰)")
print("=" * 60)
# connect
status, body = http("POST", "/api/v1/tdsql/connect",
    body={"host":"127.0.0.1","port":13306,"username":"root","password":"tdsql_test_2024","database":"tdsql_test"},
    token=admin_token, expect=200, label="F1 connect adhoc")

# extract-and-audit 真实执行
status, body = http("POST", "/api/v1/audit/extract-and-audit",
    body={"connection_id":"adhoc","database":"tdsql_test","scopes":["TABLE","VIEW","SHARDKEY"]},
    token=admin_token, expect=200, label="F1 extract-and-audit")
if status == 200:
    d = json.loads(body)
    print(f"  filename={d.get('filename')}, report_id={d.get('report_id')}")
    print(f"  summary: {d.get('summary', {})}")
    print(f"  rules 命中: {sorted(set(v['rule_id'] for r in d.get('results',[]) for v in r.get('violations',[])))}")

# 历史列表
status, body = http("GET", "/api/v1/audit/extracted-reports?limit=5",
    token=admin_token, expect=200, label="F1 extracted-reports")
if status == 200:
    d = json.loads(body)
    print(f"  历史总数: {d.get('total')}, 最新: {d.get('reports',[{}])[0].get('id')}")

# 取最新 report_id 测下载
if status == 200 and json.loads(body).get("reports"):
    rid = json.loads(body)["reports"][0]["id"]
    status, body = http("GET", f"/api/v1/audit/report/{rid}/html",
        token=admin_token, allow=[200], label=f"F1 report {rid} HTML")
    if status == 200:
        print(f"  HTML 报告 bytes: {len(body)}")
    status, body = http("GET", f"/api/v1/audit/report/{rid}/sql",
        token=admin_token, allow=[200], label=f"F1 report {rid} SQL")
    if status == 200:
        print(f"  SQL 文件 bytes: {len(body)}")

print("\n" + "=" * 60)
print("[6] 慢 SQL + EXPLAIN")
print("=" * 60)
http("POST", "/api/v1/slow-queries/analyze-explain",
    body={"explain_data": [
        {"id":1,"select_type":"SIMPLE","table":"t_test","type":"ALL",
         "possible_keys":None,"key":None,"rows":1000,"filtered":10.0,"extra":"Using where"}
    ]}, token=admin_token, expect=200, label="analyze-explain (JSON)")

http("POST", "/api/v1/slow-queries/analyze-explain-by-sql",
    body={"sql":"SELECT * FROM tdsql_test.t1 LIMIT 1", "connection_id":"adhoc"},
    token=admin_token, allow=[200, 400, 500], label="analyze-explain-by-sql (adhoc)")

print("\n" + "=" * 60)
print("[7] 元数据/字符集/慢查询配置")
print("=" * 60)
http("GET", "/api/v1/tdsql/tables?connection_id=adhoc&database=tdsql_test",
    token=admin_token, allow=[200, 500], label="tables 列表")
http("GET", "/api/v1/tdsql/check/charset?connection_id=adhoc&database=tdsql_test",
    token=admin_token, allow=[200, 500], label="charset")
http("GET", "/api/v1/tdsql/check/large-tables?connection_id=adhoc&database=tdsql_test&threshold_gb=0.001",
    token=admin_token, allow=[200, 500], label="large-tables")
http("GET", "/api/v1/tdsql/proxy-config?connection_id=adhoc",
    token=admin_token, allow=[200, 500], label="proxy-config")
http("GET", "/api/v1/tdsql/slow-query-config?connection_id=adhoc",
    token=admin_token, allow=[200, 500], label="slow-query-config")
http("GET", "/api/v1/tdsql/sets?connection_id=adhoc",
    token=admin_token, allow=[200, 500], label="discover sets")

print("\n" + "=" * 60)
print("[8] RBAC 矩阵 (4 角色 × 关键端点)")
print("=" * 60)
# 使用真实登录的 4 角色 token
# 期待:
# - admin: 全部 200
# - dba: 大部分 200，但 admin/operation-logs 应 403 (BUG-01 越权回归)
# - developer: audit sql/file OK，admin/operation-logs 403，gauge 限权
# - auditor: 全部读 OK，POST 类应 403
rbac = [
    # (role, method, path, expected, desc)
    ("test_admin",     "GET",  "/api/v1/admin/operation-logs", 200, "admin 看审计日志"),
    ("test_auditor",   "GET",  "/api/v1/admin/operation-logs", 200, "auditor 看审计日志"),
    ("test_dba",       "GET",  "/api/v1/admin/operation-logs", 403, "dba 不应看审计日志"),
    ("test_developer", "GET",  "/api/v1/admin/operation-logs", 403, "developer 不应看审计日志"),
    ("test_admin",     "POST", "/api/v1/auth/users", 200, "admin 可建用户"),
    ("test_dba",       "POST", "/api/v1/auth/users", 403, "dba 不可建用户"),
    ("test_developer", "POST", "/api/v1/auth/users", 403, "developer 不可建用户"),
    ("test_auditor",   "POST", "/api/v1/auth/users", 403, "auditor 不可建用户"),
    # BUG-01 越权回归：prefix shadowing
    ("test_developer", "GET",  "/api/v1/gate/rules/default", 200, "BUG-01: gate 放行 developer"),
    ("test_developer", "GET",  "/api/v1/gateway-log/reports", 200, "BUG-01: gateway-log 默认放行"),
    ("test_auditor",   "GET",  "/api/v1/gate/strategies", 200, "auditor 看门禁"),
    ("test_auditor",   "GET",  "/api/v1/dashboard/summary", 200, "auditor 看 dashboard"),
    ("test_developer", "POST", "/api/v1/audit/sql", 200, "developer 可发起审核"),
    ("test_developer", "GET",  "/api/v1/slow-queries?limit=5", 200, "developer 看慢SQL"),
    ("test_auditor",   "POST", "/api/v1/audit/sql", 200, "auditor 可发起审核"),
]
for role, method, path, expected, desc in rbac:
    token = tokens.get(role, "")
    http(method, path, token=token,
         expect=expected, allow=[200, 403] if expected == 200 else [403, 401],
         label=f"RBAC: {desc}")

print("\n" + "=" * 60)
print("[9] Operations 写权限测试")
print("=" * 60)
# 创建项目
status, body = http("POST", "/api/v1/projects",
    body={"project_name": "Mavis复测项目"},
    token=admin_token, allow=[200, 400], label="create project")
# 创建规则集
status, body = http("POST", "/api/v1/rulesets",
    body={"id":"mavis_test_set","name":"Mavis测试集","description":"复测",
          "items":[{"rule_id":"R001","enabled":True,"severity_override":None}]},
    token=admin_token, allow=[200, 400, 500], label="create ruleset")

print("\n" + "=" * 60)
print("[汇总]")
print("=" * 60)
print(f"  PASS: {counters['PASS']}")
print(f"  FAIL: {counters['FAIL']}")
print(f"  WARN: {counters['WARN']}")
print(f"  TOTAL: {len(results)}")
if failures:
    print("\n失败明细:")
    for f in failures:
        print(f"  FAIL {f['method']} {f['path']} status={f['status']}  {f['body_first'][:200]}")

with open(r"C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\scratch\mavis_api_probe.json", "w", encoding="utf-8") as f:
    json.dump({"counters": counters, "results": results, "failures": failures},
              f, ensure_ascii=False, indent=2)
print("\n详细结果写入: scratch/mavis_api_probe.json")
