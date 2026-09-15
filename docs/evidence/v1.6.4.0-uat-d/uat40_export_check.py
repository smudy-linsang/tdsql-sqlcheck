"""补充取证：已授权实例下拉为空 + 导出报告 + 会话页模式显示。"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))
import _boot  # noqa: F401,E402

import httpx  # noqa: E402

from uat_d40_api import Api, RUNTIME, WEB, creds, save, utc  # noqa: E402


def main():
    out = {"utc": utc()}
    a = Api("uat_d_1640")
    a.relogin_after(2)

    out["grants"] = a.get("/api/v1/copilot-admin/grants").get("body")
    out["copilot_connections"] = a.get("/api/v1/copilot/connections").get("body")
    out["capabilities"] = {k: v for k, v in
                           (a.get("/api/v1/copilot/capabilities").get("body")
                            or {}).items()
                           if k in ("mode", "enabled", "runner_ready",
                                    "knowledge", "module_schema_state")}

    # 找一个已有 SUCCEEDED 的 turn，抓导出报告
    sess = a.get("/api/v1/copilot/sessions?limit=5").get("body") or {}
    turn_id = None
    for s in sess.get("items", []):
        turns = a.get(f"/api/v1/copilot/sessions/{s['session_id']}/turns").get("body") or {}
        for t in turns.get("items", []):
            if t.get("state") in ("SUCCEEDED", "LOCAL_ONLY", "DEGRADED"):
                turn_id = t["turn_id"]
                out["turn_state"] = t.get("state")
                break
        if turn_id:
            break
    out["turn_id"] = turn_id
    if turn_id:
        r = a.client.get(f"/api/v1/copilot/turns/{turn_id}/export.html")
        out["export_status"] = r.status_code
        out["export_headers"] = {k.lower(): v for k, v in r.headers.items()
                                 if k.lower() in ("content-type",
                                                  "content-disposition",
                                                  "content-security-policy")}
        body = r.text
        out["export_bytes"] = len(body.encode("utf-8"))
        out["export_has_csp_header"] = "content-security-policy" in \
            {k.lower() for k in r.headers}
        out["export_has_script"] = "<script" in body.lower()
        out["export_has_remote"] = ("http://" in body or "https://" in body)
        out["export_internal_banner"] = "内部建议资料" in body
        out["export_text_head"] = body[:1200]
        (HERE / "export_sample.html").write_text(body, encoding="utf-8")
    a.close()
    save("s5_export_and_conn", out)
    for k in ("grants", "copilot_connections", "capabilities", "turn_state",
              "export_status", "export_headers", "export_bytes",
              "export_has_script", "export_has_remote", "export_internal_banner"):
        print(f"{k:24s} = {json.dumps(out.get(k), ensure_ascii=False, default=str)[:300]}")


if __name__ == "__main__":
    main()
