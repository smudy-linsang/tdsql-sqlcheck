"""智能体D / v1.6.4.0 UAT：GATE §5 第 1 条 —— projection_mode 与实际出站载荷一致性抽检。

三种配置各抓一次真实出站报文（受控网关逐字节落盘）：
  A 仅主腿、主腿允许标识符        → 期望 projection_mode=SCHEMA_IDENTIFIERS
  B 主腿允许 + 备用腿不允许        → 期望 ALIASED（主备不一致不得偷放真实名称）
  C 主腿 503 → 真实故障转移到备用腿 → 核对备用腿收到的内容
并核对：预览向用户展示的 model_projection_preview 是否等于真正发出的报文。
"""
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from uat_d40_api import (Api, gateway_events, save, set_gw_mode,  # noqa: E402
                         utc, wait_turn)

REAL_TABLE = "d40_tab_00"
REAL_COL = "name"
DRAFT_SQL = f"SELECT id, {REAL_COL} FROM {REAL_TABLE} WHERE id = 100"
QUESTION = f"请解释 SELECT * FROM {REAL_TABLE} 在项目规范下的风险"


def _routes(a, primary, fallback):
    out = {}
    cur = {r["scene_code"]: int(r.get("revision") or 0)
           for r in (a.get("/api/v1/copilot-admin/routes").get("body") or {}).get(
               "items", [])}
    scene = "SQL_ADVISE"
    r = a.put(f"/api/v1/copilot-admin/routes/{scene}", json={
        "primary_provider_id": primary, "fallback_provider_id": fallback,
        "privacy_profile": "INTERNAL_REDACTED",
        "expected_revision": cur.get(scene, 0)})
    out[scene] = {"status": r["status"],
                  "body": (r.get("body") or {}).get("detail")}
    return out


def _preview_and_submit(a, scene="SQL_ADVISE"):
    sess = a.post("/api/v1/copilot/sessions", json={
        "scope_kind": "INSTANCE", "connection_id": "d40-dist",
        "instance_type": "distributed", "page_key": "audit-sql"})
    sid = (sess.get("body") or {}).get("session_id")
    if not sid:
        return {"error": "session 创建失败", "resp": sess}
    prev = a.post(f"/api/v1/copilot/sessions/{sid}/previews", json={
        "expected_session_revision": 1, "scene": scene, "page_key": "audit-sql",
        "question": QUESTION, "source_refs": [],
        "draft": {"kind": "SQL", "text": DRAFT_SQL}})
    pb = prev.get("body") or {}
    if prev["status"] != 201:
        return {"preview_status": prev["status"], "preview": pb}
    sub = a.post(f"/api/v1/copilot/sessions/{sid}/turns", json={
        "client_request_id": uuid.uuid4().hex,
        "preview_id": pb["preview_id"], "snapshot_hash": pb["snapshot_hash"],
        "expected_session_revision": pb["expected_session_revision"],
        "confirm_data_use": True})
    tid = (sub.get("body") or {}).get("turn_id")
    term = wait_turn(a, tid, timeout=100) if tid else {}
    res = a.get(f"/api/v1/copilot/turns/{tid}/result") if tid else {}
    return {"session_id": sid, "preview_status": prev["status"],
            "preview": pb, "submit_status": sub["status"], "turn_id": tid,
            "terminal": (term.get("body") or {}).get("state"),
            "result_status": res.get("status"), "result": res.get("body")}


def _analyse(ev: dict) -> dict:
    body = ev.get("body") or ""
    try:
        parsed = json.loads(body)
    except Exception:
        parsed = {}
    msgs = parsed.get("messages") or []
    user_content = next((m.get("content") or "" for m in msgs
                         if m.get("role") == "user"), "")
    try:
        payload = json.loads(user_content)
    except Exception:
        payload = {}
    return {
        "port": ev.get("port"),
        "body_bytes": ev.get("body_bytes"),
        "auth_present": ev.get("authorization_present"),
        "auth_scheme": ev.get("authorization_scheme"),
        "payload_keys": sorted(payload.keys()),
        "payload_sha256": hashlib.sha256(
            user_content.encode("utf-8")).hexdigest(),
        "contains_real_table": REAL_TABLE in body,
        "has_evidence_key": "evidence" in payload,
        "has_knowledge_key": "knowledge" in payload,
        "has_draft_key": "draft" in payload,
        "has_allowed_rule_ids": "allowed_rule_ids" in payload,
        "draft_text": (payload.get("draft") or {}).get("text"),
        "question_text": payload.get("question"),
        "source_refs": payload.get("source_refs"),
        "system_prompt_head": (msgs[0].get("content") or "")[:60] if msgs else "",
        "raw_body_head": body[:700],
    }


def main():
    out = {"utc": utc(), "real_table": REAL_TABLE, "draft": DRAFT_SQL,
           "question": QUESTION, "configs": {}}
    a = Api("uat_d_1640")
    a.relogin_after(2)
    items = (a.get("/api/v1/copilot-admin/providers").get("body") or {}).get(
        "items", [])
    P = next((p["id"] for p in items if p["endpoint_id"] == "ep-uat-a"), None)
    F = next((p["id"] for p in items if p["endpoint_id"] == "ep-uat-b"), None)
    out["providers"] = {"primary": P, "fallback": F}

    out["routes_A"] = _routes(a, P, None)
    mark = len(gateway_events())
    rA = _preview_and_submit(a)
    evA = gateway_events()[mark:]
    out["configs"]["A_primary_only"] = {
        "projection_mode": (rA.get("preview") or {}).get("projection_mode"),
        "identifiers_included": (rA.get("preview") or {}).get(
            "identifiers_included"),
        "preview_status": rA.get("preview_status"),
        "terminal": rA.get("terminal"),
        "preview_projection": (rA.get("preview") or {}).get(
            "model_projection_preview"),
        "outbound_count": len(evA),
        "outbound": [_analyse(e) for e in evA],
    }

    time.sleep(22)  # preview 限速：10 次/分钟

    out["routes_B"] = _routes(a, P, F)
    mark = len(gateway_events())
    rB = _preview_and_submit(a)
    evB = gateway_events()[mark:]
    out["configs"]["B_fallback_disallows"] = {
        "projection_mode": (rB.get("preview") or {}).get("projection_mode"),
        "identifiers_included": (rB.get("preview") or {}).get(
            "identifiers_included"),
        "preview_status": rB.get("preview_status"),
        "terminal": rB.get("terminal"),
        "preview_projection": (rB.get("preview") or {}).get(
            "model_projection_preview"),
        "outbound_count": len(evB),
        "outbound": [_analyse(e) for e in evB],
    }

    time.sleep(22)

    set_gw_mode(8443, "unavail503")
    time.sleep(0.5)
    mark = len(gateway_events())
    rC = _preview_and_submit(a)
    evC = gateway_events()[mark:]
    set_gw_mode(8443, "ok")
    out["configs"]["C_primary_503_fallback"] = {
        "projection_mode": (rC.get("preview") or {}).get("projection_mode"),
        "preview_status": rC.get("preview_status"),
        "terminal": rC.get("terminal"),
        "outbound_count": len(evC),
        "outbound": [_analyse(e) for e in evC],
    }

    save("s4_egress", out)
    for name, c in out["configs"].items():
        print(f"\n=== {name} ===")
        print("  preview.projection_mode =", c.get("projection_mode"),
              "| identifiers_included =", c.get("identifiers_included"),
              "| terminal =", c.get("terminal"))
        pp = c.get("preview_projection") or {}
        if pp:
            print("  预览展示 project_mode =", pp.get("projection_mode"))
        for o in c.get("outbound", []):
            print(f"  --- 出站 port={o['port']} bytes={o['body_bytes']} "
                  f"auth={o['auth_present']}/{o['auth_scheme']}")
            print("      payload 顶层键 :", o["payload_keys"])
            print("      含真实表名     :", o["contains_real_table"],
                  "| evidence键:", o["has_evidence_key"],
                  "| knowledge键:", o["has_knowledge_key"],
                  "| allowed_rule_ids:", o["has_allowed_rule_ids"])
            print("      draft 原文     :", str(o["draft_text"])[:90])
    a.close()


if __name__ == "__main__":
    main()
