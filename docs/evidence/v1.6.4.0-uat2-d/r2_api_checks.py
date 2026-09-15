"""智能体D / v1.6.4.0 第二轮 UAT：API 侧复验（M04 / N01 / N05 / N06 / B05 接口面）。"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(R1))
sys.path.insert(0, str(ROOT))
import _boot  # noqa: F401,E402

from uat_d40_api import Api, RUNTIME, save, utc  # noqa: E402
from uat2_d40_core import _decrypt_projection  # noqa: E402

OUT_OF_SCOPE = "Oracle 的 ROWNUM 分页在 TDSQL 里应该怎么写？"
IN_SCOPE = "在线元数据审核任务失败时我应该先看什么证据？"


def m04_feedback_summary(a):
    out = {}
    r = a.get("/api/v1/copilot-admin/feedback-summary")
    out["status"] = r["status"]
    out["body"] = r.get("body")
    # 造 3 个不同 subject 的 INCORRECT 反馈，验证 ≥3 subject 隐私阈值
    turns = []
    for u in ("uat_d_1640", "uat_d_1640b", "uat_d_1640dev"):
        c = Api(u)
        c.relogin_after(2)
        sess = c.get("/api/v1/copilot/sessions?limit=5").get("body") or {}
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
        if not tid:
            out.setdefault("no_turn_for", []).append(u)
            c.close()
            continue
        fr = c.post(f"/api/v1/copilot/turns/{tid}/feedback",
                    json={"rating": -1, "code": "INCORRECT"})
        turns.append({"user": u, "turn": tid, "status": fr["status"],
                      "body": fr.get("body")})
        c.close()
    out["feedback_submitted"] = turns
    time.sleep(1)
    r2 = a.get("/api/v1/copilot-admin/feedback-summary")
    out["after"] = {"status": r2["status"], "body": r2.get("body")}
    return out


def n01_knowledge_threshold(a):
    """N-01：越界主题应不返回知识；在域主题应正常返回。"""
    out = {}
    for tag, q in (("out_of_scope", OUT_OF_SCOPE), ("in_scope", IN_SCOPE)):
        s = a.post("/api/v1/copilot/sessions", json={
            "scope_kind": "GLOBAL_HELP", "instance_type": "unknown",
            "page_key": "copilot-page"})
        sid = (s.get("body") or {}).get("session_id")
        p = a.post(f"/api/v1/copilot/sessions/{sid}/previews", json={
            "expected_session_revision": 1, "scene": "USAGE_HELP",
            "page_key": "copilot-page", "question": q, "source_refs": []})
        b = p.get("body") or {}
        mp = b.get("model_projection_preview") or {}
        out[tag] = {"question": q, "preview_status": p["status"],
                    "knowledge_count": mp.get("knowledge_count"),
                    "evidence_summary": mp.get("evidence_summary")}
        time.sleep(7)
    return out


def n05_n06(a):
    """N-05 导出 CSP；N-06 draft.revision 是否进入冻结 payload。"""
    out = {}
    # N-05：找一个已终态 turn 导出
    sess = a.get("/api/v1/copilot/sessions?limit=10").get("body") or {}
    turn_id = None
    for s in sess.get("items", []):
        ts = a.get(f"/api/v1/copilot/sessions/{s['session_id']}/turns"
                   ).get("body") or {}
        for t in ts.get("items", []):
            if t.get("state") in ("SUCCEEDED", "LOCAL_ONLY", "DEGRADED"):
                turn_id = t["turn_id"]
                break
        if turn_id:
            break
    out["turn_id"] = turn_id
    if turn_id:
        r = a.client.get(f"/api/v1/copilot/turns/{turn_id}/export.html")
        out["export_status"] = r.status_code
        out["csp"] = r.headers.get("content-security-policy")
        out["csp_is_report_specific"] = (
            "default-src 'none'" in (r.headers.get("content-security-policy") or ""))
        out["content_disposition"] = r.headers.get("content-disposition")

    # N-06：预览带 draft.revision，检查冻结 payload
    s = a.post("/api/v1/copilot/sessions", json={
        "scope_kind": "INSTANCE", "connection_id": "d40-dist",
        "instance_type": "distributed", "page_key": "audit-sql"})
    sid = (s.get("body") or {}).get("session_id")
    p = a.post(f"/api/v1/copilot/sessions/{sid}/previews", json={
        "expected_session_revision": 1, "scene": "SQL_ADVISE",
        "page_key": "audit-sql", "question": "草稿版本核验",
        "source_refs": [],
        "draft": {"kind": "SQL", "text": "SELECT 1", "revision": "7"}})
    pb = p.get("body") or {}
    out["draft_preview_status"] = p["status"]
    if p["status"] == 201:
        subj = (a.get("/api/v1/copilot/capabilities").get("body") or {}).get(
            "subject_id")
        frozen = _decrypt_projection(pb["preview_id"], subj)
        payload = json.loads(frozen["payload"])
        out["frozen_payload_draft"] = payload.get("draft")
        out["draft_revision_present"] = bool(
            isinstance(payload.get("draft"), dict)
            and payload["draft"].get("revision"))
    return out


def main():
    out = {"utc": utc()}
    a = Api("uat_d_1640")
    a.relogin_after(2)
    out["n01"] = n01_knowledge_threshold(a)
    out["n05_n06"] = n05_n06(a)
    out["m04"] = m04_feedback_summary(a)
    save("r2_api_checks", out)
    for k in ("n01", "n05_n06", "m04"):
        print(f"\n=== {k} ===")
        print(json.dumps(out[k], ensure_ascii=False, indent=1, default=str)[:2200])
    a.close()


if __name__ == "__main__":
    main()
