"""智能体D / v1.6.4.0 第三轮 UAT：M04 feedback-summary 口径 + 防回退回归。"""
import json
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
ROOT = HERE.parents[2]
for p in (str(HERE), str(R1), str(ROOT)):
    sys.path.insert(0, p)
import _boot  # noqa: F401,E402

from uat_d40_api import Api, gateway_events, utc  # noqa: E402


def m04(a):
    out = {}
    r = a.get("/api/v1/copilot-admin/feedback-summary")
    out["status"] = r["status"]
    out["shape"] = r.get("body")
    # 造 3 个不同 subject 的 INCORRECT，再取一次
    for u in ("uat_d_1640", "uat_d_1640b", "uat_d_1640dev"):
        c = Api(u)
        c.relogin_after(2)
        sess = c.get("/api/v1/copilot/sessions?limit=8").get("body") or {}
        tid = None
        for s in sess.get("items", []):
            ts = c.get(f"/api/v1/copilot/sessions/{s['session_id']}/turns"
                       ).get("body") or {}
            for t in ts.get("items", []):
                if t.get("state") in ("SUCCEEDED", "LOCAL_ONLY", "DEGRADED",
                                      "FAILED"):
                    tid = t["turn_id"]
                    break
            if tid:
                break
        if tid:
            c.post(f"/api/v1/copilot/turns/{tid}/feedback",
                   json={"rating": -1, "code": "INCORRECT"})
        c.close()
    time.sleep(1)
    r2 = a.get("/api/v1/copilot-admin/feedback-summary")
    out["after_3_subjects"] = r2.get("body")
    items = (r2.get("body") or {}).get("items") or []
    out["item_keys"] = sorted(items[0].keys()) if items else []
    out["has_rule_snapshot_hash"] = any("rule_snapshot_hash" in i for i in items)
    out["per_rule_split"] = all("rule_id" in i for i in items) if items else None
    return out


def regression(a):
    out = {}
    r = a.post("/api/v1/audit/sql",
               json={"sql": "SELECT id FROM d40_tab_00 WHERE id = 1"})
    out["instant_audit"] = {"status": r["status"],
                            "passed": (r.get("body") or {}).get("passed")}
    t0 = time.time()
    job = a.client.post("/api/v1/audit/metadata-jobs",
                        json={"connection_id": "d40-dist", "scopes": ["TABLE"]},
                        headers={"Idempotency-Key": uuid.uuid4().hex})
    jid = (job.json() if job.status_code < 500 else {}).get("job_id")
    out["metadata_job_submit"] = job.status_code
    if jid:
        final = {}
        while time.time() - t0 < 200:
            final = (a.get(f"/api/v1/audit/metadata-jobs/{jid}").get("body") or {})
            if final.get("state") in ("SUCCEEDED", "COMPLETED", "FAILED"):
                break
            time.sleep(2)
        out["metadata_job_final"] = {"state": final.get("state"),
                                     "total_s": round(time.time() - t0, 1)}
    rbac = {}
    for u in ("uat_d_1640", "uat_d_1640b", "uat_d_1640dev", "uat_d_1640aud"):
        c = Api(u)
        c.relogin_after(2)
        rbac[u] = {"capabilities": c.get("/api/v1/copilot/capabilities")["status"],
                   "admin": c.get("/api/v1/copilot-admin/providers")["status"]}
        c.close()
    out["rbac"] = rbac
    evs = gateway_events()
    out["egress"] = {"total": len(evs),
                     "with_tools": sum(1 for e in evs
                                       if '"tools"' in (e.get("body") or "")),
                     "all_bearer": all(e.get("authorization_scheme") == "Bearer"
                                       for e in evs)}
    out["tenant_isolation"] = a.get(
        "/api/v1/copilot/sessions/nonexistent").get("status")
    return out


def main():
    out = {"utc": utc()}
    a = Api("uat_d_1640")
    a.relogin_after(2)
    out["m04"] = m04(a)
    out["regression"] = regression(a)
    (HERE / "r3_m04_regression.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:2600])
    a.close()


if __name__ == "__main__":
    main()
