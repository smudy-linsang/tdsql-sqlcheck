"""智能体D / v1.6.3.6 UAT：对 A 的 v1.6.3.6 锁做独立变异抽查。

做法与既往一致：对产品源码施加"回退式变异"→ 跑对应锁 → 变红=锁有效；仍绿=假绿。
字节级读写 + finally 原地恢复，运行前后工作区产品文件应无差异。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PY = sys.executable

MUTATIONS = [
    {"id": "M1", "target": "BUG-02 预检系数",
     "file": "backend/services/metadata_audit_repository.py",
     "find": 'payload = int(len(results_json.encode("utf-8")) * _ESCAPE_FACTOR) + _PACKET_MARGIN',
     "repl": 'payload = int(len(results_json.encode("utf-8")) * 2) + _PACKET_MARGIN',
     "test": "tests/test_v1636_bugs.py::test_publish_precheck_no_false_double",
     "desc": "预检恢复 ×2 虚假翻倍（BUG-02 本体）"},
    {"id": "M2", "target": "BUG-02 自适应阈值",
     "file": "backend/services/metadata_audit_repository.py",
     "find": "adaptive_threshold = (int((max_pkt - _PACKET_MARGIN) / _ESCAPE_FACTOR)\n"
             "                                  if max_pkt else MAX_DB_PAYLOAD_THRESHOLD)",
     "repl": "adaptive_threshold = MAX_DB_PAYLOAD_THRESHOLD",
     "test": "tests/test_v1636_bugs.py::test_adaptive_threshold_no_compact_below_threshold",
     "desc": "阈值写死 32MiB（SIT-D/B-01 本体）"},
    {"id": "M3", "target": "BUG-01 良性跳过不写逐个块",
     "file": "backend/services/metadata_audit_pipeline.py",
     "find": "                    benign_skipped += 1\n",
     "repl": "                    benign_skipped += 1\n"
             "                    lines.append('-- [SKIPPED] SQL Object: CREATE TABLE')\n"
             "                    lines.append('-- Object Name: ' + sanitize_comment(obj_name))\n",
     "test": "tests/test_v1636_bugs.py::test_m07_benign_skip_summary_not_per_object_block",
     "desc": "良性跳过恢复逐个 [SKIPPED] 块（R3-B-01 本体）"},
    {"id": "M4", "target": "R2-M-03 实例类型安全方向",
     "file": "backend/workers/metadata_audit_worker.py",
     "find": 'instance_type=instance_type or "")',
     "repl": 'instance_type=instance_type or "distributed")',
     "test": "tests/test_v1636_bugs.py::test_m05_l6_missing_arg_and_worker_wiring",
     "desc": "worker 恢复 or distributed（探测不出类型也当分布式）"},
    {"id": "M5", "target": "M-02 跳过清单截断",
     "file": "backend/services/metadata_audit_pipeline.py",
     "find": '"skipped_list": skipped_objects[:50]}',
     "repl": '"skipped_list": skipped_objects}',
     "test": "tests/test_v1636_bugs.py::test_m9_skipped_list_truncated_at_extract_return",
     "desc": "去掉 skipped_list[:50] 截断"},
]


def _git(*a):
    return subprocess.run(["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), *a],
                          capture_output=True, text=True)


def run_test(test_id):
    r = subprocess.run([PY, "-m", "pytest", test_id, "-q", "--tb=no", "-p", "no:cacheprovider"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=900)
    return r.returncode, (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""


dirty = _git("status", "--porcelain").stdout.strip()
results = []
for m in MUTATIONS:
    p = ROOT / m["file"]
    raw = p.read_bytes()
    text = raw.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    find, repl = m["find"].replace("\n", nl), m["repl"].replace("\n", nl)
    if find not in text:
        results.append({"id": m["id"], "target": m["target"], "applied": False,
                        "note": "锚点未命中"})
        continue
    try:
        p.write_bytes(text.replace(find, repl, 1).encode("utf-8"))
        rc, tail = run_test(m["test"])
        results.append({"id": m["id"], "target": m["target"], "desc": m["desc"],
                        "applied": True, "test": m["test"].split("::")[-1],
                        "caught": rc != 0, "tail": tail})
    finally:
        p.write_bytes(raw)

rc, tail = run_test("tests/test_v1636_bugs.py")
out = {"dirty_before": dirty, "mutations": results,
       "restored_all_green": rc == 0, "restored_tail": tail,
       "gap_list": [r["id"] for r in results if r.get("applied") and not r.get("caught")],
       "product_files_changed": _git("status", "--porcelain", "--", "backend", "frontend",
                                     "tests", "deploy").stdout.strip()}
(HERE / "probe-mutation-1636.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
