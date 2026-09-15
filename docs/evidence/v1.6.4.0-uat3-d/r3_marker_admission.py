"""智能体D / v1.6.4.0 第三轮 UAT：持久停用标记的一致性。

设计 §16.6 要求"立即停止新受理"。标记存在时：
  · capabilities 应为 DISABLED（已验）
  · **新受理也应有据可依**——否则出现"页面说未启用、实际仍能受理"的不一致。
本脚本在标记存在 + DB enabled=true 的条件下，走到提交 turn 这一步。
"""
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

from uat_d40_api import Api, RUNTIME, utc  # noqa: E402

SANDBOX = RUNTIME / "emg"
PERSIST = SANDBOX.parent / "conf/copilot-disabled.json"


def _login():
    for _ in range(25):
        try:
            a = Api("uat_d_1640")
            a.relogin_after(2)
            return a
        except Exception:
            time.sleep(2)
    return None


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "r2n02", str(R1 / ".." / "v1.6.4.0-uat2-d" / "r2_n02.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    out = {"utc": utc()}
    PERSIST.parent.mkdir(parents=True, exist_ok=True)
    PERSIST.write_text(json.dumps({"incident_id": "UATD40R3-MARKER"}) + "\n",
                       encoding="utf-8")
    m._set_enabled(True)
    m._kill_web()
    out["web_started"] = m._start_web({"TDSQL_SQLCHECK_DIR": str(SANDBOX)}, "r3m")
    a = _login()
    if not a:
        out["error"] = "登录失败"
    else:
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
                "page_key": "copilot-page",
                "question": "标记存在时是否仍可受理？", "source_refs": []})
            out["preview"] = {"status": p["status"],
                              "code": ((p.get("body") or {}).get("detail") or {}
                                       ).get("code")
                              if isinstance((p.get("body") or {}).get("detail"), dict)
                              else None}
            pb = p.get("body") or {}
            if p["status"] == 201:
                sub = a.post(f"/api/v1/copilot/sessions/{sid}/turns", json={
                    "client_request_id": uuid.uuid4().hex,
                    "preview_id": pb["preview_id"],
                    "snapshot_hash": pb["snapshot_hash"],
                    "expected_session_revision": pb["expected_session_revision"],
                    "confirm_data_use": True})
                out["new_turn"] = {
                    "status": sub["status"],
                    "code": ((sub.get("body") or {}).get("detail") or {}).get("code")
                    if isinstance((sub.get("body") or {}).get("detail"), dict)
                    else None}
        a.close()

    # 恢复
    PERSIST.unlink(missing_ok=True)
    m._set_enabled(True)
    m._kill_web()
    out["restored"] = m._start_web({}, "normal")
    a = _login()
    out["mode_restored"] = ((a.get("/api/v1/copilot/capabilities").get("body")
                             or {}).get("mode")) if a else None
    (HERE / "r3_marker_admission.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:1500])


if __name__ == "__main__":
    main()
