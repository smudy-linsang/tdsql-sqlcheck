"""
Mavis 复测: 验证 G 的 BUG-RBAC-01 修复
目标: dba 角色访问 /api/v1/admin/operation-logs 应返回 403
      auditor 角色访问同一接口应返回 200
      admin 仍可访问 (200)
      developer 仍 403
"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"

USERS = [
    ("admin",    "Admin@1234"),
    ("test_dba", "Test@1234"),
    ("test_developer", "Test@1234"),
    ("test_auditor",   "Test@1234"),
]

def login(u, p):
    req = urllib.request.Request(
        f"{BASE}/api/v1/auth/login",
        data=json.dumps({"username": u, "password": p}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["token"]

def probe(token, path, method="GET"):
    req = urllib.request.Request(
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200]

# ---- 关键探针 ----
print("=" * 60)
print("复测 #1: BUG-RBAC-01 — /api/v1/admin/operation-logs")
print("=" * 60)
tokens = {}
for u, p in USERS:
    tokens[u] = login(u, p)
    print(f"  login {u}: OK")

print()
results = []
for u in ("admin", "test_dba", "test_developer", "test_auditor"):
    code, body = probe(tokens[u], "/api/v1/admin/operation-logs")
    results.append((u, code, body))
    print(f"  {u:20s} -> {code}")

print()
print("期望:")
print("  admin        -> 200  (允许)")
print("  test_dba     -> 403  (BUG-RBAC-01 修复验证)")
print("  test_developer -> 403 (本就一直禁止)")
print("  test_auditor -> 200  (允许)")

# ---- 数据库 role_permissions 验证 ----
print()
print("=" * 60)
print("复测 #2: 数据库 role_permissions — sys-auditlog 可见性")
print("=" * 60)

import pymysql
conn = pymysql.connect(host="127.0.0.1", port=13306, user="root", password="root", database="tdsql_sqlcheck_test")
try:
    with conn.cursor() as cur:
        cur.execute("SELECT role_id, visible FROM role_permissions WHERE menu_key='sys-auditlog' ORDER BY role_id")
        for row in cur.fetchall():
            print(f"  role={row[0]:10s} visible={row[1]}")
finally:
    conn.close()

# ---- 复测 ISSUE-01/02/03/04 (G 应该都已经清理) ----
print()
print("=" * 60)
print("复测 #3: 死代码 / 双分号清理")
print("=" * 60)
import os, re
issues = []

# ISSUE-01: backend/connectors/ 应该是死代码
conn_dir = "backend/connectors"
if os.path.isdir(conn_dir):
    issues.append(f"ISSUE-01: {conn_dir} 仍存在 (G 未清理)")

# ISSUE-02: sql_audit.py:166 死代码
with open("backend/api/sql_audit.py", encoding="utf-8") as f:
    content = f.read()
if "tables = fetcher.fetch_databases()" in content:
    issues.append("ISSUE-02: tables = fetcher.fetch_databases() 仍存在")
else:
    print("  ISSUE-02: 已清理 ✓")

# ISSUE-03: DDL 双分号
if "create_sql.rstrip(';')" in content:
    print("  ISSUE-03: 已加 rstrip(';') ✓")
else:
    issues.append("ISSUE-03: 未加 rstrip(';')")

# ISSUE-04: visible-menus 白名单
with open("backend/main.py", encoding="utf-8") as f:
    main_content = f.read()
if "visible-menus" in main_content and "is_public_path" in main_content:
    # 看 visible-menus 是不是在 is_public_path 列表里
    import re as _re
    m = _re.search(r"is_public_path\s*=\s*\(([^)]+)\)", main_content, _re.DOTALL)
    if m and "visible-menus" in m.group(1):
        print("  ISSUE-04: 已加入 is_public_path ✓")
    else:
        issues.append("ISSUE-04: visible-menus 未在 is_public_path (低危)")

# auth_service.py 是否包含 BUG-RBAC-01 修复
with open("backend/services/auth_service.py", encoding="utf-8") as f:
    auth_content = f.read()
if "_ADMIN_AUDITOR_ONLY_PREFIXES" in auth_content and "operation-logs" in auth_content:
    print("  BUG-RBAC-01: auth_service.py 包含新白名单 ✓")
else:
    issues.append("BUG-RBAC-01: auth_service.py 未见 _ADMIN_AUDITOR_ONLY_PREFIXES")

if issues:
    print()
    print("⚠️ 残留问题:")
    for i in issues:
        print(f"  - {i}")
else:
    print()
    print("✅ 所有问题已修复")
