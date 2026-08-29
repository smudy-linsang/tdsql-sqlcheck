"""Authenticated HTTP and production-shaped two-worker tests for sixth-round UAT."""
import hashlib
import json
import os
from pathlib import Path
import requests
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
BASE = "http://127.0.0.1:8007"
PASSWORD = os.environ["UAT_O_PASSWORD"]


def login(username):
    session = requests.Session()
    response = session.post(
        BASE + "/api/v1/auth/login",
        json={"username": username, "password": PASSWORD}, timeout=15,
    )
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


admin = login("uat_o_admin_r6")
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
    response = admin.post(
        BASE + "/api/v1/audit/sql",
        json={"sql": sql, "instance_type": "distributed"}, timeout=30,
    )
    body = response.json()
    rows.append({"label": label, "status": response.status_code,
                 "passed": body.get("passed"),
                 "fired": sorted({v["rule_id"] for v in body.get("violations", [])})})

for label, content in (
    ("gateway_empty", b""),
    ("gateway_invalid", b"not a gateway log\nnot another log\n"),
    ("gateway_over_threshold", (
        b"invalid one\n"
        b"[2026-08-29 00:00:00 100] INFO topic=test&timecost=2.5&sql=select 1&db=synthetic&user=uat\n"
        b"invalid two\n")),
):
    response = admin.post(
        BASE + "/api/v1/gateway-log/upload",
        data={"connection_id": "uat_o_offline_r6", "log_type": "interf"},
        files={"file": (label + ".log", content, "text/plain")}, timeout=60,
    )
    rows.append(record(response, label, input_lines=len(content.splitlines())))

for label, file_name in (("gateway_partial", "gateway_partial.log"),
                         ("gateway_xss", "gateway_xss.log")):
    content = (HERE / file_name).read_bytes()
    response = admin.post(
        BASE + "/api/v1/gateway-log/upload",
        data={"connection_id": "uat_o_offline_r6", "log_type": "interf"},
        files={"file": (file_name, content, "text/plain")}, timeout=60,
    )
    row = record(response, label, input_lines=len(content.splitlines()))
    if response.ok:
        row["report_id"] = response.json()["report_id"]
    rows.append(row)

xss_report_id = next(row["report_id"] for row in rows if row["label"] == "gateway_xss")
detail = admin.get(BASE + f"/api/v1/gateway-log/reports/{xss_report_id}", timeout=20).json()
header_document = admin.get(
    BASE + f"/api/v1/gateway-log/reports/{xss_report_id}/html", timeout=20,
)
rows.append(record(
    header_document, "gateway_html_authorization_header",
    csp=header_document.headers.get("content-security-policy"),
    xfo=header_document.headers.get("x-frame-options"),
    raw_script_breakout="</script><script>window.__uat_o_r6_pwned" in header_document.text,
    unicode_escaped="\\u003c/script\\u003e" in detail.get("report_html", ""),
))

# Production-shaped: issue, consume and replay through separate new TCP connections under
# the real two-worker server. This models the shipped Nginx config, which has no upstream
# stickiness and may dispatch each upstream request independently.
ticket_attempts = []
auth_header = admin.headers["Authorization"]
for i in range(100):
    issued = requests.post(
        BASE + f"/api/v1/gateway-log/reports/{xss_report_id}/ticket",
        headers={"Authorization": auth_header, "Connection": "close"}, timeout=20,
    )
    ticket = issued.json().get("ticket", "") if issued.ok else ""
    first = requests.get(
        BASE + f"/api/v1/gateway-log/reports/{xss_report_id}/html",
        params={"report_ticket": ticket}, headers={"Connection": "close"}, timeout=20,
    )
    replay = requests.get(
        BASE + f"/api/v1/gateway-log/reports/{xss_report_id}/html",
        params={"report_ticket": ticket}, headers={"Connection": "close"}, timeout=20,
    )
    ticket_attempts.append({"n": i + 1, "issue": issued.status_code,
                            "first": first.status_code, "replay": replay.status_code})
ticket_summary = {
    "label": "gateway_ticket_two_worker", "status": 200,
    "attempts": ticket_attempts,
    "issue_200": sum(x["issue"] == 200 for x in ticket_attempts),
    "first_200": sum(x["first"] == 200 for x in ticket_attempts),
    "replay_401": sum(x["replay"] == 401 for x in ticket_attempts),
}
rows.append(ticket_summary)
rows.append(record(
    admin.get(BASE + f"/api/v1/gateway-log/reports/{xss_report_id}/ticket", timeout=20),
    "gateway_ticket_get_rejected",
))

wrong = admin.post(
    BASE + f"/api/v1/gateway-log/reports/{xss_report_id}/ticket", timeout=20,
).json()["ticket"]
wrong_report = requests.get(
    BASE + f"/api/v1/gateway-log/reports/{xss_report_id + 1}/html",
    params={"report_ticket": wrong}, headers={"Connection": "close"}, timeout=20,
)
correct_after_wrong = requests.get(
    BASE + f"/api/v1/gateway-log/reports/{xss_report_id}/html",
    params={"report_ticket": wrong}, headers={"Connection": "close"}, timeout=20,
)
rows.append({"label": "gateway_ticket_wrong_binding", "status": wrong_report.status_code,
             "correct_after_wrong": correct_after_wrong.status_code})

response = admin.post(
    BASE + "/api/v1/index-audit/run",
    json={"connection_id": "uat_o_index_r6", "database": "tdsql_uat_o_r6_index_target"},
    timeout=90,
)
rows.append(record(response, "index_audit"))
response = admin.get(
    BASE + "/api/v1/ppt-report/dashboard",
    params={"connection_id": "uat_o_index_r6"}, timeout=30,
)
rows.append(record(response, "index_dashboard"))
response = admin.get(
    BASE + "/api/v1/ppt-report/generate",
    params={"connection_id": "uat_o_index_r6"}, timeout=60,
)
(HERE / "index_actual.pdf").write_bytes(response.content)
rows.append(record(response, "index_pdf"))

for db_name in (None, "tdsql_uat_o_r6_index_target", "tdsql_uat_o_missing_r6"):
    payload = {"sql": "SELECT id FROM t_uat_order WHERE customer_id=1",
               "connection_id": "uat_o_local_r6"}
    if db_name:
        payload["db_name"] = db_name
    response = admin.post(
        BASE + "/api/v1/slow-queries/analyze-explain-by-sql", json=payload, timeout=30,
    )
    rows.append(record(response, "explain_" + (db_name or "default")))

for username in ("uat_o_developer_r6", "uat_o_auditor_r6", "uat_o_dba_r6"):
    session = login(username)
    for path in ("/api/v1/tdsql/connections/options", "/api/v1/tdsql/connections"):
        rows.append(record(session.get(BASE + path, timeout=20),
                           username + "_" + path.rsplit("/", 1)[-1]))
    if "dba" not in username:
        rows.append(record(session.post(
            BASE + "/api/v1/tdsql/connections",
            json={"name": "forbidden", "host": "127.0.0.1", "port": 1,
                  "username": "synthetic", "password": ""}, timeout=20,
        ), username + "_forbidden_create"))

response = admin.post(
    BASE + "/api/v1/daily-inspect/run",
    json={"connection_id": "uat_o_offline_r6", "inspect_date": "2026-08-29"}, timeout=30,
)
rows.append(record(response, "daily_offline"))

# Verify plaintext ticket is not present in shared storage after the HTTP run.
os.environ.setdefault("SQLCHECK_DB_NAME", "tdsql_uat_o_r6_1622_20260829")
from backend.services.database import _get_connection
conn = _get_connection()
try:
    stored = conn.execute(
        "SELECT ticket_hash FROM gateway_report_tickets ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
finally:
    conn.close()
rows.append({"label": "gateway_ticket_storage", "status": 200,
             "hash_lengths": sorted({len(dict(row)["ticket_hash"]) for row in stored}),
             "sha256_shape": all(len(dict(row)["ticket_hash"]) == 64 for row in stored)})

(HERE / "http_results.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n",
)
print("ROWS", len(rows), "5XX", sum(row["status"] >= 500 for row in rows),
      "ISSUE", ticket_summary["issue_200"], "FIRST", ticket_summary["first_200"],
      "REPLAY", ticket_summary["replay_401"])
