# -*- coding: utf-8 -*-
"""One-command Rev.Q evidence runner with design and implementation modes."""
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


def _read_impl_baseline() -> dict | None:
    """读取经评审的实现基线清单（UAT 后实现演进审计）。
    清单记录当前实现包哈希、演进来源设计包哈希与评审依据，
    使正式门禁能显式验证“新实现与旧设计合同”的演进关系。"""
    path = Path(__file__).resolve().parent / "implementation_baseline.json"
    if not path.is_file():
        return None
    try:
        import json
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _generator_checks(work: Path, python: Path, doc: str, target_files: dict[str, str],
                      failures: list[str], impl_baseline: dict | None = None) -> None:
    # v1.6.2.2-UAT-O-21：实现演进模式下，生成物（manifest 章节/codestat 章节）与经评审的
    # 实现基线清单中的审计哈希同源比对；不再拿“当前实现生成的章节”去硬套旧设计文档，
    # 也不得简单跳过——基线缺失或哈希不符均判失败。设计一致模式仍逐字比对设计文档。
    audit = (impl_baseline or {}).get("implementation_audit") or {}
    evidence = work / "docs" / "evidence" / "v1.6.2.2"
    manifest = _run([str(python), str(evidence / "manifest_doc.py")], work,
                    "manifest-doc", failures, check=False)
    want_manifest = audit.get("manifest_section_sha256")
    if want_manifest:
        got_manifest = normalized_sha256(manifest.stdout.rstrip("\n")) \
            if manifest.returncode == 0 and manifest.stdout.strip() else ""
        if manifest.returncode != 0 or got_manifest != want_manifest:
            failures.append("manifest generated section vs implementation baseline mismatch "
                            "want=%s got=%s" % (want_manifest, got_manifest))
        else:
            print("CHECK manifest-section=OK (implementation baseline)")
    elif manifest.returncode != 0 or not manifest.stdout.strip() or manifest.stdout.rstrip("\n") not in doc:
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
    want_codestat = audit.get("codestat_section_sha256")
    if want_codestat:
        got_codestat = normalized_sha256(codestat.stdout.rstrip("\n")) \
            if codestat.returncode == 0 and codestat.stdout.strip() else ""
        if codestat.returncode != 0 or got_codestat != want_codestat:
            failures.append("codestat generated section vs implementation baseline mismatch "
                            "want=%s got=%s" % (want_codestat, got_codestat))
        else:
            print("CHECK codestat-section=OK (implementation baseline)")
    elif codestat.returncode != 0 or not codestat.stdout.strip() or codestat.stdout.rstrip("\n") not in doc:
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
    temp = Path(tempfile.mkdtemp(prefix="v1622-revq-evidence-"))
    work = temp / "tree"
    impl_baseline: dict | None = None
    try:
        doc = io.open(DESIGN, encoding="utf-8").read()
        design_files = rebuild_texts(REPO, DESIGN, BASELINE_COMMIT)
        if args.mode == "design":
            _copy_repo(work)
            write_target(work, design_files)
            print("MODE design baseline=%s" % BASELINE_COMMIT)
        else:
            # v1.6.2.2-UAT-O-12：把“设计包真实性”与“实现版本验证”拆为两个明确检查。
            current = _current_texts()
            cur_hash = bundle_sha256(current)
            design_hash = bundle_sha256(design_files)
            # 检查 1（设计合同真实性，独立保留）：设计包哈希必须与设计文档声明一致。
            want = _expected_hash(doc)
            if want != design_hash:
                failures.append("design bundle hash vs doc mismatch want=%s got=%s"
                                % (want, design_hash))
            # 检查 2（实现版本验证）：实现包与设计包一致则完全按设计验证；
            # 不一致则属“实现演进”（如 UAT-R3 后的修复），需经评审的实现基线清单确认。
            if cur_hash == design_hash:
                print("MODE implementation bundle=%s (matches design)" % cur_hash)
                _copy_repo(work)
            else:
                impl_baseline = _read_impl_baseline()
                if impl_baseline and impl_baseline.get("implementation_bundle") == cur_hash:
                    print("MODE implementation bundle=%s (evolved from design %s; baseline-recorded)"
                          % (cur_hash, impl_baseline.get("evolved_from_design_bundle", "?")))
                    _copy_repo(work)
                else:
                    print("STATUS NOT_IMPLEMENTED current_bundle=%s design_bundle=%s" % (
                        cur_hash, design_hash))
                    return 3

        versions = MATRIX_SQLGLOT if args.matrix else (RELEASE_SQLGLOT,)
        _run_matrix(work, temp, versions, failures, not args.skip_full_tests)

        release_py = temp / "venv_30_14_0" / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python")
        if release_py.is_file():
            _generator_checks(work, release_py, doc, design_files, failures,
                              impl_baseline=impl_baseline)
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
