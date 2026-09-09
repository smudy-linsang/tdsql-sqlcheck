"""UAT2 evidence probes: read local fixture + in-memory fault injection only.

No repository production files are rewritten, and no fixture task is created here.
Use UAT_FIXTURE_PASSWORD for authenticated read-only HTTP checks (never saved).
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import local_fixture as fixture
from backend.services.database import _get_connection
from backend.services.metadata_audit_repository import repository as repo, MetadataJobError
from backend.api.metadata_audit import _check_runner_ready, _job_summary
from backend.services import metadata_audit_pipeline as pipe
from backend.workers import metadata_audit_worker as worker

OUT = Path(__file__).resolve().parent
result = {}
cx = _get_connection()
result["db_version"] = cx.execute("SELECT VERSION() AS version").fetchone()
jobs = cx.execute("SELECT * FROM metadata_audit_jobs ORDER BY created_at").fetchall()
cx.close()
result["jobs"] = [_job_summary(j) for j in jobs]
result["slot"] = repo.slot_state()
result["timestamp_utc"] = datetime.now(timezone.utc).isoformat()

old = datetime.now(timezone.utc) - timedelta(seconds=60)
result["heartbeat_cases"] = []
for label, hb in (("expired-real-iso", old.replace(tzinfo=None).isoformat()),
                  ("expired-datetime", old), ("missing", None),
                  ("malformed", "invalid-time"),
                  ("fresh-real-iso", datetime.now(timezone.utc).replace(tzinfo=None).isoformat())):
    with patch.object(repo, "slot_state", return_value={"accepting": 1, "runner_heartbeat_at": hb}):
        try:
            _check_runner_ready()
            actual = "ACCEPTS"
        except MetadataJobError as e:
            actual = f"{e.http_status}/{e.code}"
    result["heartbeat_cases"].append({"case": label, "actual": actual})

# Execute the real Q assertion with mutated script content in memory.
spec = importlib.util.spec_from_file_location("uat_deploy_contract", fixture.ROOT / "tests/test_v1635_deploy_contract.py")
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)
original_read = Path.read_text
result["deployment_mutations"] = []
for script in ("install.sh", "upgrade_incremental.sh", "apply_patch.sh"):
    source = original_read(tests.DEPLOY / script, encoding="utf-8")
    for mode in ("delete-runner-restart", "comment-runner-restart"):
        mutated = "\n".join(
            (("# " + line) if mode.startswith("comment") else "# UAT2 removed start action")
            if "systemctl restart tdsql-metadata-runner" in line else line
            for line in source.splitlines())
        assert mutated != source
        def fake_read(path, *a, **kw):
            return mutated if path == tests.DEPLOY / script else original_read(path, *a, **kw)
        with patch.object(Path, "read_text", fake_read):
            try:
                tests.test_runner_starts_before_web(script)
                actual = "GREEN_MUTATION_SURVIVED"
            except AssertionError:
                actual = "RED_MUTATION_CAUGHT"
        result["deployment_mutations"].append({"script": script, "mutation": mode, "actual": actual})

# Exercise both actual worker catch branches. Repository writes and target I/O are mocked.
from backend.services.connection_registry import registry
import pymysql
job = dict(jobs[0], state="RUNNING", attempt_token="uat2-mock-token")
fake_conn = MagicMock()
fake_conn.cursor.return_value.execute.side_effect = pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")
try:
    pipe._show_create(fake_conn, "synthetic_db", {"name": "t_demo", "type": "BASE TABLE"})
except pipe.MetadataExtractError as e:
    wrapped_error = e
result["humanize_worker_branches"] = []
for label, err in (("connect-2013", pymysql.err.OperationalError(2013, "Lost connection")),
                   ("show-create-2013-wrapped", wrapped_error)):
    with patch.object(repo, "get_job", return_value=job), patch.object(repo, "cas_state"), \
         patch.object(repo, "fail") as fail, patch.object(registry, "get", return_value=MagicMock()), \
         patch.object(pipe, "extract_metadata", side_effect=err):
        rc = worker.run(job["id"], job["attempt_token"])
        result["humanize_worker_branches"].append({"case": label, "exit": rc, **fail.call_args.kwargs})

if os.environ.get("UAT_FIXTURE_PASSWORD"):
    import requests
    session = requests.Session()
    login = session.post("http://127.0.0.1:8005/api/v1/auth/login", json={
        "username": "uat_o_1635_r2", "password": os.environ["UAT_FIXTURE_PASSWORD"]}, timeout=10)
    login.raise_for_status()
    session.headers["Authorization"] = "Bearer " + login.json()["token"]
    jid = jobs[0]["id"]
    base = f"http://127.0.0.1:8005/api/v1/audit/metadata-jobs/{jid}"
    result["http_read_checks"] = {}
    import hashlib
    for endpoint in ("", "/results?offset=50&limit=50", "/sql", "/html"):
        response = session.get(base + endpoint, timeout=10)
        entry = {"status": response.status_code, "bytes": len(response.content),
                 "sha256": hashlib.sha256(response.content).hexdigest()}
        if endpoint.startswith("/results"):
            data = response.json()
            entry.update(total=data["total"], items=len(data["items"]), next_offset=data["next_offset"])
        elif endpoint == "":
            entry["summary"] = response.json()
        else:
            entry["connection_name_present"] = "O-UAT2-63表-本机模拟目标" in response.content.decode("utf-8")
        result["http_read_checks"][endpoint] = entry
    retired = session.post("http://127.0.0.1:8005/api/v1/audit/extract-and-audit", data="not-json", timeout=10)
    result["retired_http"] = {"status": retired.status_code, "body": retired.json()}

dest = OUT / (sys.argv[1] if len(sys.argv) > 1 else "contract-probes.json")
dest.write_text(json.dumps(result, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
