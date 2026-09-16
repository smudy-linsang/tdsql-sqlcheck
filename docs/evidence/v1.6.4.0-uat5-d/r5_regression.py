"""智能体D / v1.6.4.0 第五轮 UAT：防回退回归（主产品 + RBAC + 出站安全）。"""
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

from uat_d40_api import Api, gateway_events, save, utc  # noqa: E402


def main():
    out = {"utc": utc()}
    a = Api("uat_d_1640")
    a.relogin_after(2)

    r = a.post("/api/v1/audit/sql",
               json={"sql": "SELECT id FROM d40_tab_00 WHERE id = 1"})
    out["instant_audit"] = {"status": r["status"],
                            "passed": (r.get("body") or {}).get("passed"),
                            "violations": len((r.get("body") or {}).get("violations") or [])}

    t0 = time.time()
    job = a.client.post("/api/v1/audit/metadata-jobs",
                        json={"connection_id": "d40-dist", "scopes": ["TABLE", "INDEX"]},
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
    out["tenant_isolation"] = a.get("/api/v1/copilot/sessions/nope").get("status")

    # 重名映射（第三轮已验，防回退）
    dup = a.post("/api/v1/copilot-admin/providers", json={
        "name": "UAT-D40-主模型", "endpoint_id": "ep-uat-a",
        "protocol": "OPENAI_COMPAT_CHAT", "model_id": "x", "auth_mode": "BEARER",
        "capabilities": {"context_tokens": 32768, "max_output_field": "max_tokens",
                         "supports_temperature": True, "supports_json_schema": False,
                         "supports_json_object": True, "supports_store_false": True},
        "secret_action": "REPLACE", "secret": "sk-x"})
    d = (dup.get("body") or {}).get("detail")
    out["duplicate_name"] = {"status": dup["status"],
                             "code": d.get("code") if isinstance(d, dict) else None}

    # 面板：feedbacks / health / settings 快检
    out["health"] = (a.get("/api/v1/copilot-admin/health").get("body") or {}).get("ready")
    out["feedback_summary"] = a.get(
        "/api/v1/copilot-admin/feedback-summary")["status"]
    out["audit_events"] = a.get("/api/v1/copilot-audit/events")["status"]

    save("r5_regression", out)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:1800])
    a.close()


if __name__ == "__main__":
    main()
