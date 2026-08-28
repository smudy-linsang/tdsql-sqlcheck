"""Offline audit of O's recorded UAT artifacts, not a new product test run."""
from __future__ import annotations

import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REPORT = HERE.parents[1] / "UAT-v1.6.2.2-第一轮全项目用户验收测试报告-智能体O.md"


def read_json(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    data = read_json("rule_probe_current.json")
    baseline = read_json("rule_probe_baseline.json")
    diff = read_json("rule_diff.json")
    delta = read_json("delta_classification.json")
    steps = read_json("browser_steps.json")
    http = read_json("http_results.json")
    assert len(steps) == 65
    assert len({step["id"] for step in steps}) == 65
    for step in steps:
        assert (HERE / (step["id"] + ".jpg")).read_bytes().startswith(b"\xff\xd8\xff")
        assert (HERE / (step["id"] + ".txt")).stat().st_size > 0
    assert len(data["rules"]) == data["rule_count"] == 119
    assert data["rules"] == baseline["rules"]
    assert data["case_count"] == baseline["case_count"] == len(data["rows"]) == 1000
    fired = {r for r in data["covered"] if r.startswith("R")}
    assert len(fired) == 114
    unfired = sorted({r["rule_id"] for r in data["rules"]} - fired)
    assert unfired == ["R025", "R035", "R038", "R049", "R059"]
    injected_only = sorted(r for r in fired if all(c.startswith("metadata:") for c in data["coverage"][r]))
    assert len(injected_only) == 7
    assert diff["rules_equal"] and diff["change_count"] == 575
    assert sum(x["count"] for x in delta["categories"]) == 575
    assert len(delta["categories"]) == 13
    assert all(x["unchanged"] for x in delta["unchanged_files"])
    failures = Counter(f["kind"] for f in data["failures"])
    assert failures == {"must_have": 72, "corpus_exact": 3}
    e999 = "E999_SYNTAX_ERROR"
    kfn = [r for r in data["rows"] if r["id"].startswith("kfn_")]
    assert len(kfn) == 75
    assert sum(e999 not in r["fired"] for r in kfn) == 72
    fully_passed_comments = sum(r["passed"] for r in kfn if r["id"].startswith("kfn_comment:"))
    assert fully_passed_comments == 18
    assert len(http) == 269
    statuses = Counter(r["status"] for r in http)
    assert statuses == {200: 266, 403: 3}
    mismatches = [r["id"] for r in http if r.get("engine_equals_http") is False]
    assert len(mismatches) == 2
    for row in read_json("original_current.json"):
        assert row["exact_equal_ignoring_line_endings"]
        assert row["source_replacement_char_count"] == row["fixture_replacement_char_count"] == 0
        assert row["parse_error"] is None
    root = ET.parse(HERE / "full_regression.xml").getroot()
    suites = list(root.iter("testsuite"))
    assert sum(int(s.get("tests", 0)) for s in suites) == 1384
    for key in ("failures", "errors", "skipped"):
        assert sum(int(s.get(key, 0)) for s in suites) == 0
    matrix = (HERE / "implementation_matrix.txt").read_text(encoding="utf-8")
    assert "RESULT PASS mode=implementation versions=29.0.0,30.14.0,30.17.0" in matrix
    assert matrix.count("680 passed") == 3
    assert "71 passed" in matrix and "1384 passed" in matrix
    assert (HERE / "ops_report.pdf").read_bytes().startswith(b"%PDF-")
    # Read all structured artifacts and source scripts; do not compile/import product code.
    for path in HERE.glob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))
    for path in HERE.glob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    text_files = [p for p in HERE.iterdir() if p.suffix in (".md", ".txt", ".json", ".log", ".html", ".sql", ".xml", ".py")] + [REPORT]
    # Restrict to credential-bearing forms; normal SQL PASSWORD keywords are test inputs.
    sensitive = re.compile(r"Bearer\s+[A-Za-z0-9_.-]{20,}|eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]+\.|[\"'](?:password_hash|salt)[\"']\s*:\s*[\"'][^\"']+", re.I)
    for path in text_files:
        content = path.read_text(encoding="utf-8")
        assert not sensitive.search(content), f"Potential credential: {path.name}"
        for match in re.finditer(r'"password"\s*:\s*"([^"\s]+)"', content):
            assert set(match.group(1)) <= {"*"}, f"Unmasked password field: {path.name}"
    broken_links = []
    for path in (REPORT, HERE / "README.md", HERE / "rule_coverage_119.md"):
        content = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", content):
            if target.startswith(("https://", "http://", "#")):
                continue
            local = target.split("#", 1)[0]
            resolved = (path.parent / local).resolve()
            generated = {HERE / "validation.json", HERE / "evidence_manifest.json"}
            if not resolved.exists() and resolved not in generated:
                broken_links.append({"file":path.name, "target":target})
    assert not broken_links, broken_links
    summary = {
        "kind": "offline_evidence_integrity_check_not_product_retest",
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "tested_commit": diff["current_commit"],
        "baseline_commit": diff["baseline_commit"],
        "browser_checkpoint_pairs": len(steps),
        "independent_inputs_each_version": data["case_count"],
        "rule_definitions_equal": True,
        "registered_rules": 119, "fired_rules": len(fired),
        "non_injected_metadata_input_fired_rules": len(fired) - len(injected_only),
        "synthetic_metadata_only_rules": injected_only,
        "unfired_rules": unfired,
        "independent_oracle_failures": dict(failures),
        "kfn_comment_cases_fully_passed_incorrectly": fully_passed_comments,
        "changed_rule_id_sets": 575, "unchanged_rule_id_sets": 425,
        "delta_categories": 13,
        "http_records": len(http), "http_status_counts": dict(statuses),
        "http_engine_mismatches": mismatches,
        "pytest_passed": 1384,
        "implementation_matrix_passed": True,
        "links_valid": True, "structured_artifacts_valid": True,
        "credential_pattern_scan": "no_matches",
        "uat_verdict": "NO_GO: mandatory fail-closed gap; see report for other defects and unverified scope",
    }
    (HERE / "validation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = []
    for path in sorted(HERE.iterdir()):
        if path.is_file() and path.name != "evidence_manifest.json":
            files.append({"path":path.name, "bytes":path.stat().st_size, "sha256":sha256(path)})
    manifest = {"tested_commit":diff["current_commit"], "files":files,
                "report":{"path":str(REPORT.relative_to(HERE.parents[1])).replace("\\", "/"), "sha256":sha256(REPORT)},
                "note":"Manifest excludes itself; validation timestamp is an integrity check, not a new browser test."}
    (HERE / "evidence_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"MANIFEST_FILES={len(files)} TOTAL_BYTES={sum(f['bytes'] for f in files)}")


if __name__ == "__main__":
    main()
