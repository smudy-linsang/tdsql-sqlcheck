# -*- coding: utf-8 -*-
"""复测专项实证：S08 CSP / S10 白名单 / R01 映射补齐（针对运行中的 8899 服务）"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8899"


def req(method, path, body=None, token=None, raw_headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def login(u, p):
    st, _, body = req("POST", "/api/v1/auth/login", {"username": u, "password": p})
    return json.loads(body).get("token") if st == 200 else None


print("=" * 60)
print("S08-1 首页安全响应头实测")
st, hdrs, html = req("GET", "/")
hdrs = {k.lower(): v for k, v in hdrs.items()}
csp = hdrs.get("content-security-policy", "")
print(f"  GET / -> {st}, html_len={len(html)}")
print(f"  CSP: {csp[:200]}")
print(f"  nosniff: {hdrs.get('x-content-type-options')}, frame: {hdrs.get('x-frame-options')}")
assert hdrs.get("x-content-type-options") == "nosniff"
assert hdrs.get("x-frame-options") == "DENY"
assert "'unsafe-eval'" in csp and "sha256-" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0], "script-src 不应含 unsafe-inline"
assert "frame-ancestors 'none'" in csp and "base-uri 'self'" in csp and "form-action 'self'" in csp
print("  [OK] script-src 无 unsafe-inline，含 unsafe-eval+sha256，frame/base/form 均收紧")

print("=" * 60)
print("S08-2 首页 HTML 完整性（#app 挂载点 + 内联脚本存在，粗验非白屏）")
assert 'id="app"' in html, "#app 挂载点缺失"
import re, hashlib, base64
m = re.search(r"<script>(.*?)</script>", html, re.S)
digest = base64.b64encode(hashlib.sha256(m.group(1).encode()).digest()).decode()
declared = re.search(r"sha256-([A-Za-z0-9+/=]+)", csp).group(1)
print(f"  内联脚本实际 sha256: {digest}")
print(f"  CSP 声明 sha256:     {declared}")
assert digest == declared, "运行服务下发的 CSP 哈希与实际内联脚本不一致！"
print("  [OK] 运行服务实际哈希与 CSP 声明一致（浏览器不会拒绝执行）")

print("=" * 60)
print("S10-1 白名单外端点 URL 传 token 必须 401")
tok = login("admin", "Admin@1234")
assert tok, "admin 登录失败"
for path in ("/api/v1/dashboard/summary", "/api/v1/rules", "/api/v1/auth/users",
             "/api/v1/slow-queries", "/api/v1/admin/operation-logs"):
    st, _, _ = req("GET", f"{path}?access_token={tok}")
    assert st == 401, f"白名单外 {path}?access_token= 返回 {st}（应 401）"
    print(f"  [OK] {path}?access_token= -> 401")

print("=" * 60)
print("S10-2 白名单内导出端点 URL 传 token 仍可用（不能 401）")
# audit 报告 html 导出属白名单（/api/v1/audit/ 前缀 + /html 后缀）
st, _, body = req("GET", f"/api/v1/audit/report/1/html?access_token={tok}")
print(f"  /api/v1/audit/report/1/html?access_token= -> {st}（404/200 均证明 token 被接受）")
assert st != 401, "导出端点 URL token 未被接受，导出功能被误伤"
# 无 token 访问同一端点仍须 401（白名单只是允许 URL 传参，不是免认证）
st2, _, _ = req("GET", "/api/v1/audit/report/1/html")
assert st2 == 401, f"导出端点无 token 应 401，实际 {st2}"
print("  [OK] 导出端点认 URL token，且无 token 仍 401（未放开认证）")

print("=" * 60)
print("R01-1 DBA 关键写操作不因映射补齐被误伤")
dba = login("t3p_dba", "T3p#Passw0rd2026")
assert dba, "t3p_dba 登录失败"
# test-connection 是 R01 声明补齐的 14 个写端点之一（映射 instances 菜单）
st, _, body = req("POST", "/api/v1/tdsql/test-connection",
                  {"host": "127.0.0.1", "port": 1, "username": "x", "password": "x"},
                  token=dba)
print(f"  DBA POST /tdsql/test-connection -> {st}（连接失败属正常，不能是 403）")
assert st != 403, "DBA 被 403 拦截，R01 修复误伤业务！"
assert st != 401, "DBA 被 401 拦截"
print("  [OK] DBA 可调用 test-connection（权限正常，仅目标不可达）")

print("=" * 60)
print("R01-2 auditor 越权写仍被 403（补齐映射未放开最小权限）")
aud = login("t3p_auditor", "T3p#Passw0rd2026")
st, _, _ = req("POST", "/api/v1/tdsql/test-connection",
               {"host": "127.0.0.1", "port": 1, "username": "x", "password": "x"},
               token=aud)
assert st == 403, f"auditor 调 test-connection 应 403，实际 {st}"
print(f"  [OK] auditor POST /tdsql/test-connection -> 403")

print("=" * 60)
print("全部专项复测通过")
