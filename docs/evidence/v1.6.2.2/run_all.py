# -*- coding: utf-8 -*-
"""One-command Rev.P evidence runner with design and implementation modes."""
from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from rebuild_from_design import (
    BASELINE_COMMIT, DESIGN, DISTRIBUTED, PARSER, PYPROJECT, REPO,
    REQUIREMENTS, TARGET_FILES, bundle_sha256, normalized_sha256,
    rebuild_texts, write_target,
)


RELEASE_SQLGLOT = "30.14.0"
MATRIX_SQLGLOT = ("29.0.0", RELEASE_SQLGLOT, "30.17.0")
HASH_LABEL = "design_bundle_normalized_sha256"
ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")


def _ascii(value: str) -> str:
    return value.encode("ascii", "backslashreplace").decode("ascii")


def _run(cmd, cwd: Path, label: str, failures: list[str], check=True):
    proc = subprocess.run(cmd, cwd=str(cwd), env=ENV, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    lines = [x for x in (proc.stdout + "\n" + proc.stderr).splitlines() if x.strip()]
    tail = " | ".join(lines[-3:]) if lines else "no-output"
    print("STEP %s rc=%d tail=%s" % (label, proc.returncode, _ascii(tail)))
    if check and proc.returncode != 0:
        failures.append("%s rc=%d" % (label, proc.returncode))
    return proc


def _copy_repo(dst: Path) -> None:
    shutil.copytree(
        REPO, dst, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules", "*.pyc"),
    )


def _venv_python(root: Path, version: str, failures: list[str]) -> Path:
    safe = version.replace(".", "_")
    venv = root / ("venv_" + safe)
    proc = _run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)],
                REPO, "venv-" + version, failures)
    if proc.returncode != 0:
        return Path(sys.executable)
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "--quiet",
          "sqlglot==" + version], REPO, "pip-sqlglot-" + version, failures)
    return py


def _expected_hash(doc: str):
    match = re.search(HASH_LABEL + r"\s*=\s*([0-9a-f]{64})", doc)
    return match.group(1) if match else None


def _generator_checks(work: Path, python: Path, doc: str, target_files: dict[str, str],
                      failures: list[str]) -> None:
    evidence = work / "docs" / "evidence" / "v1.6.2.2"
    manifest = _run([str(python), str(evidence / "manifest_doc.py")], work,
                    "manifest-doc", failures, check=False)
    if manifest.returncode != 0 or not manifest.stdout.strip() or manifest.stdout.rstrip("\n") not in doc:
        failures.append("manifest generated section mismatch")
    else:
        print("CHECK manifest-section=OK")

    baseline = work / ".evidence_baseline_parser.py"
    baseline.write_text(
        subprocess.run(["git", "show", "%s:%s" % (BASELINE_COMMIT, PARSER)],
                       cwd=str(REPO), capture_output=True, check=True).stdout.decode("utf-8"),
        encoding="utf-8", newline="\n",
    )
    codestat = _run(
        [str(python), str(evidence / "codestat.py"), str(baseline), str(work / PARSER)],
        work, "codestat", failures, check=False,
    )
    if codestat.returncode != 0 or not codestat.stdout.strip() or codestat.stdout.rstrip("\n") not in doc:
        failures.append("codestat generated section mismatch")
    else:
        print("CHECK codestat-section=OK")

    got = bundle_sha256(target_files)
    want = _expected_hash(doc)
    if want != got:
        failures.append("design bundle hash mismatch want=%s got=%s" % (want, got))
    else:
        print("CHECK design-bundle-hash=OK value=%s" % got)


def _run_matrix(work: Path, temp_root: Path, versions, failures: list[str], full_tests: bool) -> None:
    evidence_test = work / "docs" / "evidence" / "v1.6.2.2" / "test_parser_recovery_manifest.py"
    for version in versions:
        py = _venv_python(temp_root, version, failures)
        ver = _run([str(py), "-c", "import sqlglot; print(sqlglot.__version__)"],
                   work, "runtime-version-" + version, failures)
        actual = (ver.stdout or "").strip().splitlines()[-1:] or [""]
        if actual[0] != version:
            failures.append("runtime version mismatch want=%s got=%s" % (version, actual[0]))
            continue
        _run([str(py), "-m", "pytest", "-q", str(evidence_test)],
             work, "manifest-" + version, failures)
        if version == RELEASE_SQLGLOT:
            _run([str(py), "-m", "pytest", "-q",
                  "tests/test_r077_r054_tdsql_syntax.py",
                  "tests/test_parser_tdsql_dialect_fallback.py",
                  "tests/test_r061_index_name_quoting.py"],
                 work, "frozen-71-release", failures)
            if full_tests:
                _run([str(py), "-m", "pytest", "-q", "tests/"],
                     work, "full-tests-release", failures)


def _current_texts() -> dict[str, str]:
    return {rel: (REPO / rel).read_text(encoding="utf-8") for rel in TARGET_FILES}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("design", "implementation"), default="design")
    ap.add_argument("--matrix", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--skip-full-tests", action="store_true",
                    help="diagnostic only; release evidence must not use this flag")
    args = ap.parse_args(argv)

    failures: list[str] = []
    temp = Path(tempfile.mkdtemp(prefix="v1622-revp-evidence-"))
    work = temp / "tree"
    try:
        doc = io.open(DESIGN, encoding="utf-8").read()
        design_files = rebuild_texts(REPO, DESIGN, BASELINE_COMMIT)
        if args.mode == "design":
            _copy_repo(work)
            write_target(work, design_files)
            print("MODE design baseline=%s" % BASELINE_COMMIT)
        else:
            current = _current_texts()
            if bundle_sha256(current) != bundle_sha256(design_files):
                print("STATUS NOT_IMPLEMENTED current_bundle=%s design_bundle=%s" % (
                    bundle_sha256(current), bundle_sha256(design_files)))
                return 3
            _copy_repo(work)
            print("MODE implementation bundle=%s" % bundle_sha256(current))

        versions = MATRIX_SQLGLOT if args.matrix else (RELEASE_SQLGLOT,)
        _run_matrix(work, temp, versions, failures, not args.skip_full_tests)

        release_py = temp / "venv_30_14_0" / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python")
        if release_py.is_file():
            _generator_checks(work, release_py, doc, design_files, failures)
        else:
            failures.append("release venv unavailable; generator checks not run")

        for rel in (REQUIREMENTS, PYPROJECT):
            text = design_files[rel]
            if "sqlglot==30.14.0" not in text:
                failures.append("release pin missing in %s" % rel)
        if failures:
            for failure in failures:
                print("FAIL %s" % _ascii(failure))
            print("RESULT FAIL count=%d" % len(failures))
            return 1
        print("RESULT PASS mode=%s versions=%s" % (args.mode, ",".join(versions)))
        return 0
    finally:
        if args.keep:
            print("TEMP_KEEP %s" % _ascii(str(temp)))
        else:
            shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
