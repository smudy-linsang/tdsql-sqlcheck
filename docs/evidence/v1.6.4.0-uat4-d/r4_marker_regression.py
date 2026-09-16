"""智能体D / v1.6.4.0 第四轮 UAT：③ 标记受理拦截复测 + 防回退回归。"""
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
R2 = HERE.parents[1] / "evidence/v1.6.4.0-uat2-d"
ROOT = HERE.parents[2]
for p in (str(HERE), str(R1), str(R2), str(ROOT)):
    sys.path.insert(0, p)
import _boot  # noqa: F401,E402

from uat_d40_api import Api, gateway_events, utc  # noqa: E402

SANDBOX = ROOT / "data/reports/uat_d_1640/emg"
PERSIST = SANDBOX.parent / "conf/copilot-disabled.json"


def _load_n02():
    spec = importlib.util.spec_from_file_location("r2n02", str(R2 / "r2_n02.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _login():
    for _ in range(25):
        try:
            a = Api("uat_d_1640")
            a.relogin_after(2)
            return a
        except Exception:
            time.sleep(2)
    return None


def marker_admission():
    m = _load_n02()
    out = {}
    PERSIST.parent.mkdir(parents=True, exist_ok=True)
    PERSIST.write_text(json.dumps({"incident_id": "UATD40R4-MARKER"}) + "\n",
                       encoding="utf-8")
    m._set_enabled(True)
    m._kill_web()
    out["web_started"] = m._start_web({"TDSQL_SQLCHECK_DIR": str(SANDBOX)}, "r4m")
    a = _login()
    if not a:
        return {"error": "登录失败"}
    cap = a.get("/api/v1/copilot/capabilities")
    out["capabilities"] = {"status": cap["status"],
                           "mode": (cap.get("body") or {}).get("mode")}
    s = a.post("/api/v1/copilot/sessions", json={
        "scope_kind": "GLOBAL_HELP", "instance_type": "unknown",
        "page_key": "copilot-page"})
    out["new_session"] = s["status"]
    sid = (s.get("body") or {}).get("session_id")
    if sid:
        p = a.post(f"/api/v1/copilot/sessions/{sid}/previews", json={
            "expected_session_revision": 1, "scene": "USAGE_HELP",
            "page_key": "copilot-page", "question": "标记存在时是否仍可受理？",
            "source_refs": []})
        out["preview"] = p["status"]
        pb = p.get("body") or {}
        if p["status"] == 201:
            sub = a.post(f"/api/v1/copilot/sessions/{sid}/turns", json={
                "client_request_id": uuid.uuid4().hex,
                "preview_id": pb["preview_id"], "snapshot_hash": pb["snapshot_hash"],
                "expected_session_revision": pb["expected_session_revision"],
                "confirm_data_use": True})
            det = sub.get("body") or {}
            d = det.get("detail") if isinstance(det.get("detail"), dict) else {}
            out["new_turn"] = {"status": sub["status"],
                               "code": d.get("code"), "message": d.get("message")}
    a.close()
    PERSIST.unlink(missing_ok=True)
    m._set_enabled(True)
    m._kill_web()
    out["restored"] = m._start_web({}, "normal")
    a = _login()
    out["mode_restored"] = ((a.get("/api/v1/copilot/capabilities").get("body")
                             or {}).get("mode")) if a else None
    if a:
        a.close()
    return out


def regression():
    a = _login()
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
    for u in ("uat_d_1640", "uat_d_1640dev", "uat_d_1640aud"):
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
    # R3-N01：重名应 4xx 而非 500
    dup = a.post("/api/v1/copilot-admin/providers", json={
        "name": "UAT-D40-主模型", "endpoint_id": "ep-uat-a",
        "protocol": "OPENAI_COMPAT_CHAT", "model_id": "x", "auth_mode": "BEARER",
        "capabilities": {"context_tokens": 32768, "max_output_field": "max_tokens",
                         "supports_temperature": True, "supports_json_schema": False,
                         "supports_json_object": True, "supports_store_false": True},
        "secret_action": "REPLACE", "secret": "sk-x"})
    d = (dup.get("body") or {}).get("detail")
    out["duplicate_name"] = {"status": dup["status"],
                             "code": d.get("code") if isinstance(d, dict) else None,
                             "message": d.get("message") if isinstance(d, dict) else None}
    a.close()
    return out


def main():
    out = {"utc": utc()}
    print("=== ③ 标记受理拦截 ===")
    out["marker"] = marker_admission()
    print(json.dumps(out["marker"], ensure_ascii=False, indent=1)[:1200])
    print("=== 防回退回归 ===")
    out["regression"] = regression()
    print(json.dumps(out["regression"], ensure_ascii=False, indent=1)[:1500])
    (HERE / "r4_marker_regression.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
