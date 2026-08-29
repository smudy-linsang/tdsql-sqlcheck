# -*- coding: utf-8 -*-
"""计算 O-21 实现基线清单所需哈希：实现包哈希 + manifest/codestat 生成物审计哈希"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

from rebuild_from_design import (  # noqa: E402
    BASELINE_COMMIT, PARSER, REPO, TARGET_FILES, bundle_sha256, normalized_sha256,
)

current = {rel: (REPO / rel).read_text(encoding="utf-8") for rel in TARGET_FILES}
cur_hash = bundle_sha256(current)
print("implementation_bundle:", cur_hash)

# codestat：基线提交 parser vs 当前实现 parser
bl = subprocess.run(
    ["git", "show", f"{BASELINE_COMMIT}:{PARSER}"],
    cwd=str(REPO), capture_output=True, check=True).stdout.decode("utf-8")
tmp = Path(tempfile.mkdtemp(prefix="o21_codestat_"))
blp = tmp / "baseline_parser.py"
blp.write_text(bl, encoding="utf-8", newline="\n")
cs = subprocess.run(
    [sys.executable, str(HERE / "codestat.py"), str(blp), str(REPO / PARSER)],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    env=ENV, cwd=str(REPO))
print("codestat rc:", cs.returncode)
if cs.returncode != 0:
    print("codestat stderr:", (cs.stderr or "")[-800:])
    print("codestat stdout:", (cs.stdout or "")[-400:])
codestat_sha = normalized_sha256(cs.stdout.rstrip("\n")) if cs.returncode == 0 and cs.stdout.strip() else None
print("codestat_section_sha256:", codestat_sha)

# manifest_doc：当前实现树生成的清单章节
mf = subprocess.run(
    [sys.executable, str(HERE / "manifest_doc.py")],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    env=ENV, cwd=str(REPO))
print("manifest rc:", mf.returncode)
if mf.returncode != 0:
    print("manifest stderr:", (mf.stderr or "")[-800:])
manifest_sha = normalized_sha256(mf.stdout.rstrip("\n")) if mf.returncode == 0 and mf.stdout.strip() else None
print("manifest_section_sha256:", manifest_sha)

out = {
    "implementation_bundle": cur_hash,
    "codestat_section_sha256": codestat_sha,
    "manifest_section_sha256": manifest_sha,
}
(HERE / "_o21_hashes.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print("written:", HERE / "_o21_hashes.json")
