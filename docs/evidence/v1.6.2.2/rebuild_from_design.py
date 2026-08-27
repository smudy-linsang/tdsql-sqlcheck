# -*- coding: utf-8 -*-
"""Deterministically rebuild the Rev.Q target from stable document markers.

The tool reads immutable product blobs from ``baseline_commit`` and writes only
below the caller-supplied output root. It never edits the working product tree.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import re
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DESIGN = REPO / "docs" / "DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md"
BASELINE_COMMIT = "03216b788412caa476bba49b9d8524de80919bf4"

PARSER = "backend/engine/parser/parser_legacy.py"
DISTRIBUTED = "backend/engine/rules/distributed.py"
REQUIREMENTS = "requirements.txt"
PYPROJECT = "pyproject.toml"
TARGET_FILES = (PARSER, DISTRIBUTED, REQUIREMENTS, PYPROJECT)


def normalized_bytes(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def normalized_sha256(text: str) -> str:
    return hashlib.sha256(normalized_bytes(text)).hexdigest()


def bundle_sha256(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(files):
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(normalized_bytes(files[rel]))
        digest.update(b"\0")
    return digest.hexdigest()


def _git_blob(repo: Path, commit: str, rel: str) -> str:
    proc = subprocess.run(
        ["git", "show", "%s:%s" % (commit, rel)], cwd=str(repo),
        capture_output=True, check=True,
    )
    return proc.stdout.decode("utf-8")


def marker_blocks(design_text: str) -> dict[str, str]:
    pattern = re.compile(
        r"<!-- BEGIN CODE: (?P<id>[^>]+) -->\s*"
        r"```[^\n]*\n(?P<body>.*?)\n```\s*"
        r"<!-- END CODE: (?P=id) -->",
        re.DOTALL,
    )
    out: dict[str, str] = {}
    for match in pattern.finditer(design_text):
        marker = match.group("id").strip()
        if marker in out:
            raise AssertionError("duplicate marker: %s" % marker)
        out[marker] = match.group("body")
    begin_ids = re.findall(r"<!-- BEGIN CODE: ([^>]+) -->", design_text)
    end_ids = re.findall(r"<!-- END CODE: ([^>]+) -->", design_text)
    if sorted(x.strip() for x in begin_ids) != sorted(x.strip() for x in end_ids):
        raise AssertionError("unpaired BEGIN/END marker")
    return out


def _replace_once(src: str, before: str, after: str, label: str) -> str:
    count = src.count(before)
    if count != 1:
        raise AssertionError("%s before count=%d" % (label, count))
    return src.replace(before, after, 1)


def _insert_after_once(src: str, anchor: str, body: str, label: str) -> str:
    if src.count(anchor) != 1:
        raise AssertionError("%s anchor count=%d" % (label, src.count(anchor)))
    return src.replace(anchor, anchor + "\n" + body.strip("\n"), 1)


def _replace_parse_final_return(src: str, before: str, after: str) -> str:
    start = src.index("    def parse(self, sql: str) -> ParsedSQL:")
    end = src.index("    # ── 正则预解析", start)
    part = src[start:end]
    pos = part.rfind(before)
    if pos < 0:
        raise AssertionError("parse final return anchor missing")
    part = part[:pos] + after + part[pos + len(before):]
    return src[:start] + part + src[end:]


def _replace_recovery_module(src: str, body: str) -> str:
    pattern = re.compile(
        r"\n# TDSQL 方言尾子句：.*?\n_TDSQL_DIALECT_RE\s*=\s*re\.compile\(.*?\n\)\n",
        re.DOTALL,
    )
    matches = list(pattern.finditer(src))
    if len(matches) != 1:
        raise AssertionError("legacy recovery module count=%d" % len(matches))
    match = matches[0]
    return src[:match.start()] + "\n" + body.strip("\n") + "\n" + src[match.end():]


def rebuild_texts(repo: Path = REPO, design: Path = DESIGN,
                  baseline_commit: str = BASELINE_COMMIT) -> dict[str, str]:
    doc = io.open(design, encoding="utf-8").read()
    blocks = marker_blocks(doc)
    required = {
        "IMPORT-TOKENTYPE-AFTER", "RECOVERY-MODULE-AFTER",
        "COMMAND-RETRY-BEFORE", "COMMAND-RETRY-AFTER",
        "EXCEPT-RETRY-BEFORE", "EXCEPT-RETRY-AFTER",
        "INDEX-TYPE-BEFORE", "INDEX-TYPE-AFTER",
        "SEMICOLON-BEFORE", "SEMICOLON-AFTER",
        "PARSED-UNIQUE-FIELDS-BEFORE", "PARSED-UNIQUE-FIELDS-AFTER",
        "TABLE-UNIQUE-BEFORE", "TABLE-UNIQUE-AFTER",
        "COLUMN-UNIQUE-METHOD-BEFORE", "COLUMN-UNIQUE-METHOD-AFTER",
        "UNIQUE-INIT-BEFORE", "UNIQUE-INIT-AFTER",
        "COLUMN-UNIQUE-WIRE-BEFORE", "COLUMN-UNIQUE-WIRE-AFTER",
        "TABLE-UNIQUE-WIRE-BEFORE", "TABLE-UNIQUE-WIRE-AFTER",
        "UNIQUE-COMPLETE-BEFORE", "UNIQUE-COMPLETE-AFTER",
        "SOURCE-PREFLIGHT-BEFORE", "SOURCE-PREFLIGHT-AFTER",
        "PARSE-PREFLIGHT-BEFORE", "PARSE-PREFLIGHT-AFTER",
        "PARSE-KFN-FINALIZE-BEFORE", "PARSE-KFN-FINALIZE-AFTER",
        "KFN-GATE-ASSERT-CONTAINED",
        "R054-UNIQUE-ITER-BEFORE", "R054-UNIQUE-ITER-AFTER",
        "REQUIREMENTS-SQLGLOT-BEFORE", "REQUIREMENTS-SQLGLOT-AFTER",
        "PYPROJECT-SQLGLOT-BEFORE", "PYPROJECT-SQLGLOT-AFTER",
    }
    missing = sorted(required - set(blocks))
    if missing:
        raise AssertionError("missing markers: %s" % ",".join(missing))
    extra = sorted(set(blocks) - required)
    if extra:
        raise AssertionError("unknown/unconsumed markers: %s" % ",".join(extra))

    files = {rel: _git_blob(repo, baseline_commit, rel) for rel in TARGET_FILES}
    parser = files[PARSER]
    parser = _insert_after_once(
        parser, "from sqlglot.errors import SqlglotError",
        blocks["IMPORT-TOKENTYPE-AFTER"], "import")
    parser = _replace_recovery_module(parser, blocks["RECOVERY-MODULE-AFTER"])
    for stem in (
        "COMMAND-RETRY", "EXCEPT-RETRY", "INDEX-TYPE", "SEMICOLON",
        "PARSED-UNIQUE-FIELDS", "TABLE-UNIQUE", "COLUMN-UNIQUE-METHOD",
        "UNIQUE-INIT", "COLUMN-UNIQUE-WIRE", "TABLE-UNIQUE-WIRE",
        "UNIQUE-COMPLETE", "SOURCE-PREFLIGHT", "PARSE-PREFLIGHT",
    ):
        parser = _replace_once(
            parser, blocks[stem + "-BEFORE"], blocks[stem + "-AFTER"], stem)
    parser = _replace_parse_final_return(
        parser, blocks["PARSE-KFN-FINALIZE-BEFORE"],
        blocks["PARSE-KFN-FINALIZE-AFTER"])
    gate = blocks["KFN-GATE-ASSERT-CONTAINED"]
    if parser.count(gate) != 1:
        raise AssertionError("KFN gate contained count=%d" % parser.count(gate))
    files[PARSER] = parser

    distributed_before = files[DISTRIBUTED]
    files[DISTRIBUTED] = _replace_once(
        distributed_before, blocks["R054-UNIQUE-ITER-BEFORE"],
        blocks["R054-UNIQUE-ITER-AFTER"], "R054 unique iterator")
    r077_anchor = "class R077CreateTableMustHaveShardKey"
    if (r077_anchor not in distributed_before or
            distributed_before.split(r077_anchor, 1)[1] !=
            files[DISTRIBUTED].split(r077_anchor, 1)[1]):
        raise AssertionError("R077 class/text drifted outside the approved R054 helper")
    files[REQUIREMENTS] = _replace_once(
        files[REQUIREMENTS], blocks["REQUIREMENTS-SQLGLOT-BEFORE"],
        blocks["REQUIREMENTS-SQLGLOT-AFTER"], "requirements pin")
    files[PYPROJECT] = _replace_once(
        files[PYPROJECT], blocks["PYPROJECT-SQLGLOT-BEFORE"],
        blocks["PYPROJECT-SQLGLOT-AFTER"], "pyproject pin")

    if "sqlglot==30.14.0" not in files[REQUIREMENTS]:
        raise AssertionError("requirements pin missing")
    if '"sqlglot==30.14.0"' not in files[PYPROJECT]:
        raise AssertionError("pyproject pin missing")
    if files[PARSER].count("unique_constraints_complete") < 2:
        raise AssertionError("unique completeness wiring missing")
    return files


def write_target(output_root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = output_root / Path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_root")
    parser.add_argument("--repo", default=str(REPO))
    parser.add_argument("--design", default=str(DESIGN))
    parser.add_argument("--baseline", default=BASELINE_COMMIT)
    args = parser.parse_args(argv)
    files = rebuild_texts(Path(args.repo), Path(args.design), args.baseline)
    write_target(Path(args.output_root), files)
    print("REBUILD_OK files=%d bundle_sha256=%s" % (len(files), bundle_sha256(files)))
    for rel in sorted(files):
        print("FILE %s normalized_utf8_sha256=%s" % (rel, normalized_sha256(files[rel])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
