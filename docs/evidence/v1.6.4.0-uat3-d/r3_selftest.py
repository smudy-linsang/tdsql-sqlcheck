"""智能体D / v1.6.4.0 第三轮 UAT：R2-B03 复验 —— 自检端到端（全走公开 API）。

同时记录一个次要发现：重复 provider 名称时返回 500（唯一键 IntegrityError 未映射为 4xx）。
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

from uat_d40_api import Api, gateway_events, save, utc, wait_turn  # noqa: E402

CAPS = {"context_tokens": 32768, "max_output_field": "max_tokens",
        "supports_temperature": True, "supports_json_schema": False,
        "supports_json_object": True, "supports_store_false": True}


def main():
    out = {"utc": utc(), "steps": []}
    a = Api("uat_d_1640")
    a.relogin_after(2)
    name = f"UAT3-自检-{uuid.uuid4().hex[:6]}"

    # 次要发现：重名
    dup = a.post("/api/v1/copilot-admin/providers", json={
        "name": "UAT2-自检主模型", "endpoint_id": "ep-uat-a",
        "protocol": "OPENAI_COMPAT_CHAT", "model_id": "x", "auth_mode": "BEARER",
        "capabilities": CAPS, "secret_action": "REPLACE", "secret": "sk-x"})
    out["duplicate_name"] = {"status": dup["status"],
                             "body": str(dup.get("body"))[:120]}

    r = a.post("/api/v1/copilot-admin/providers", json={
        "name": name, "endpoint_id": "ep-uat-a",
        "protocol": "OPENAI_COMPAT_CHAT", "model_id": "uat3-selftest",
        "auth_mode": "BEARER", "capabilities": CAPS,
        "secret_action": "REPLACE", "secret": "sk-uat3"})
    out["steps"].append({"POST /providers": {"status": r["status"],
                                             "body": r.get("body")}})
    pid = (r.get("body") or {}).get("id")
    out["provider_id"] = pid
    out["provider_name"] = name
    if not pid:
        save("r3_selftest", out)
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:1500])
        return

    en1 = a.put(f"/api/v1/copilot-admin/providers/{pid}/enabled",
                json={"expected_revision": 1, "enabled": True})
    out["steps"].append({"PUT enabled(未自检)": {
        "status": en1["status"],
        "code": ((en1.get("body") or {}).get("detail") or {}).get("code")
        if isinstance((en1.get("body") or {}).get("detail"), dict) else None}})

    mark = len(gateway_events())
    st = a.post(f"/api/v1/copilot-admin/providers/{pid}/self-tests",
                json={"client_request_id": uuid.uuid4().hex,
                      "expected_provider_revision": 1})
    out["steps"].append({"POST /self-tests": {"status": st["status"],
                                              "body": st.get("body")}})
    tid = (st.get("body") or {}).get("turn_id")
    out["selftest_turn_id"] = tid

    if tid:
        st2 = a.post(f"/api/v1/copilot-admin/providers/{pid}/self-tests",
                     json={"client_request_id": uuid.uuid4().hex,
                           "expected_provider_revision": 1})
        out["idempotent_replay"] = {"status": st2["status"],
                                    "turn_id": (st2.get("body") or {}).get("turn_id"),
                                    "reused": (st2.get("body") or {}).get("reused"),
                                    "same_turn": (st2.get("body") or {}).get(
                                        "turn_id") == tid}

    term = wait_turn(a, tid, timeout=150) if tid else {}
    tb = term.get("body") or {}
    out["selftest_terminal"] = tb.get("state")
    out["selftest_error"] = tb.get("error_code")

    evs = [e for e in gateway_events()[mark:]]
    out["selftest_outbound_count"] = len(evs)
    if evs:
        body = evs[0].get("body") or ""
        try:
            parsed = json.loads(body)
            user = next((m.get("content") or "" for m in
                         (parsed.get("messages") or [])
                         if m.get("role") == "user"), "")
        except Exception:
            user = ""
        out["selftest_outbound"] = {
            "port": evs[0].get("port"),
            "contains_real_identifier": "d40_tab_00" in body,
            "has_tools": '"tools"' in body,
            "user_head": user[:200]}

    prov = (a.get("/api/v1/copilot-admin/providers").get("body") or {}).get("items", [])
    item = next((p for p in prov if p["id"] == pid), {})
    out["provider_after_selftest"] = {k: item.get(k) for k in
                                      ("enabled", "revision", "tested_revision")}

    en2 = a.put(f"/api/v1/copilot-admin/providers/{pid}/enabled",
                json={"expected_revision": 1, "enabled": True})
    out["steps"].append({"PUT enabled(自检后)": {"status": en2["status"],
                                                "body": en2.get("body")}})
    save("r3_selftest", out)
    for k, v in out.items():
        if k != "steps":
            print(f"{k}: {json.dumps(v, ensure_ascii=False, default=str)[:300]}")
    for s in out["steps"]:
        for k, v in s.items():
            print(f"  {k}: {json.dumps(v, ensure_ascii=False, default=str)[:220]}")
    a.close()


if __name__ == "__main__":
    main()
