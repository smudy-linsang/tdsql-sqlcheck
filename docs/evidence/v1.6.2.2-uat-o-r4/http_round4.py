"""Authenticated loopback contract tests for fourth-round UAT."""
import json
import os
from pathlib import Path
import requests

HERE = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8004"
PASSWORD = os.environ["UAT_O_PASSWORD"]


def login(username):
    session = requests.Session()
    response = session.post(BASE + "/api/v1/auth/login",
                            json={"username": username, "password": PASSWORD}, timeout=15)
    response.raise_for_status()
    session.headers["Authorization"] = "Bearer " + response.json()["token"]
    return session


def record(response, label, **extra):
    try:
        body = response.json()
    except Exception:
        body = {"content_type": response.headers.get("content-type"),
                "bytes": len(response.content)}
    return {"label": label, "status": response.status_code, "body": body, **extra}


admin = login("uat_o_admin_r4")
rows = []
audit_cases = {
    "r042_hash_comment": "# operator's note\nLOAD XML INFILE '/tmp/synthetic.xml' INTO TABLE t",
    "view_cr_fail_open": "-- ordinary\rCREATE VIEW v AS SELECT 1 +",
    "load_leading_semicolon": "; LOAD XML INFILE '/tmp/synthetic.xml' INTO TABLE t",
    "load_literal_decoy": "'decoy' LOAD XML INFILE '/tmp/synthetic.xml' INTO TABLE t",
}
for label, sql in audit_cases.items():
    response = admin.post(BASE + "/api/v1/audit/sql",
                          json={"sql": sql, "instance_type": "distributed"}, timeout=30)
    body = response.json()
    rows.append({"label": label, "entry": "instant", "status": response.status_code,
                 "passed": body.get("passed"),
                 "fired": sorted({v["rule_id"] for v in body.get("violations", [])})})
    response = admin.post(BASE + "/api/v1/audit/upload",
                          data={"instance_type": "distributed"},
                          files={"file": (label + ".sql", sql.encode("utf-8"), "text/plain")}, timeout=30)
    body = response.json()
    rows.append({"label": label, "entry": "upload", "status": response.status_code,
                 "body": body})

for label, content in (
    ("gateway_empty", b""),
    ("gateway_invalid", b"not a gateway log\nnot another log\n"),
    ("gateway_mixed", b"invalid one\n[2026-08-29 00:00:00 100] INFO topic=test&timecost=2.5&sql=select 1&db=synthetic&user=uat\ninvalid two\n"),
):
    response = admin.post(BASE + "/api/v1/gateway-log/upload",
                          data={"connection_id": "uat_o_offline", "log_type": "interf"},
                          files={"file": (label + ".log", content, "text/plain")}, timeout=60)
    rows.append(record(response, label, input_lines=len(content.splitlines())))

marker = "UAT_OR4_INLINE_EXECUTED"
payload = ("[2026-08-29 00:00:02 12346] INFO topic=test&timecost=1500.2&"
           "sql=select 1 /* </script><script>document.documentElement.dataset.uatOr4='" + marker +
           "'</script><script> */&db=synthetic&user=uat\n").encode("utf-8")
response = admin.post(BASE + "/api/v1/gateway-log/upload",
                      data={"connection_id": "uat_o_offline", "log_type": "interf"},
                      files={"file": ("gateway_xss_canary.log", payload, "text/plain")}, timeout=60)
xss_row = record(response, "gateway_xss_canary")
if response.ok:
    report_id = response.json()["report_id"]
    detail = admin.get(BASE + f"/api/v1/gateway-log/reports/{report_id}", timeout=20).json()
    document = admin.get(BASE + f"/api/v1/gateway-log/reports/{report_id}/html", timeout=20)
    xss_row.update({
        "report_id": report_id,
        "marker_in_persisted_html": marker in detail.get("report_html", ""),
        "raw_script_breakout_in_html": "</script><script>document.documentElement.dataset.uatOr4" in detail.get("report_html", ""),
        "report_csp": document.headers.get("content-security-policy"),
        "x_frame_options": document.headers.get("x-frame-options"),
    })
rows.append(xss_row)

response = admin.post(BASE + "/api/v1/index-audit/run",
                      json={"connection_id": "uat_o_index", "database": "tdsql_uat_o_r4_index_target"},
                      timeout=90)
rows.append(record(response, "index_audit"))
response = admin.get(BASE + "/api/v1/ppt-report/dashboard",
                     params={"connection_id": "uat_o_index"}, timeout=30)
dashboard = record(response, "index_dashboard")
rows.append(dashboard)
response = admin.get(BASE + "/api/v1/ppt-report/generate",
                     params={"connection_id": "uat_o_index"}, timeout=60)
(HERE / "index_actual.pdf").write_bytes(response.content)
rows.append(record(response, "index_pdf"))

for db_name in (None, "tdsql_uat_o_target_1622", "tdsql_uat_o_missing_r4"):
    payload = {"sql": "SELECT id FROM t_uat_order WHERE customer_id=1",
               "connection_id": "uat_o_local"}
    if db_name:
        payload["db_name"] = db_name
    response = admin.post(BASE + "/api/v1/slow-queries/analyze-explain-by-sql",
                          json=payload, timeout=30)
    rows.append(record(response, "explain_" + (db_name or "default")))

for username in ("uat_o_developer", "uat_o_auditor", "uat_o_dba"):
    session = login(username)
    for path in ("/api/v1/tdsql/connections/options", "/api/v1/tdsql/connections"):
        rows.append(record(session.get(BASE + path, timeout=20),
                           username + "_" + path.rsplit("/", 1)[-1]))
    if username != "uat_o_dba":
        rows.append(record(session.post(BASE + "/api/v1/tdsql/connections",
                                        json={"name": "forbidden", "host": "127.0.0.1",
                                              "port": 1, "username": "synthetic", "password": ""},
                                        timeout=20), username + "_forbidden_create"))

response = admin.post(BASE + "/api/v1/daily-inspect/run",
                      json={"connection_id": "uat_o_offline", "inspect_date": "2026-08-29"},
                      timeout=30)
rows.append(record(response, "daily_offline"))

(HERE / "http_results.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print("ROWS", len(rows), "5XX", sum(row["status"] >= 500 for row in rows),
      "XSS_REPORT", xss_row.get("report_id"))
