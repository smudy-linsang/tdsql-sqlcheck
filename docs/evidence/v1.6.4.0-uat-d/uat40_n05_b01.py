"""智能体D / v1.6.4.0 UAT：GATE §5.1 两条。

  N-05：知识包"多包并存"是**加载期**闸，不是运行期看门狗 ——
        现场换包必须重启才生效；本脚本按真实运维动作走一遍并取证。
  B-01：知识包字节与 manifest 自洽（跨平台换行根因回归）——跑随包的
        test_shipped_bundle_ready / test_sources_rebuild_matches_shipped。
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import _boot  # noqa: F401,E402

from uat_d40_api import Api, RUNTIME, save, utc  # noqa: E402

KB_ROOT = ROOT / "backend/copilot_knowledge"
REAL_BUNDLE = KB_ROOT / "kb-1.6.4.0-5b8426f429c6a89a"
EXTRA_BUNDLE = KB_ROOT / "kb-1.6.4.0-uatd40-extra"

RESTART = str(HERE / ".." / ".." / ".." / "docs" / "evidence" / "v1.6.4.0-uat-d"
              / "restart_gw_web.ps1")


def _kb_status(a):
    cap = a.get("/api/v1/copilot/capabilities").get("body") or {}
    return cap.get("knowledge"), cap.get("mode")


def _restart_web():
    subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File",
                    str(HERE / "restart_web.ps1")],
                   capture_output=True, timeout=180, cwd=str(ROOT))


def main():
    out = {"utc": utc()}
    a = Api("uat_d_1640")
    a.relogin_after(2)

    out["step0_initial"] = _kb_status(a)

    # ── 制造"多包并存"（真实运维动作：手工多放了一个包目录）──
    if EXTRA_BUNDLE.exists():
        shutil.rmtree(EXTRA_BUNDLE)
    shutil.copytree(REAL_BUNDLE, EXTRA_BUNDLE)
    out["extra_bundle_created"] = str(EXTRA_BUNDLE.name)

    # 1) 不重启：应仍为旧状态（证明是加载期闸）
    time.sleep(2)
    out["step1_without_restart"] = _kb_status(a)

    # 2) 重启 Web 后：应 INVALID + KNOWLEDGE_BUNDLE_AMBIGUOUS
    _restart_web()
    time.sleep(3)
    for _ in range(30):
        try:
            r = a.relogin_after(2)
            out["relogin"] = "ok"
            break
        except Exception:
            time.sleep(2)
    out["step2_after_restart"] = _kb_status(a)
    out["step2_health"] = (a.get("/api/v1/copilot-admin/health").get("body")
                           or {}).get("knowledge")

    # 3) 删除多余包并重启：应回到 READY
    shutil.rmtree(EXTRA_BUNDLE, ignore_errors=True)
    _restart_web()
    time.sleep(3)
    for _ in range(30):
        try:
            a.relogin_after(2)
            break
        except Exception:
            time.sleep(2)
    out["step3_after_cleanup_restart"] = _kb_status(a)
    out["extra_bundle_removed"] = not EXTRA_BUNDLE.exists()

    save("s9_n05_b01", out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:3000])


def b01():
    """B-01：知识包字节与 manifest 自洽 + 源文档重建一致。"""
    out = {"utc": utc()}
    py = sys.executable
    for name in ("test_shipped_bundle_ready",
                 "test_sources_rebuild_matches_shipped"):
        p = subprocess.run(
            [py, "-m", "pytest", f"tests/copilot/test_knowledge_output.py::{name}",
             "-q", "--no-header"],
            capture_output=True, cwd=str(ROOT), timeout=300)
        out[name] = {
            "rc": p.returncode,
            "tail": (p.stdout or b"").decode("utf-8", "replace")[-500:]}
    # 落盘字节与 manifest hash 对照
    import hashlib
    mf = json.loads((REAL_BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    for fn in ("chunks.jsonl", "index.json"):
        raw = (REAL_BUNDLE / fn).read_bytes()
        out[f"{fn}_sha256"] = hashlib.sha256(raw).hexdigest()
        out[f"{fn}_manifest"] = mf["sha256"].get(fn)
        out[f"{fn}_crlf"] = raw.count(b"\r\n")
        out[f"{fn}_lf"] = raw.count(b"\n")
    save("s10_b01", out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:2500])


if __name__ == "__main__":
    {"n05": main, "b01": b01}.get(
        sys.argv[1] if len(sys.argv) > 1 else "n05", lambda: print(__doc__))()
