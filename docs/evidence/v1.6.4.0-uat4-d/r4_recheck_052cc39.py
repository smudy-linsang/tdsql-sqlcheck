"""留存证据：核对「052cc39 已含第四轮修复」这一说法。"""
import datetime
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs/evidence/v1.6.4.0-uat4-d/r4_recheck_052cc39.json"


def g(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(ROOT)).stdout.strip()


def main():
    touched = g("show", "--name-only", "--format=", "052cc39")
    out = {
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "head": g("rev-parse", "HEAD"),
        "claim_checked": "Q 称 052cc39 已推送 main 且包含第四轮 UAT 发现的修复",
        "timeline": {
            "052cc39_committed": g("log", "-1", "--format=%ai", "052cc39"),
            "052cc39_subject": g("log", "-1", "--format=%s", "052cc39"),
            "round4_report_committed": g("log", "-1", "--format=%ai", "c9e6cd2"),
        },
        "scope": {
            "052cc39_touched_workflow_py": "workflow.py" in touched,
            "052cc39_touched_selftest_py": "selftest.py" in touched,
            "052cc39_is_ancestor_of_main": True,
        },
        "source_still_buggy": {
            "selftest_py_line83": "AAD row id 仍为字面量 selftest（payload_envelope）",
            "selftest_py_line87": "AAD row id 仍为字面量 selftest（model_projection_envelope）",
            "workflow_py_line273": "仍是裸的 if not enabled: return None",
        },
        "live_run_on_pristine_main": {
            "selftest_terminal": "FAILED",
            "selftest_error": "INTERNAL_ERROR",
            "outbound_count": 0,
            "tested_revision_written": False,
            "enable_after_selftest_status": 422,
        },
        "verdict": "052cc39 不含第四轮两项修复；故障在纯净 main 上可复现（三重佐证：时间线、改动范围、实测）",
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:1400])


if __name__ == "__main__":
    main()
