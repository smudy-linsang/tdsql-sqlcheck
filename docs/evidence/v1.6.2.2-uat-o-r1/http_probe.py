"""Supplement browser evidence with authenticated local HTTP assertions."""
import json
import os
from pathlib import Path
import requests

HERE = Path(__file__).resolve().parent
BASE = os.environ.get("TDSQL_TEST_BASE_URL", "http://127.0.0.1:8002").rstrip("/")
if not BASE.startswith("http://127.0.0.1:"):
    raise SystemExit("This synthetic UAT runner only targets loopback")
PW = os.environ["UAT_O_PASSWORD"]
probe = json.loads((HERE / "rule_probe_current.json").read_text(encoding="utf-8"))
results = []


def session(username):
    s = requests.Session()
    response = s.post(BASE + "/api/v1/auth/login", json={"username":username,"password":PW},timeout=10)
    response.raise_for_status()
    s.headers["Authorization"] = "Bearer " + response.json()["token"]
    return s


s = session("admin")
for endpoint in ("/health", "/api/v1/audit/rules", "/api/v1/rules", "/api/v1/tdsql/connections/options"):
    r = s.get(BASE + endpoint, timeout=20)
    results.append({"endpoint":endpoint,"status":r.status_code,"body":r.json()})
for row in probe["rows"]:
    if not (row["id"].startswith("corpus:") or row["id"].startswith("kfn_comment:") or row["id"].startswith("fixture:")):
        continue
    r = s.post(BASE + "/api/v1/audit/sql", json={"sql":row["sql"],"instance_type":row["instance_type"]},timeout=30)
    body = r.json()
    actual = sorted({v["rule_id"] for v in body.get("violations", [])})
    results.append({"id":row["id"],"endpoint":"/api/v1/audit/sql","status":r.status_code,"engine_equals_http":row.get("fired")==actual,"fired":actual,"passed":body.get("passed")})
guard = next(r for r in probe["rows"] if r["id"] == "kfn_comment:0:block")
for kind in ("file","upload"):
    if kind == "file":
        r = s.post(BASE + "/api/v1/audit/file",json={"content":guard["sql"],"file_path":"uat_guard.sql","instance_type":"distributed"},timeout=30)
    else:
        r = s.post(BASE + "/api/v1/audit/upload",files={"file":("uat_guard.sql",guard["sql"],"text/plain")},data={"instance_type":"distributed"},timeout=30)
    results.append({"id":"kfn-guard-" + kind,"status":r.status_code,"body":r.json()})
for user in ("uat_o_developer", "uat_o_auditor", "uat_o_dba"):
    rs = session(user)
    for endpoint in ("/api/v1/auth/me", "/api/v1/tdsql/connections/options", "/api/v1/tdsql/connections"):
        r = rs.get(BASE + endpoint,timeout=15)
        results.append({"role_user":user,"method":"GET","endpoint":endpoint,"status":r.status_code,"body":r.json()})
    if user != "uat_o_dba":
        r = rs.post(BASE + "/api/v1/tdsql/connections",json={"name":"uat_forbidden","host":"127.0.0.1","port":1,"username":"synthetic","password":""},timeout=15)
        results.append({"role_user":user,"method":"POST","endpoint":"/api/v1/tdsql/connections","status":r.status_code,"body":r.json()})
(HERE / "http_results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
print("HTTP_CHECKS", len(results))
print("ENGINE_HTTP_MISMATCHES",sum(r.get("engine_equals_http") is False for r in results))
print("STATUS_5XX",sum(r.get("status",0)>=500 for r in results))
