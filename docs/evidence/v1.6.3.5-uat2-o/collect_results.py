"""Export bounded, credential-free test/log summaries, not raw captured credentials."""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
RUNTIME = ROOT / "data/reports/uat_o_1635_r2"
result = {"regression_runs": [], "web_access": [], "runner_events": []}
for name in ("full-regression.xml", "full-regression-prepared.xml", "full-regression-final.xml"):
    path = RUNTIME / name
    if not path.exists():
        continue
    root = ET.parse(path).getroot()
    suite = root.find("testsuite")
    result["regression_runs"].append({
        "file": name, "attributes": suite.attrib,
        "failed_tests": [f"{t.get('classname')}::{t.get('name')}" for t in suite.findall("testcase")
                         if t.find("failure") is not None or t.find("error") is not None],
        "skipped_tests": [f"{t.get('classname')}::{t.get('name')}" for t in suite.findall("testcase")
                          if t.find("skipped") is not None],
    })
for line in (RUNTIME / "web-auth.stderr.log").read_text(encoding="utf-8", errors="replace").splitlines():
    if "[tdsql.access]" not in line:
        continue
    if any(s in line for s in (
        "POST /api/v1/audit/metadata-jobs", "POST /api/v1/audit/extract-and-audit",
        "POST /api/v1/audit/sql", "GET /api/v1/slow-queries/scan-tasks",
        "GET /api/v1/bigtable/inventory", "GET /api/v1/gateway-log/reports")):
        result["web_access"].append(line)
for name in ("runner.stderr.log", "runner-resumed.stderr.log"):
    for line in (RUNTIME / name).read_text(encoding="utf-8", errors="replace").splitlines():
        if "job_id=" in line:
            # Only ASCII structured fields; discard raw target exceptions and any token.
            pairs = re.findall(r"\b(job_id|rc|elapsed_s|cleanup_ok|timed_out|cancelled|rss_exceeded)=([^\s]+)", line)
            if pairs:
                result["runner_events"].append({"time_local": line[:23], **dict(pairs)})
(OUT / "verification-summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"runs": [r["attributes"] for r in result["regression_runs"]],
                  "web_access_count": len(result["web_access"]),
                  "runner_event_count": len(result["runner_events"])}, ensure_ascii=False, indent=2))
