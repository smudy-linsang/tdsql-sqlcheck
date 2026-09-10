"""智能体D / v1.6.3.5 UAT 整改复测：对 Q 新增回归锁做变异有效性验证。

做法：对产品源码施加"回退式变异"，再跑 Q 的对应用例。
  · 变异被抓住（用例变红）= 该锁真实有效；
  · 变异仍全绿 = 假绿锁（覆盖缺口）。
每个变异执行后无论成败都从 git 恢复文件（工作区在跑前必须干净）。

只读/可逆：仅临时改写工作区文件，不提交、不推送。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PY = sys.executable

MUTATIONS = [
    {
        "id": "M1", "target": "D-01",
        "file": "backend/api/metadata_audit.py",
        "find": '            end_dt = _jp.parse_utc(job.get("finished_at"))\n'
                '            elapsed = None if end_dt is None else max(0, int((end_dt - st_dt).total_seconds()))',
        "repl": '            end_dt = _jp.parse_utc(job.get("finished_at")) or _now_utc()\n'
                '            elapsed = max(0, int((end_dt - st_dt).total_seconds()))',
        "test": "tests/test_v1635_uat3.py::test_terminal_without_finished_at_is_none",
        "desc": "把终态耗时回退到当前时间（复原 D-01 缺陷）",
    },
    {
        "id": "M2", "target": "D-01",
        "file": "backend/services/metadata_audit_repository.py",
        "find": '            extra={"finished_at": _now(), "cleanup_ok": 1 if cleanup_ok else 0})',
        "repl": '            extra={"cleanup_ok": 1 if cleanup_ok else 0})',
        "test": "tests/test_v1635_uat3.py::test_cancelled_job_writes_finished_at",
        "desc": "取消终态不写 finished_at",
    },
    {
        "id": "M3", "target": "D-02（受理接线）",
        "file": "backend/services/metadata_audit_repository.py",
        "find": "        try:\n"
                "            from backend.services import metadata_job_process as _jp\n"
                "            self.reclaim_stale_accepted(_jp.MetadataLimits.START_TIMEOUT)\n"
                "        except Exception:\n"
                "            pass   # 回收失败不阻断本次受理\n",
        "repl": "        pass   # 变异：移除受理路径的回收接线\n",
        "test": "tests/test_v1635_uat3.py",
        "desc": "移除 create_job 中的回收调用（生产接线）",
    },
    {
        "id": "M4", "target": "D-02（runner 接线）",
        "file": "backend/workers/metadata_runner.py",
        "find": "        # D-02：每轮先回收\"受理后超期未被认领\"的幽灵任务（释放唯一槽）\n"
                "        try:\n"
                "            self.repo.reclaim_stale_accepted(jp.MetadataLimits.START_TIMEOUT)\n"
                "        except Exception as e:\n"
                "            logger.error(\"回收过期未认领任务异常: %s\", e)\n",
        "repl": "        pass   # 变异：移除 runner 的回收接线\n",
        "test": "tests/test_v1635_uat3.py",
        "desc": "移除 runner._tick 中的回收调用（生产接线）",
    },
    {
        "id": "M5", "target": "D-03（分页守卫）",
        "file": "frontend/static/js/app.js",
        "find": "        if(gen!==_metaPollGen)return;  // generation 已变（新任务/离开），丢弃旧响应\n",
        "repl": "",
        "test": "tests/test_v1635_uat3.py::test_load_metadata_results_has_generation_guard",
        "desc": "移除分页回写的 generation 守卫",
    },
    {
        "id": "M6", "target": "D-03（恢复入口）",
        "file": "frontend/index.html",
        "find": '                      <!-- v1.6.3.5 / D-03：与"重新扫描"分离的"恢复查看上次任务"动作（只 GET 不新建） -->\n'
                '                      <el-button size="small" :disabled="extractAuditing" @click="recoverMetadataJob">📄 恢复查看上次任务</el-button>\n',
        "repl": "",
        "test": "tests/test_v1635_uat3.py::test_two_distinct_actions_recover_and_rescan",
        "desc": "移除「恢复查看上次任务」入口",
    },
]


def _git(*args):
    return subprocess.run(["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), *args],
                          capture_output=True, text=True)


def run_test(test_id):
    r = subprocess.run([PY, "-m", "pytest", test_id, "-q", "--tb=no", "-p", "no:cacheprovider"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stdout or "")[-400:]


results = []
dirty = _git("status", "--porcelain").stdout.strip()
for m in MUTATIONS:
    path = ROOT / m["file"]
    # 以字节读写，避免 Windows 文本模式把 LF 改成 CRLF（污染工作区）
    raw = path.read_bytes()
    original = raw.decode("utf-8")
    if m["find"] not in original:
        results.append({**{k: m[k] for k in ("id", "target", "desc")},
                        "applied": False, "note": "变异锚点未命中（源码已变？）"})
        continue
    try:
        path.write_bytes(original.replace(m["find"], m["repl"], 1).encode("utf-8"))
        rc, tail = run_test(m["test"])
        results.append({**{k: m[k] for k in ("id", "target", "desc")},
                        "applied": True, "test": m["test"],
                        "rc_after_mutation": rc, "caught": rc != 0,
                        "tail": tail.strip().splitlines()[-1] if tail.strip() else ""})
    finally:
        path.write_bytes(raw)

# 恢复后自证：原始代码下用例必须全绿
rc, tail = run_test("tests/test_v1635_uat3.py")
out = {"dirty_before": dirty, "mutations": results,
       "restored_rc": rc, "restored_green": rc == 0,
       "gap_list": [r["id"] for r in results if r.get("applied") and not r.get("caught")],
       "restored_git_status": _git("status", "--porcelain").stdout.strip()}
(HERE / "probe-mutation-q.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
