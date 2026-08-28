"""Re-run the same independent oracle on immutable baseline and current HEAD."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
BASELINE = "0079300"


def main():
    baseline = Path(tempfile.mkdtemp(prefix="uat-o-v1622-baseline-"))
    archive = subprocess.check_output(["git", "archive", "--format=zip", BASELINE], cwd=ROOT)
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        z.extractall(baseline)
    for label, tree in (("baseline", baseline), ("current", ROOT)):
        result = subprocess.run([sys.executable, str(OUT / "rule_probe.py"), "--repo", str(tree), "--out", str(OUT / f"rule_probe_{label}.json")], capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))
        (OUT / f"rule_probe_{label}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise SystemExit(result.returncode)
    before = json.loads((OUT / "rule_probe_baseline.json").read_text(encoding="utf-8"))
    after = json.loads((OUT / "rule_probe_current.json").read_text(encoding="utf-8"))
    mapping = {r["id"]:r for r in before["rows"]}
    changes = []
    for r in after["rows"]:
        old = mapping[r["id"]]
        if r.get("fired") != old.get("fired") or r.get("exception") != old.get("exception"):
            changes.append({"id":r["id"], "sql":r["sql"], "before":old.get("fired"), "after":r.get("fired"), "baseline_parse_error":old.get("parse_error"), "current_parse_error":r.get("parse_error")})
    summary = {"baseline_commit":BASELINE,"current_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(), "baseline_temp":str(baseline),"case_count":after["case_count"],"rules_equal":before["rules"]==after["rules"],"covered_rule_count":len([r for r in after["covered"] if r.startswith("R")]),"unfired_rules":sorted(set(x["rule_id"] for x in after["rules"])-set(after["covered"])),"baseline_failures":before["failures"],"current_failures":after["failures"],"change_count":len(changes),"changes":changes}
    (OUT / "rule_diff.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k not in ("changes","baseline_failures","current_failures")}, ensure_ascii=True))
    print("CURRENT_ORACLE_FAILURES", len(after["failures"]))


if __name__ == "__main__":
    main()
