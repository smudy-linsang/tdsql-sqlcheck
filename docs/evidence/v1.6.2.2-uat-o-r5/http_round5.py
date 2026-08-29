"""Authenticated loopback and production-shaped two-worker tests for fifth-round UAT."""
import json
import os
from pathlib import Path
import requests


HERE = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8006"
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
                "bytes": len(response.content), "text_prefix": response.text[:160]}
    return {"label": label, "status": response.status_code, "body": body, **extra}


admin = login("uat_o_admin_r5")
rows = []
for label, sql in {
    "cr_malformed_view": "-- ordinary\rCREATE VIEW v AS SELECT 1 +",
    "r042_hash_comment": "# operator's note\nLOAD XML INFILE '/tmp/synthetic.xml' INTO TABLE t",
    "tdsql_hash_table": (
        "CREATE TABLE cus_bas_corp_contact (ID varchar(64) NOT NULL, "
        "CUST_NO varchar(20) NOT NULL, PRIMARY KEY (ID,CUST_NO)) "
        "ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(CUST_NO)"),
    "tdsql_broadcast": (
        "CREATE TABLE cus_name_list_type (ID varchar(64) NOT NULL, PRIMARY KEY(ID)) "
        "ENGINE=InnoDB shardkey=noshardkey_allset"),
}.items():
    response = admin.post(BASE + "/api/v1/audit/sql",
                          json={"sql": sql, "instance_type": "distributed"}, timeout=30)
    body = response.json()
    rows.append({"label": label, "status": response.status_code,
                 "passed": body.get("passed"),
                 "fired": sorted({v["rule_id"] for v in body.get("violations", [])})})

invalid = b"not a gateway log\nnot another log\n"
over_threshold = (
    b"invalid one\n"
    b"[2026-08-29 00:00:00 100] INFO topic=test&timecost=2.5&sql=select 1&db=synthetic&user=uat\n"
    b"invalid two\n")
for label, content in (("gateway_empty", b""), ("gateway_invalid", invalid),
                       ("gateway_over_threshold", over_threshold)):
    response = admin.post(BASE + "/api/v1/gateway-log/upload",
                          data={"connection_id": "uat_o_offline_r5", "log_type": "interf"},
                          files={"file": (label + ".log", content, "text/plain")}, timeout=60)
    rows.append(record(response, label, input_lines=len(content.splitlines())))

for label, file_name in (("gateway_partial", "gateway_partial.log"),
                         ("gateway_xss", "gateway_xss.log")):
    content = (HERE / file_name).read_bytes()
    response = admin.post(BASE + "/api/v1/gateway-log/upload",
                          data={"connection_id": "uat_o_offline_r5", "log_type": "interf"},
                          files={"file": (file_name, content, "text/plain")}, timeout=60)
    row = record(response, label, input_lines=len(content.splitlines()))
    if response.ok:
        row["report_id"] = response.json()["report_id"]
    rows.append(row)

xss_report_id = next(row["report_id"] for row in rows if row["label"] == "gateway_xss")
detail = admin.get(BASE + f"/api/v1/gateway-log/reports/{xss_report_id}", timeout=20).json()
header_document = admin.get(BASE + f"/api/v1/gateway-log/reports/{xss_report_id}/html", timeout=20)
rows.append(record(
    header_document, "gateway_html_authorization_header",
    csp=header_document.headers.get("content-security-policy"),
    xfo=header_document.headers.get("x-frame-options"),
    raw_script_breakout="</script><script>window.__uat_o_r5_pwned" in header_document.text,
    unicode_escaped="\\u003c/script\\u003e" in detail.get("report_html", "")))

# Production uses two workers. Ticket creation is pinned to the admin Session's keep-alive
# connection; each iframe simulation uses a fresh TCP connection. A process-local ticket
# store therefore produces intermittent 401 when the second request lands on the other worker.
ticket_attempts = []
for i in range(30):
    issued = admin.get(BASE + f"/api/v1/gateway-log/reports/{xss_report_id}/ticket",
                       headers={"Connection": "keep-alive"}, timeout=20)
    ticket = issued.json().get("ticket", "") if issued.ok else ""
    fresh = requests.get(
        BASE + f"/api/v1/gateway-log/reports/{xss_report_id}/html",
        params={"report_ticket": ticket}, headers={"Connection": "close"}, timeout=20)
    ticket_attempts.append({"n": i + 1, "issue": issued.status_code,
                            "consume": fresh.status_code})
ticket_summary = {"label": "gateway_ticket_two_worker", "status": 200,
                  "attempts": ticket_attempts,
                  "consume_200": sum(x["consume"] == 200 for x in ticket_attempts),
                  "consume_401": sum(x["consume"] == 401 for x in ticket_attempts)}
rows.append(ticket_summary)

response = admin.post(BASE + "/api/v1/index-audit/run",
                      json={"connection_id": "uat_o_index_r5",
                            "database": "tdsql_uat_o_r5_index_target"}, timeout=90)
rows.append(record(response, "index_audit"))
response = admin.get(BASE + "/api/v1/ppt-report/dashboard",
                     params={"connection_id": "uat_o_index_r5"}, timeout=30)
rows.append(record(response, "index_dashboard"))
response = admin.get(BASE + "/api/v1/ppt-report/generate",
                     params={"connection_id": "uat_o_index_r5"}, timeout=60)
(HERE / "index_actual.pdf").write_bytes(response.content)
rows.append(record(response, "index_pdf"))

for db_name in (None, "tdsql_uat_o_r5_index_target", "tdsql_uat_o_missing_r5"):
    payload = {"sql": "SELECT id FROM t_uat_order WHERE customer_id=1",
               "connection_id": "uat_o_local_r5"}
    if db_name:
        payload["db_name"] = db_name
    response = admin.post(BASE + "/api/v1/slow-queries/analyze-explain-by-sql",
                          json=payload, timeout=30)
    rows.append(record(response, "explain_" + (db_name or "default")))

for username in ("uat_o_developer_r5", "uat_o_auditor_r5", "uat_o_dba_r5"):
    session = login(username)
    for path in ("/api/v1/tdsql/connections/options", "/api/v1/tdsql/connections"):
        rows.append(record(session.get(BASE + path, timeout=20),
                           username + "_" + path.rsplit("/", 1)[-1]))
    if "dba" not in username:
        rows.append(record(session.post(
            BASE + "/api/v1/tdsql/connections",
            json={"name": "forbidden", "host": "127.0.0.1", "port": 1,
                  "username": "synthetic", "password": ""}, timeout=20),
            username + "_forbidden_create"))

response = admin.post(BASE + "/api/v1/daily-inspect/run",
                      json={"connection_id": "uat_o_offline_r5",
                            "inspect_date": "2026-08-29"}, timeout=30)
rows.append(record(response, "daily_offline"))

(HERE / "http_results.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print("ROWS", len(rows), "5XX", sum(row["status"] >= 500 for row in rows),
      "TICKET_200", ticket_summary["consume_200"],
      "TICKET_401", ticket_summary["consume_401"])
