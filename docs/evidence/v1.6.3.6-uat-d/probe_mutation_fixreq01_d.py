"""智能体D / v1.6.3.6 补丁复测：对 Q 的新锁做独立变异抽查（FIXREQ-v1.6.3.6-01）。

字节级读写 + finally 原地恢复；运行前后工作区产品文件应无差异。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PY = sys.executable

MUTATIONS = [
    {"id": "M1", "target": "L1/L2 回收 PUBLISHING/PUBLISHED",
     "file": "backend/services/metadata_audit_repository.py",
     "find": "        elif state in (STATE_PUBLISHING, STATE_PUBLISHED):\n",
     "repl": "        elif False and state in (STATE_PUBLISHING, STATE_PUBLISHED):\n",
     "test": "tests/test_v1637_fixreq01.py::test_reclaim_published_orphan_converges",
     "desc": "回收不再处理 PUBLISHING/PUBLISHED（UAT36-01 本体）"},
    {"id": "M2", "target": "L9 stale_running 暴露",
     "file": "backend/api/metadata_audit.py",
     "find": '    stale_running = (job.get("state") == "RUNNING"\n',
     "repl": '    stale_running = (False and job.get("state") == "RUNNING"\n',
     "test": "tests/test_v1637_fixreq01.py::test_job_summary_exposes_stale_running",
     "desc": "stale_running 恒为假（§2.1b 可观测性丢失）"},
    {"id": "M3", "target": "L6 complete() 返回值检查",
     "file": "backend/workers/metadata_runner.py",
     "find": "            if not ok:   # FIXREQ-v1.6.3.6-01 §2.2：CAS 未命中不得静默继续放槽\n",
     "repl": "            if False:   # 变异：忽略 complete() 未命中\n",
     "test": "tests/test_v1637_fixreq01.py::test_complete_miss_does_not_release_slot",
     "desc": "忽略 complete() 未命中（静默放槽回归）"},
    {"id": "M4", "target": "L6 放槽前终态护栏",
     "file": "backend/workers/metadata_runner.py",
     "find": "        if cur.get(\"state\") not in R.TERMINAL_STATES:\n",
     "repl": "        if False and cur.get(\"state\") not in R.TERMINAL_STATES:\n",
     "test": "tests/test_v1637_fixreq01.py::test_complete_miss_does_not_release_slot",
     "desc": "去掉「非终态不得放槽」护栏"},
    {"id": "M5", "target": "L8 前端活动判定",
     "file": "frontend/static/js/app.js",
     "find": "const _isReallyActive=(d)=>META_ACTIVE_STATES.includes(d.state)&&d.slot_owned!==false&&!d.stale_running;",
     "repl": "const _isReallyActive=(d)=>META_ACTIVE_STATES.includes(d.state);",
     "test": "tests/test_v1637_fixreq01.py::test_recover_treats_unowned_as_inactive",
     "desc": "前端判定退回「只看 state」（回到锁死）"},
    {"id": "M6", "target": "L10 RUNNING 不回收（§2.1b 决策）",
     "file": "backend/services/metadata_audit_repository.py",
     "find": "        if state == STATE_ACCEPTED:\n",
     "repl": "        if state in (STATE_ACCEPTED, STATE_RUNNING):\n",
     "test": "tests/test_v1637_fixreq01.py::test_reclaim_never_touches_running",
     "desc": "把 RUNNING 也纳入回收分支（SQL 内层 state 守卫仍在，属等价变异）"},
    # ── 组合变异：一次性移除"整层防护"，检验锁对真实缺陷是否有效 ──
    {"id": "M7", "target": "L6 两层护栏同时移除（真实缺陷）",
     "file": "backend/workers/metadata_runner.py",
     "edits": [("            if not ok:   # FIXREQ-v1.6.3.6-01 §2.2：CAS 未命中不得静默继续放槽\n",
                "            if False:\n"),
               ("        if cur.get(\"state\") not in R.TERMINAL_STATES:\n",
                "        if False and cur.get(\"state\") not in R.TERMINAL_STATES:\n")],
     "test": "tests/test_v1637_fixreq01.py::test_complete_miss_does_not_release_slot",
     "desc": "同时去掉 complete() 检查与放槽前护栏 → 静默放槽缺陷复活"},
    {"id": "M8", "target": "L10 内层 state 守卫移除（真实缺陷）",
     "file": "backend/services/metadata_audit_repository.py",
     "edits": [("        if state == STATE_ACCEPTED:\n",
                "        if state in (STATE_ACCEPTED, STATE_RUNNING):\n"),
               ("                         now, now, jid, STATE_ACCEPTED))\n",
                "                         now, now, jid, state))\n")],
     "test": "tests/test_v1637_fixreq01.py::test_reclaim_never_touches_running",
     "desc": "分支条件与 SQL 守卫同时放开 → RUNNING 真会被回收"},
]


def _git(*a):
    return subprocess.run(["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), *a],
                          capture_output=True, text=True)


def run_test(t):
    r = subprocess.run([PY, "-m", "pytest", t, "-q", "--tb=no", "-p", "no:cacheprovider"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=900)
    out = (r.stdout or "").strip().splitlines()
    return r.returncode, out[-1] if out else ""


dirty = _git("status", "--porcelain").stdout.strip()
results = []
for m in MUTATIONS:
    p = ROOT / m["file"]
    raw = p.read_bytes()
    text = raw.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    edits = m.get("edits") or [(m["find"], m["repl"])]
    mutated, miss = text, None
    for find, repl in edits:
        f, r = find.replace("\n", nl), repl.replace("\n", nl)
        if f not in mutated:
            miss = f[:60]
            break
        mutated = mutated.replace(f, r, 1)
    if miss:
        results.append({"id": m["id"], "applied": False, "note": f"锚点未命中: {miss}"})
        continue
    try:
        p.write_bytes(mutated.encode("utf-8"))
        rc, tail = run_test(m["test"])
        results.append({"id": m["id"], "target": m["target"], "desc": m["desc"],
                        "applied": True, "test": m["test"].split("::")[-1],
                        "caught": rc != 0, "tail": tail})
    finally:
        p.write_bytes(raw)

rc, tail = run_test("tests/test_v1637_fixreq01.py")
out = {"dirty_before": dirty, "mutations": results,
       "restored_all_green": rc == 0, "restored_tail": tail,
       "gap_list": [r["id"] for r in results if r.get("applied") and not r.get("caught")],
       "product_files_changed": _git("status", "--porcelain", "--", "backend", "frontend",
                                     "tests", "deploy").stdout.strip()}
(HERE / "probe-mutation-fixreq01.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
