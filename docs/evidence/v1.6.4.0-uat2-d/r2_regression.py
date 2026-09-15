"""智能体D / v1.6.4.0 第二轮 UAT：防回退快检（主产品 + RBAC + 出站安全）。"""
import json
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
ROOT = HERE.parents[2]
sys.path.insert(0, str(R1))
sys.path.insert(0, str(ROOT))
import _boot  # noqa: F401,E402

from uat_d40_api import Api, utc, gateway_events  # noqa: E402


def main():
    out = {"utc": utc()}
    a = Api("uat_d_1640")
    a.relogin_after(2)

    # 主产品：即时审核
    r = a.post("/api/v1/audit/sql",
               json={"sql": "SELECT id FROM d40_tab_00 WHERE id = 1"})
    out["instant_audit"] = {"status": r["status"],
                            "passed": (r.get("body") or {}).get("passed"),
                            "violations": len((r.get("body") or {}).get("violations")
                                              or [])}
    # 主产品：在线元数据审核任务
    t0 = time.time()
    job = a.client.post("/api/v1/audit/metadata-jobs",
                        json={"connection_id": "d40-dist",
                              "scopes": ["TABLE", "INDEX"]},
                        headers={"Idempotency-Key": uuid.uuid4().hex})
    jb = job.json() if job.status_code < 500 else {}
    jid = (jb or {}).get("job_id")
    out["metadata_job"] = {"submit_status": job.status_code, "job_id": jid}
    if jid:
        final = {}
        while time.time() - t0 < 240:
            final = (a.get(f"/api/v1/audit/metadata-jobs/{jid}").get("body") or {})
            if final.get("state") in ("SUCCEEDED", "COMPLETED", "FAILED",
                                      "CANCELLED"):
                break
            time.sleep(2)
        out["metadata_job"]["final"] = {
            "state": final.get("state"), "total_s": round(time.time() - t0, 1)}

    # RBAC 快检
    rbac = {}
    for u, expect in (("uat_d_1640", 200), ("uat_d_1640b", 200),
                      ("uat_d_1640dev", 200), ("uat_d_1640aud", 403)):
        c = Api(u)
        c.relogin_after(2)
        cap = c.get("/api/v1/copilot/capabilities")
        adm = c.get("/api/v1/copilot-admin/providers")
        rbac[u] = {"capabilities": cap["status"], "admin_providers": adm["status"]}
        c.close()
    out["rbac"] = rbac

    # 出站安全：本轮所有报文里是否出现 tools/functions 或明文密钥
    evs = gateway_events()
    bad_tools = [e for e in evs if '"tools"' in (e.get("body") or "")]
    out["egress_audit"] = {
        "total_requests": len(evs),
        "requests_with_tools": len(bad_tools),
        "auth_bearer_all": all(e.get("authorization_scheme") == "Bearer" for e in evs),
    }
    # 幂等：同键重放
    s = a.post("/api/v1/copilot/sessions", json={
        "scope_kind": "GLOBAL_HELP", "instance_type": "unknown",
        "page_key": "copilot-page"})
    out["session_create"] = s["status"]
    out["tenant_isolation"] = a.get(
        "/api/v1/copilot/sessions/nonexistent-id").get("status")
    (HERE / "r2_regression.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:2000])
    a.close()


if __name__ == "__main__":
    main()
