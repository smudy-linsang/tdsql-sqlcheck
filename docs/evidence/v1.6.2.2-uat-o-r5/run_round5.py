"""Independent fifth-round UAT runners; never overwrite earlier evidence."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EVIDENCE = HERE.parent
R1 = EVIDENCE / "v1.6.2.2-uat-o-r1"
R2 = EVIDENCE / "v1.6.2.2-uat-o-r2"
R3 = EVIDENCE / "v1.6.2.2-uat-o-r3"
R4 = EVIDENCE / "v1.6.2.2-uat-o-r4"
CACHE = Path("C:/Users/linsa/AppData/Local/Temp/v1622-revq-evidence-h3fdnkee")


def run(args, log, env):
    with (HERE / log).open("w", encoding="utf-8", newline="\n") as out:
        result = subprocess.run(args, cwd=ROOT, env=env, stdout=out,
                                stderr=subprocess.STDOUT, text=True)
    print(log, "EXIT", result.returncode, flush=True)
    return result.returncode


def base_env(db_name):
    return dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                AUTH_ENABLED="false", SCHEDULER_ENABLED="false",
                SQLCHECK_DB_NAME=db_name)


def full():
    env = base_env("tdsql_uat_o_reg_r5_full_20260829")
    if run([sys.executable, str(R1 / "prepare_regression.py")], "full_prepare.txt", env):
        return 2
    return run([sys.executable, "-m", "pytest", "tests", "-q",
                "--junitxml=" + str(HERE / "full_regression.xml")],
               "full_regression.txt", env)


def probes():
    env = base_env("tdsql_uat_o_reg_r5_probe_20260829")
    jobs = [
        (R1 / "rule_probe.py", "rule_probe_current"),
        (R2 / "edge_probe.py", "edge_current"),
        (R3 / "load_comment_matrix.py", "load_current"),
        (R3 / "head_boundary_probe.py", "head_current"),
    ]
    for script, name in jobs:
        args = [sys.executable, str(script), "--repo", str(ROOT),
                "--out", str(HERE / f"{name}.json")]
        if run(args, f"{name}.txt", env):
            return 3
    comparisons = {}
    names = ("rule_probe_current.json", "edge_current.json",
             "load_current.json", "head_current.json")
    for name in names:
        old = json.loads((R4 / name).read_text(encoding="utf-8"))
        new = json.loads((HERE / name).read_text(encoding="utf-8"))
        comparisons[name] = {
            "equal": old == new,
            "old_summary": old.get("summary") if isinstance(old, dict) else None,
            "new_summary": new.get("summary") if isinstance(new, dict) else None,
        }
    (HERE / "round5_diff.json").write_text(
        json.dumps({
            "baseline_commit": "e481432d3da63a43d3166dd51d7bdd78a749aab8",
            "tested_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "comparisons": comparisons,
        }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return 0


def matrix():
    env = base_env("tdsql_uat_o_reg_r5_matrix_20260829")
    if run([sys.executable, str(R1 / "prepare_regression.py")], "matrix_prepare.txt", env):
        return 4
    results = []
    for version in ("29.0.0", "30.14.0", "30.17.0"):
        py = CACHE / ("venv_" + version.replace(".", "_")) / "Scripts/python.exe"
        actual = subprocess.check_output(
            [str(py), "-c", "import sqlglot; print(sqlglot.__version__)"],
            text=True).strip()
        for label, args in (
            ("manifest", ["-m", "pytest", "-q",
                          "docs/evidence/v1.6.2.2/test_parser_recovery_manifest.py",
                          "tests/test_kfn_fail_closed.py",
                          "tests/test_o14_cr_fail_closed.py"]),
            ("edge", [str(R2 / "edge_probe.py"), "--repo", str(ROOT),
                      "--out", str(HERE / f"edge_{version}.json")]),
            ("load", [str(R3 / "load_comment_matrix.py"), "--repo", str(ROOT),
                      "--out", str(HERE / f"load_{version}.json")]),
            ("head", [str(R3 / "head_boundary_probe.py"), "--repo", str(ROOT),
                      "--out", str(HERE / f"head_{version}.json")]),
        ):
            exit_code = run([str(py), *args], f"{label}_{version}.txt", env)
            results.append({"version": version, "actual": actual,
                            "job": label, "exit": exit_code})
    (HERE / "independent_matrix.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return 0 if all(row["exit"] == 0 for row in results) else 5


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("full", "probes", "matrix"))
    mode = parser.parse_args().mode
    raise SystemExit({"full": full, "probes": probes, "matrix": matrix}[mode]())
