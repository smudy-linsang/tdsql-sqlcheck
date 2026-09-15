"""读取最近 FAILED turn 的 result，确认降级原因（model.failure_code）。"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
sys.path.insert(0, str(R1))
sys.path.insert(0, str(HERE.parents[2]))

from uat_d40_api import Api, save, utc  # noqa: E402


def main():
    a = Api("uat_d_1640")
    a.relogin_after(2)
    out = {"utc": utc(), "turns": []}
    sess = a.get("/api/v1/copilot/sessions?limit=10").get("body") or {}
    for s in sess.get("items", [])[:8]:
        ts = a.get(f"/api/v1/copilot/sessions/{s['session_id']}/turns"
                   ).get("body") or {}
        for t in ts.get("items", []):
            if t.get("state") != "FAILED":
                continue
            r = a.get(f"/api/v1/copilot/turns/{t['turn_id']}/result")
            b = r.get("body") or {}
            out["turns"].append({
                "turn_id": t["turn_id"], "scene": t.get("scene"),
                "result_status": r["status"],
                "state": b.get("state"), "answer_source": b.get("answer_source"),
                "model": b.get("model"),
                "answer_summary": ((b.get("answer") or {}).get("summary") or "")[:160],
                "sources": len(b.get("sources") or []),
                "limitations": ((b.get("answer") or {}).get("limitations") or [])[:3],
            })
            if len(out["turns"]) >= 4:
                break
        if len(out["turns"]) >= 4:
            break
    save("r2_failed_turns", out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:3000])
    a.close()


if __name__ == "__main__":
    main()
