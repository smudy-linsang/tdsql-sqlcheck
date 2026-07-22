"""Mavis 全量 API 全覆盖扫描 - 24 个路由模块 × 所有端点"""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"


def login(u, p):
    req = urllib.request.Request(f"{BASE}/api/v1/auth/login",
        data=json.dumps({"username":u,"password":p}).encode(),
        headers={"Content-Type":"application/json"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=5).read().decode())["token"]
    except Exception:
        return None


token = login("test_admin", "Test@1234")
admin = login("admin", "Admin@1234")
tokens = {"test_admin": token, "admin": admin}
for r, p in [("test_dba","Test@1234"),("test_developer","Test@1234"),("test_auditor","Test@1234")]:
    tokens[r] = login(r, p)
print("Tokens:", {k: v[:20]+"..." if v else None for k,v in tokens.items()})

# 全路由表
ALL_ENDPOINTS = [
    # auth
    ("GET", "/api/v1/auth/me"),
    ("GET", "/api/v1/auth/roles"),
    ("GET", "/api/v1/auth/role-permissions"),
    ("GET", "/api/v1/auth/users"),
    ("GET", "/api/v1/auth/visible-menus"),
    # audit
    ("GET", "/api/v1/audit/rules"),
    ("GET", "/api/v1/audit/file-reports"),
    ("GET", "/api/v1/audit/extracted-reports"),
    # slow queries
    ("GET", "/api/v1/slow-queries"),
    ("GET", "/api/v1/slow-queries/statistics"),
    ("GET", "/api/v1/slow-queries/scan-tasks"),
    ("GET", "/api/v1/slow-queries/db-names"),
    ("GET", "/api/v1/slow-queries/set-ids"),
    ("GET", "/api/v1/slow-queries/orphan-records"),
    ("GET", "/api/v1/slow-queries/cross-set-analysis"),
    # tdsql
    ("GET", "/api/v1/tdsql/status"),
    ("GET", "/api/v1/tdsql/connections"),
    ("GET", "/api/v1/tdsql/scan-schedules"),
    # rules
    ("GET", "/api/v1/rules"),
    ("GET", "/api/v1/rules/categories"),
    # rulesets
    ("GET", "/api/v1/rulesets"),
    # project
    ("GET", "/api/v1/projects"),
    # gate
    ("GET", "/api/v1/gate/rules/default"),
    ("GET", "/api/v1/gate/strategies"),
    # monitor
    ("GET", "/api/v1/monitor/alerts"),
    ("GET", "/api/v1/monitor/rules"),
    # inspection
    ("GET", "/api/v1/inspection/tasks"),
    # admin
    ("GET", "/api/v1/admin/info"),
    ("GET", "/api/v1/admin/config"),
    ("GET", "/api/v1/admin/retention"),
    ("GET", "/api/v1/admin/operation-logs"),
    # dashboard
    ("GET", "/api/v1/dashboard/summary"),
    ("GET", "/api/v1/dashboard/audit-trend"),
    ("GET", "/api/v1/dashboard/rule-stats"),
    # deep-diag
    ("GET", "/api/v1/cluster-inspect/list/adhoc"),
    ("GET", "/api/v1/daily-inspect/trend?connection_id=adhoc"),
    ("GET", "/api/v1/sql-stats/bigtable/growth?connection_id=adhoc"),
    ("GET", "/api/v1/schema-diff/items/1"),
    # toolkit
    ("GET", "/api/v1/toolkit/scripts"),
    # gitlab
    ("GET", "/api/v1/gitlab/config"),
    # health/metrics
    ("GET", "/health"),
    ("GET", "/metrics"),
]

print(f"\n=== 用 test_admin token 跑 {len(ALL_ENDPOINTS)} 个端点 ===")
counts = {"2xx": 0, "401/403": 0, "404/500": 0, "other": 0}
for method, path in ALL_ENDPOINTS:
    req = urllib.request.Request(f"{BASE}{path}", method=method,
        headers={"Authorization": f"Bearer {tokens['test_admin']}"})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    if 200 <= status < 300:
        counts["2xx"] += 1
    elif status in (401, 403):
        counts["401/403"] += 1
        print(f"  401/403: {method} {path}")
    elif status in (404, 500):
        counts["404/500"] += 1
        print(f"  404/500: {method} {path}")
    else:
        counts["other"] += 1
        print(f"  {status}: {method} {path}")
print(f"\n  2xx: {counts['2xx']}, 401/403: {counts['401/403']}, 404/500: {counts['404/500']}, other: {counts['other']}")

# 测 dba 越权
print("\n=== dba 越权扫描 ===")
dba_overpriv = []
for method, path in ALL_ENDPOINTS:
    req = urllib.request.Request(f"{BASE}{path}", method=method,
        headers={"Authorization": f"Bearer {tokens['test_dba']}"})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    # 这些应该 403 的端点
    expected_403 = [
        "/api/v1/admin/operation-logs",  # 审计日志
        "/api/v1/auth/users",  # 用户管理
    ]
    if path in expected_403 and status == 200:
        dba_overpriv.append((method, path, status))
        print(f"  OVERPRIV: dba 可访问 {method} {path}  status={status}")
print(f"\n  dba 越权数: {len(dba_overpriv)}")

# 测 auditor 越权
print("\n=== auditor 越权扫描 ===")
aud_overpriv = []
for method, path in ALL_ENDPOINTS:
    if method == "GET":  # auditor 允许 GET
        continue
    req = urllib.request.Request(f"{BASE}{path}", method=method,
        headers={"Authorization": f"Bearer {tokens['test_auditor']}"})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    if status == 200:
        aud_overpriv.append((method, path, status))
        print(f"  OVERPRIV: auditor 可{method}  status={status}: {path}")
print(f"\n  auditor 越权数: {len(aud_overpriv)}")

# BUG-01 path shadowing 验证
print("\n=== BUG-01 路径碰撞验证 ===")
# 撤销 developer 的 deep-diag-gateway 后，应该 403，但 gate 仍 200
import sys
sys.path.insert(0, '.')
from backend.services.auth_service import set_role_permissions, get_visible_menus, _user_cache
set_role_permissions("developer", {
    "deep-diag-gateway": 0, "gate": 1, "deep-diag-emergency": 0, "deep-diag-sqlstats": 0
})
# 清缓存
from backend.services.auth_service import auth_service
_user_cache.clear()
auth_service.get_user("test_developer")
auth_service.get_user("test_auditor")
auth_service.get_user("test_dba")

# developer 访问 gate 仍应 200（路径不 shadow）
req = urllib.request.Request(f"{BASE}/api/v1/gate/rules/default", method="GET",
    headers={"Authorization": f"Bearer {tokens['test_developer']}"})
try:
    r = urllib.request.urlopen(req, timeout=10)
    print(f"  developer->/gate 期望 200, 实际: {r.status}", "PASS" if r.status==200 else "FAIL")
except urllib.error.HTTPError as e:
    print(f"  developer->/gate 期望 200, 实际: {e.code}", "FAIL")

# developer 访问 gateway-log 应 403
req = urllib.request.Request(f"{BASE}/api/v1/gateway-log/reports", method="GET",
    headers={"Authorization": f"Bearer {tokens['test_developer']}"})
try:
    r = urllib.request.urlopen(req, timeout=10)
    print(f"  developer->/gateway-log 期望 403, 实际: {r.status}", "FAIL")
except urllib.error.HTTPError as e:
    print(f"  developer->/gateway-log 期望 403, 实际: {e.code}", "PASS" if e.code==403 else "FAIL")

# 撤销 emergency-sqlstats
set_role_permissions("developer", {"deep-diag-emergency": 0})
_user_cache.clear()
auth_service.get_user("test_developer")
req = urllib.request.Request(f"{BASE}/api/v1/emergency/run", method="POST",
    headers={"Authorization": f"Bearer {tokens['test_developer']}",
    "Content-Type": "application/json"}, data=json.dumps({"connection_id":"adhoc"}).encode())
try:
    r = urllib.request.urlopen(req, timeout=10)
    print(f"  developer->/emergency 撤销后 期望 403, 实际: {r.status}", "FAIL")
except urllib.error.HTTPError as e:
    print(f"  developer->/emergency 撤销后 期望 403, 实际: {e.code}", "PASS" if e.code==403 else "FAIL")

# 恢复
set_role_permissions("developer", {"deep-diag-gateway": 1, "deep-diag-emergency": 1})
