"""第三轮补验：R2-N02（D40-N05 导出报告 CSP）——本轮已有 SUCCEEDED 轮次，可复验。"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
ROOT = HERE.parents[2]
for p in (str(HERE), str(R1), str(ROOT)):
    sys.path.insert(0, p)
import _boot  # noqa: F401,E402

from uat_d40_api import Api, utc  # noqa: E402


def main():
    a = Api("uat_d_1640")
    a.relogin_after(2)
    out = {"utc": utc()}
    turn_id = None
    sess = a.get("/api/v1/copilot/sessions?limit=10").get("body") or {}
    for s in sess.get("items", []):
        ts = a.get(f"/api/v1/copilot/sessions/{s['session_id']}/turns"
                   ).get("body") or {}
        for t in ts.get("items", []):
            if t.get("state") in ("SUCCEEDED", "LOCAL_ONLY", "DEGRADED"):
                turn_id = t["turn_id"]
                out["turn_state"] = t.get("state")
                break
        if turn_id:
            break
    out["turn_id"] = turn_id
    if turn_id:
        r = a.client.get(f"/api/v1/copilot/turns/{turn_id}/export.html")
        csp = r.headers.get("content-security-policy") or ""
        out["export_status"] = r.status_code
        out["csp"] = csp
        out["csp_report_specific"] = ("default-src 'none'" in csp
                                      and "script-src" not in csp)
        out["content_disposition"] = r.headers.get("content-disposition")
        body = r.text
        out["has_script"] = "<script" in body.lower()
        out["has_remote"] = ("http://" in body or "https://" in body)
        out["internal_banner"] = "内部建议资料" in body
        out["bytes"] = len(body.encode("utf-8"))
        (HERE / "export_sample_r3.html").write_text(body, encoding="utf-8")
    (HERE / "r3_export_csp.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:1200])
    a.close()


if __name__ == "__main__":
    main()
