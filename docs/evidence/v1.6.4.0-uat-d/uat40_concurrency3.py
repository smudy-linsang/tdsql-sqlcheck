"""GATE §5 第 2 条补测：Copilot 满并发期间提交真实在线元数据审核任务，验证不被挤排队。"""
import json
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from uat_d40_api import Api, save, set_gw_mode, utc  # noqa: E402


def _turn(api, tag):
    s = api.post("/api/v1/copilot/sessions", json={
        "scope_kind": "GLOBAL_HELP", "instance_type": "unknown",
        "page_key": "copilot-page"})
    sid = (s.get("body") or {}).get("session_id")
    p = api.post(f"/api/v1/copilot/sessions/{sid}/previews", json={
        "expected_session_revision": 1, "scene": "USAGE_HELP",
        "page_key": "copilot-page", "question": f"并发占用 {tag}", "source_refs": []})
    b = p.get("body") or {}
    if p["status"] != 201:
        return {"preview_status": p["status"]}
    r = api.post(f"/api/v1/copilot/sessions/{sid}/turns", json={
        "client_request_id": uuid.uuid4().hex, "preview_id": b["preview_id"],
        "snapshot_hash": b["snapshot_hash"],
        "expected_session_revision": b["expected_session_revision"],
        "confirm_data_use": True})
    return {"submit_status": r["status"],
            "turn_id": (r.get("body") or {}).get("turn_id")}


def main():
    out = {"utc": utc()}
    a = Api("uat_d_1640")
    b = Api("uat_d_1640b")
    a.relogin_after(2)
    b.relogin_after(2)

    set_gw_mode(8443, "slow")
    set_gw_mode(8444, "slow")
    time.sleep(0.5)
    out["turn_A"] = _turn(a, "A")
    time.sleep(1)
    out["turn_B"] = _turn(b, "B")
    print("占用并发：", json.dumps(out, ensure_ascii=False))

    t0 = time.time()
    job = a.client.post("/api/v1/audit/metadata-jobs",
                        json={"connection_id": "d40-dist",
                              "scopes": ["TABLE", "INDEX"]},
                        headers={"Idempotency-Key": uuid.uuid4().hex})
    try:
        jb = job.json()
    except Exception:
        jb = {"text": job.text[:300]}
    out["metadata_job_submit"] = {"status": job.status_code, "body": jb,
                                  "at_s": round(time.time() - t0, 2)}
    jid = jb.get("job_id") if isinstance(jb, dict) else None
    out["metadata_job_id"] = jid
    accepted_at = time.time()
    final = {}
    if jid:
        while time.time() - t0 < 300:
            final = (a.get(f"/api/v1/audit/metadata-jobs/{jid}").get("body") or {})
            if final.get("state") in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(2)
    out["metadata_job_final"] = {
        "state": final.get("state"), "phase": final.get("phase"),
        "error_code": final.get("error_code"),
        "accepted_to_terminal_s": round(time.time() - accepted_at, 1),
        "total_s": round(time.time() - t0, 1)}
    print("元数据审核任务：", json.dumps(out["metadata_job_final"], ensure_ascii=False))

    set_gw_mode(8443, "ok")
    set_gw_mode(8444, "ok")
    save("s8c_concurrency_job", out)
    a.close()
    b.close()


if __name__ == "__main__":
    main()
