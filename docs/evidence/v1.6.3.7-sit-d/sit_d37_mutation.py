"""智能体D / v1.6.3.7 SIT：对 G 的命名锁做独立变异验证。

字节级读写 + finally 原地恢复；运行前后产品文件应无差异（脚本自证）。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PY = sys.executable
W = "backend/workers/metadata_audit_worker.py"

MUTATIONS = [
    {"id": "MA", "target": "命名还原（L1/L4 本体）", "file": W,
     "find": '    now_ts = datetime.now().strftime("%Y%m%d_%H%M%S")\n'
             '    filename = f"extracted_{db_name}_{now_ts}.sql"',
     "repl": '    filename = f"extracted_{db_name}_{job_id[:8]}.sql"',
     "test": "tests/test_v1637_filename_rule.py::test_l1_worker_filename_format_and_no_job_id",
     "desc": "把文件名改回 UUID 前缀（v1.6.3.5/1.6.3.6 缺陷态）"},
    {"id": "MB", "target": "时间戳取本地时间（L2 本体）", "file": W,
     "find": '    now_ts = datetime.now().strftime("%Y%m%d_%H%M%S")',
     "repl": '    now_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")',
     "test": "tests/test_v1637_filename_rule.py::test_l2_filename_timestamp_matches_local_now",
     "desc": "时间戳改用 UTC（东八区下与本地时间差 8 小时）"},
    {"id": "MC", "target": "created_at 时区口径（L5 本体）", "file": W,
     "find": '        now_local = _local_now_str()',
     "repl": '        now_local = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")',
     "test": "tests/test_v1637_filename_rule.py::test_l5_publish_created_at_is_local_time",
     "desc": "落库时间改用 UTC（合法值，不依赖已删除的 _utcnow，模拟真实缺陷态）"},
    {"id": "MD", "target": "L1 的 file_path/source 一致性断言", "file": W,
     "find": '            schema_sql, file_path=filename, rule_overrides=overrides,',
     "repl": '            schema_sql, file_path=filename.replace(".sql", ""), rule_overrides=overrides,',
     "test": "tests/test_v1637_filename_rule.py::test_l1_worker_filename_format_and_no_job_id",
     "desc": "审核流 file_path 与落库 source 解耦（契约漂移）"},
]


def _git(*a):
    return subprocess.run(["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), *a],
                          capture_output=True, text=True)


def run_test(t):
    r = subprocess.run([PY, "-m", "pytest", t, "-q", "--tb=no", "-p", "no:cacheprovider"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=900)
    out = (r.stdout or "").strip().splitlines()
    return r.returncode, out[-1] if out else ""


dirty = _git("status", "--porcelain", "--", "backend", "frontend", "tests", "deploy").stdout.strip()
results = []
for m in MUTATIONS:
    p = ROOT / m["file"]
    raw = p.read_bytes()
    text = raw.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    find, repl = m["find"].replace("\n", nl), m["repl"].replace("\n", nl)
    if find not in text:
        results.append({"id": m["id"], "applied": False, "note": "锚点未命中"})
        continue
    try:
        p.write_bytes(text.replace(find, repl, 1).encode("utf-8"))
        rc, tail = run_test(m["test"])
        results.append({"id": m["id"], "target": m["target"], "desc": m["desc"],
                        "applied": True, "test": m["test"].split("::")[-1],
                        "caught": rc != 0, "tail": tail})
    finally:
        p.write_bytes(raw)

rc, tail = run_test("tests/test_v1637_filename_rule.py")
out = {"dirty_before": dirty, "mutations": results,
       "restored_all_green": rc == 0, "restored_tail": tail,
       "gap_list": [r["id"] for r in results if r.get("applied") and not r.get("caught")],
       "product_files_changed": _git("status", "--porcelain", "--", "backend", "frontend",
                                     "tests", "deploy").stdout.strip()}
(HERE / "sit-mutation.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
