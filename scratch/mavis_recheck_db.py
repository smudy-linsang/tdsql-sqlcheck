"""通过 admin HTTP API 验证 role_permissions 中 sys-auditlog 的可见性"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"

def login(u, p):
    req = urllib.request.Request(
        f"{BASE}/api/v1/auth/login",
        data=json.dumps({"username": u, "password": p}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["token"]

# 用 admin 看 auditlog 可见性
admin_token = login("admin", "Admin@1234")

# 1. 查当前 admin 可见菜单
req = urllib.request.Request(
    f"{BASE}/api/v1/auth/visible-menus",
    headers={"Authorization": f"Bearer {admin_token}"},
)
with urllib.request.urlopen(req, timeout=10) as r:
    menus = json.loads(r.read())
print("admin 可见菜单 keys:", menus if isinstance(menus, list) else menus.get("menus", menus))

# 2. 4 角色登录后查看各自的可见菜单
for u, p in [("admin", "Admin@1234"),
             ("test_dba", "Test@1234"),
             ("test_developer", "Test@1234"),
             ("test_auditor", "Test@1234")]:
    tok = login(u, p)
    req = urllib.request.Request(
        f"{BASE}/api/v1/auth/visible-menus",
        headers={"Authorization": f"Bearer {tok}"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.loads(r.read())
    if isinstance(d, list):
        keys = [m.get("key") or m.get("menu_key") for m in d]
    else:
        keys = d.get("menus", d)
    has_audit = "sys-auditlog" in keys
    print(f"  {u:20s} sys-auditlog 可见: {has_audit}")
