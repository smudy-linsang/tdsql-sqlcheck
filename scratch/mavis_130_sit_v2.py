"""
Mavis V1.3.0.0 测试套件 v2 (修正字段名)
- 实际: API 用 'reports' 字段 (不是 'items')
"""
import json
import time
import urllib.request
import urllib.error
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "http://127.0.0.1:8000"
results = []

def req(method, path, token=None, body=None, timeout=30, raw=False):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            body_bytes = resp.read()
            if raw:
                return resp.status, body_bytes
            try:
                return resp.status, json.loads(body_bytes)
            except Exception:
                return resp.status, body_bytes[:300].decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body_bytes = e.read()[:300]
        try:
            return e.code, json.loads(body_bytes)
        except Exception:
            return e.code, body_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)

def login(u, p):
    code, d = req("POST", "/api/v1/auth/login", body={"username": u, "password": p})
    if code != 200:
        raise RuntimeError(f"login {u} failed: {code} {d}")
    return d["token"]

def record(round_name, name, passed, detail=""):
    icon = "✅" if passed else "❌"
    print(f"  [{round_name}] {icon} {name}{('  — ' + detail) if detail else ''}")
    results.append((round_name, name, passed, detail))

# 准备
print("=" * 70)
print("准备")
print("=" * 70)
admin_tok = login("admin", "Admin@1234")
dba_tok = login("test_dba", "Test@1234")
dev_tok = login("test_developer", "Test@1234")
aud_tok = login("test_auditor", "Test@1234")
print(f"  4 角色登录: OK")

# 注册连接
code, d = req("POST", "/api/v1/tdsql/connect", token=admin_tok, body={
    "host": "127.0.0.1", "port": 13306, "username": "root",
    "password": "tdsql_test_2024", "database": "tdsql_test"
})
print(f"  连接: {code}")

# ====== SIT R1 ======
print()
print("=" * 70)
print("[SIT R1] 7 个核心接口 + 4 角色 RBAC")
print("=" * 70)

# 1.1 GET /snapshots
for rname, tok in [("admin", admin_tok), ("dba", dba_tok), ("developer", dev_tok), ("auditor", aud_tok)]:
    c, d = req("GET", "/api/v1/scan-compare/snapshots?module=schema_audit&limit=5", token=tok)
    if rname == "developer":
        record("SIT R1", f"{rname} 越权", c == 403, f"实测 {c}")
    else:
        record("SIT R1", f"{rname} GET /snapshots", c == 200, f"total={d.get('total') if isinstance(d, dict) else '?'}")

# 1.2 错误码 E4006 (A 自定义)
code, d = req("GET", "/api/v1/scan-compare/snapshots?module=invalid", token=admin_tok)
record("SIT R1", "module=invalid → 400 (E4006)", code == 400, f"实测 {code} {d.get('detail', d) if isinstance(d, dict) else d}")

# 1.3 缺 module — 注意 FastAPI 会自动返回 422, 不是 A 的 400 E4006
code, d = req("GET", "/api/v1/scan-compare/snapshots", token=admin_tok)
record("SIT R1", "缺 module → 期望 400 E4006, 实际 FastAPI 422", code == 400, f"实测 {code} (422 是 FastAPI 自动校验)")
# 这是设计文档与实现不一致: 文档说要 400 (E4006), 实际 422

# 1.4 POST /compare 各种错误
code, d = req("POST", "/api/v1/scan-compare/compare", token=admin_tok, body={"module": "schema_audit", "snapshot_ids": [1]})
record("SIT R1", "snapshot_ids=1 → 400 (E4001)", code == 400, f"实测 {code}")

code, d = req("POST", "/api/v1/scan-compare/compare", token=admin_tok, body={"module": "schema_audit", "snapshot_ids": [1,1]})
record("SIT R1", "snapshot_ids=[1,1] → 400 (E4002)", code == 400, f"实测 {code}")

code, d = req("POST", "/api/v1/scan-compare/compare", token=admin_tok, body={"module": "schema_audit", "snapshot_ids": [999999, 999998]})
record("SIT R1", "不存在 ID → 404 (E4004)", code == 404, f"实测 {code} {d.get('detail', {}).get('code', d) if isinstance(d, dict) else d}")

# 1.5 POST /snapshots/rebuild 角色
for rname, tok in [("admin", admin_tok), ("dba", dba_tok), ("developer", dev_tok), ("auditor", aud_tok)]:
    c, d = req("POST", "/api/v1/scan-compare/snapshots/rebuild", token=tok, body={"module": "bigtable", "limit": 1})
    if rname in ("admin", "dba"):
        record("SIT R1", f"{rname} /snapshots/rebuild", c == 200, f"实测 {c}")
    else:
        record("SIT R1", f"{rname} /snapshots/rebuild 403", c == 403, f"实测 {c}")

# 1.6 DELETE /reports 角色
for rname, tok in [("admin", admin_tok), ("dba", dba_tok), ("auditor", aud_tok)]:
    c, d = req("DELETE", "/api/v1/scan-compare/reports/999999", token=tok)
    if rname == "admin":
        record("SIT R1", f"{rname} DELETE 不存在 ID → 404", c == 404, f"实测 {c}")
    else:
        record("SIT R1", f"{rname} DELETE /reports → 403", c == 403, f"实测 {c}")

# 1.7 GET /compare/html 错误路径
code, d = req("GET", "/api/v1/scan-compare/compare/html?module=schema_audit&snapshot_ids=1", token=admin_tok)
record("SIT R1", "GET /compare/html 1 个 ID → 400", code == 400, f"实测 {code}")

# ====== SIT R2 端到端 ======
print()
print("=" * 70)
print("[SIT R2] 端到端: extract-and-audit → 比对 → HTML")
print("=" * 70)
# 2.1 跑 2 次
print("  跑第 1 次...")
code, d = req("POST", "/api/v1/audit/extract-and-audit", token=admin_tok, body={
    "connection_id": "adhoc", "database": "tdsql_test", "scopes": ["TABLE"]
}, timeout=120)
snap1 = d.get("snapshot_id")
record("SIT R2", f"extract-and-audit #1 (report_id={d.get('report_id')} snap={snap1})", code == 200 and snap1 is not None, f"实测 {code}")

print("  跑第 2 次...")
code, d = req("POST", "/api/v1/audit/extract-and-audit", token=admin_tok, body={
    "connection_id": "adhoc", "database": "tdsql_test", "scopes": ["TABLE"]
}, timeout=120)
snap2 = d.get("snapshot_id")
record("SIT R2", f"extract-and-audit #2 (snap={snap2})", code == 200 and snap2 is not None, f"实测 {code}")

# 2.2 compare
if snap1 and snap2:
    code, d = req("POST", "/api/v1/scan-compare/compare", token=admin_tok, body={
        "module": "schema_audit", "snapshot_ids": [snap1, snap2]
    })
    if code == 200 and isinstance(d, dict):
        s = d.get("summary", {})
        record("SIT R2", "POST /compare", True, f"base={s.get('base_total')} target={s.get('target_total')} fixed={s.get('fixed_count')} new={s.get('new_count')}")
        record("SIT R2", "✅ 指纹稳定性: 0 修复 0 新增", s.get('fixed_count') == 0 and s.get('new_count') == 0)
        # 字段完整性
        for k in ["base", "target", "summary", "labels", "warnings", "fixed", "new", "remain", "changed"]:
            record("SIT R2", f"返回字段 {k}", k in d)
        record("SIT R2", f"labels.fixed", d.get("labels", {}).get("fixed") == "已修复", f"实测 {d.get('labels', {}).get('fixed')!r}")

        # 2.3 HTML
        t0 = time.time()
        code, d = req("GET", f"/api/v1/scan-compare/compare/html?module=schema_audit&snapshot_ids={snap1}&snapshot_ids={snap2}", token=admin_tok, raw=True)
        t1 = time.time()
        if code == 200 and isinstance(d, bytes):
            html = d.decode("utf-8", errors="replace")
            record("SIT R2", f"GET /compare/html 200 ({(t1-t0)*1000:.0f}ms, {len(d)} 字节)", len(html) > 1000)
            record("SIT R2", "HTML 含报告头", "TDSQL" in html or "扫描结果对比" in html)
        else:
            record("SIT R2", f"GET /compare/html 失败", code == 200, f"实测 {code}")
    else:
        record("SIT R2", f"POST /compare 失败", code == 200, f"实测 {code}")

# ====== SIT R3 边界 ======
print()
print("=" * 70)
print("[SIT R3] 边界与异常")
print("=" * 70)

# 3.1 性能
if snap1 and snap2:
    t0 = time.time()
    code, d = req("POST", "/api/v1/scan-compare/compare", token=admin_tok, body={"module": "schema_audit", "snapshot_ids": [snap1, snap2]})
    t1 = time.time()
    record("SIT R3", f"二次 compare (cache hit) < 2s", (t1-t0) < 2, f"实测 {(t1-t0)*1000:.0f}ms")

# 3.2 跨模块
code, d = req("POST", "/api/v1/scan-compare/compare", token=admin_tok, body={"module": "slow_scan", "snapshot_ids": [snap1 or 1, snap2 or 2]})
record("SIT R3", "跨模块 → 400 (E4003)", code == 400, f"实测 {code}")

# 3.3 无 token
code, d = req("GET", "/api/v1/scan-compare/snapshots?module=schema_audit")
record("SIT R3", "无 token → 401", code == 401, f"实测 {code}")

# ====== UAT R1 D1/D2/D4 修复 ======
print()
print("=" * 70)
print("[UAT R1] D1/D2/D4 修复验证")
print("=" * 70)

# D1: 重新跑一次 extract-and-audit, 然后查列表
code, d = req("POST", "/api/v1/audit/extract-and-audit", token=admin_tok, body={
    "connection_id": "adhoc", "database": "tdsql_test", "scopes": ["TABLE"]
})
new_rep_id = d.get("report_id")
new_snap_id = d.get("snapshot_id")
record("UAT R1", f"D1 验证: extract-and-audit 写入 ({new_rep_id}, {new_snap_id})", code == 200 and new_snap_id is not None, f"实测 {code}")

# 关键: API 返回的字段名是 'reports' 不是 'items'!
code, d = req("GET", "/api/v1/audit/extracted-reports?connection_id=adhoc&limit=10", token=admin_tok)
if isinstance(d, dict):
    # ⚠️ 关键发现: 实际字段是 'reports' 不是设计文档的 'items'
    records_list = d.get("reports") or d.get("items") or []
    actual_field = "reports" if d.get("reports") is not None else "items"
    record("UAT R1", f"⚠️ API 字段命名: {actual_field!r} (设计文档约定 'items')", actual_field == "items",
           f"实测: A 用 {actual_field!r}, 文档说 'items' — 字段不一致, 前端按文档会拿不到数据")
    record("UAT R1", f"D1 实际: total={d.get('total')} records={len(records_list)}", len(records_list) > 0)
    if records_list:
        all_adhoc = all(r.get("connection_id") == "adhoc" for r in records_list)
        record("UAT R1", f"D1: 全部 connection_id=adhoc", all_adhoc, f"首条: id={records_list[0].get('id')} conn={records_list[0].get('connection_id')!r} db={records_list[0].get('db_name')!r}")
        has_db = all("db_name" in r for r in records_list)
        has_conn = all("connection_id" in r for r in records_list)
        record("UAT R1", f"D1: 都有 db_name 字段", has_db)
        record("UAT R1", f"D1: 都有 connection_id 字段", has_conn)

# D2: get_inventory 默认只返回最近一次
code, d = req("GET", "/api/v1/bigtable/inventory/adhoc", token=admin_tok)
if isinstance(d, dict):
    items = d.get("data", [])
    record("UAT R1", f"D2: get_inventory 返回 {len(items)} 条", len(items) >= 0)
    if items:
        dates = set(it.get("inspection_date") for it in items)
        record("UAT R1", f"D2: 单日期 (无跨日期混合)", len(dates) <= 1, f"dates={dates}")

# D4: scan-tasks 筛选 + connection_name 字段
# 先创建一个扫描任务
code, d = req("POST", "/api/v1/slow-queries/trigger-scan", token=admin_tok, body={
    "connection_id": "adhoc", "db_name": "tdsql_test", "limit": 5
}, timeout=120)
record("UAT R1", f"D4: 触发扫描任务", code == 200, f"实测 {code} {str(d)[:200]}")
# 再查
code, d = req("GET", "/api/v1/slow-queries/scan-tasks?connection_id=adhoc&limit=5", token=admin_tok)
if isinstance(d, dict):
    tasks = d.get("tasks", [])
    record("UAT R1", f"D4: scan-tasks 筛选返回 {len(tasks)} 条", len(tasks) > 0, f"total={d.get('total')}")
    if tasks:
        has_name = "connection_name" in tasks[0]
        record("UAT R1", f"D4: tasks[0] 有 connection_name 字段", has_name, f"keys={list(tasks[0].keys())[:8]}")

# ====== UAT R2 报告留档端到端 ======
print()
print("=" * 70)
print("[UAT R2] 报告留档端到端")
print("=" * 70)
if snap1 and snap2:
    code, d = req("POST", "/api/v1/scan-compare/reports", token=admin_tok, body={
        "module": "schema_audit", "snapshot_ids": [snap1, snap2], "title": "Mavis v1.3.0.0 测试"
    })
    rid = d.get("id")
    record("UAT R2", f"POST /reports 创建留档 (id={rid})", code == 200 and rid is not None, f"实测 {code}")

    code, d = req("GET", "/api/v1/scan-compare/reports?module=schema_audit&limit=5", token=admin_tok)
    if isinstance(d, dict):
        items = d.get("items") or d.get("reports") or d.get("data") or []
        record("UAT R2", f"GET /reports 列表 {len(items)} 条", len(items) > 0, f"total={d.get('total')}")
        # ⚠️ 同样检查字段名
        for k in ("items", "reports", "data"):
            if k in d:
                record("UAT R2", f"⚠️ /reports 响应字段: {k!r}", k == "items", f"实际 {k!r}, 文档 items")

    if rid:
        code, d = req("DELETE", f"/api/v1/scan-compare/reports/{rid}", token=dba_tok)
        record("UAT R2", f"DELETE /reports/{rid} (dba) → 403", code == 403, f"实测 {code}")
        code, d = req("DELETE", f"/api/v1/scan-compare/reports/{rid}", token=admin_tok)
        record("UAT R2", f"DELETE /reports/{rid} (admin) → 200", code == 200, f"实测 {code}")

# ====== UAT R3 关键模块回归 ======
print()
print("=" * 70)
print("[UAT R3] 关键模块回归")
print("=" * 70)
critical_checks = [
    ("GET", "/api/v1/rules", 200),
    ("GET", "/api/v1/dashboard/summary", 200),
    ("GET", "/api/v1/inspection/tasks?limit=3", 200),
    ("GET", "/api/v1/admin/operation-logs?limit=3", 200),
    ("GET", "/api/v1/auth/visible-menus", 200),
    ("GET", "/api/v1/tdsql/status", 200),
    ("GET", "/api/v1/audit/file-reports?limit=3", 200),
    ("GET", "/api/v1/audit/extracted-reports?limit=3", 200),
    ("GET", "/api/v1/slow-queries?limit=3", 200),
    ("GET", "/api/v1/slow-queries/statistics", 200),
    ("GET", "/api/v1/rulesets", 200),
    ("GET", "/api/v1/projects", 200),
    ("GET", "/api/v1/admin/info", 200),
    ("GET", "/api/v1/admin/retention", 200),
    ("GET", "/api/v1/monitor/alerts", 200),
    ("GET", "/api/v1/toolkit/scripts", 200),
]
for method, path, exp in critical_checks:
    c, d = req(method, path, token=admin_tok)
    record("UAT R3", f"{method} {path}", c == exp, f"实测 {c}")

# BUG-RBAC-01 回归
c, d = req("GET", "/api/v1/admin/operation-logs?limit=3", token=dba_tok)
record("UAT R3", "BUG-RBAC-01 回归: dba operation-logs = 403", c == 403, f"实测 {c}")
c, d = req("GET", "/api/v1/admin/operation-logs?limit=3", token=aud_tok)
record("UAT R3", "auditor operation-logs = 200", c == 200, f"实测 {c}")

# 汇总
print()
print("=" * 70)
print("[汇总]")
print("=" * 70)
total = len(results)
passed = sum(1 for _, _, p, _ in results if p)
print(f"  PASS: {passed}/{total}  ({100*passed/total:.1f}%)")
fails = [(r, n, d) for r, n, p, d in results if not p]
if fails:
    print(f"  ❌ FAIL 列表 ({len(fails)}):")
    for r, n, d in fails:
        print(f"    [{r}] {n}  {d}")

import json as _json
with open("scratch/mavis_130_test_results_v2.json", "w", encoding="utf-8") as f:
    _json.dump({
        "version": "1.3.0.0",
        "results": [{"round": r, "name": n, "passed": p, "detail": d} for r, n, p, d in results],
        "summary": {"total": total, "passed": passed, "failed": total - passed}
    }, f, ensure_ascii=False, indent=2)
print(f"\n详细结果: scratch/mavis_130_test_results_v2.json")
