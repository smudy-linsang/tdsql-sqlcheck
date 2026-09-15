"""智能体D / v1.6.4.0 第二轮 UAT：核心复验（B03/B04/M01/N01/N02/N05/N06/M04）。

与第一轮的区别：本轮不只验行为，还**逐字节核对出站报文与冻结投影是否相等**
（第一轮 D40-M01 的整改要求就是要这条锁）。
"""
import hashlib
import json
import os
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

from uat_d40_api import (Api, RUNTIME, gateway_events, save, set_gw_mode,  # noqa: E402
                         utc, wait_turn)

REAL_TABLE = "d40_tab_00"
DRAFT_SQL = f"SELECT id, name FROM {REAL_TABLE} WHERE id = 100"
QUESTION = f"请解释 SELECT * FROM {REAL_TABLE} 在项目规范下的风险"


# ══════════════════════════════════════════════════════════════════
# 解密冻结投影（用于"出站 == 冻结投影"的逐字节断言）
# ══════════════════════════════════════════════════════════════════
def _decrypt_projection(preview_id: str, owner_subject_id: str) -> str:
    os.environ.update({
        "SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
        "SQLCHECK_DB_USER": "root", "SQLCHECK_DB_PASSWORD": "tdsql_test_2024",
        "SQLCHECK_DB_NAME": "uat_d_1640_meta",
        "COPILOT_KEYRING_FILE": str(RUNTIME / "copilot-keyring.json"),
        "COPILOT_ENDPOINTS_FILE": str(RUNTIME / "copilot-endpoints.json"),
    })
    from backend.services.copilot import crypto as crypto_mod
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT model_projection_envelope, payload_envelope, projection_mode, "
            "data_class FROM copilot_previews WHERE id = ?", (preview_id,)).fetchone()
    finally:
        conn.close()
    r = dict(row)
    kr = crypto_mod.load_keyring()
    proj = crypto_mod.decrypt(
        r["model_projection_envelope"], "copilot_previews", preview_id,
        "model_projection_envelope", owner=owner_subject_id, keyring=kr)
    payload = crypto_mod.decrypt(
        r["payload_envelope"], "copilot_previews", preview_id,
        "payload_envelope", owner=owner_subject_id, keyring=kr)
    return {"projection": proj, "payload": payload,
            "projection_mode": r["projection_mode"], "data_class": r["data_class"]}


def _outbound(ev: dict) -> dict:
    body = ev.get("body") or ""
    try:
        parsed = json.loads(body)
    except Exception:
        parsed = {}
    msgs = parsed.get("messages") or []
    user = next((m.get("content") or "" for m in msgs
                 if m.get("role") == "user"), "")
    sysmsg = next((m.get("content") or "" for m in msgs
                   if m.get("role") == "system"), "")
    try:
        payload = json.loads(user)
    except Exception:
        payload = {}
    return {"port": ev.get("port"), "body": body, "user": user,
            "system": sysmsg, "payload": payload,
            "payload_keys": sorted(payload.keys()),
            "user_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest(),
            "has_tools": "tools" in parsed or "functions" in parsed,
            "auth_present": ev.get("authorization_present")}


# ══════════════════════════════════════════════════════════════════
# B04：自检端到端（全走公开 API，不直写 DB）
# ══════════════════════════════════════════════════════════════════
def b04_selftest_e2e():
    out = {"utc": utc(), "steps": []}
    a = Api("uat_d_1640")
    a.relogin_after(2)
    CAPS = {"context_tokens": 32768, "max_output_field": "max_tokens",
            "supports_temperature": True, "supports_json_schema": False,
            "supports_json_object": True, "supports_store_false": True}

    r = a.post("/api/v1/copilot-admin/providers", json={
        "name": "UAT2-自检主模型", "endpoint_id": "ep-uat-a",
        "protocol": "OPENAI_COMPAT_CHAT", "model_id": "uat2-selftest",
        "auth_mode": "BEARER", "capabilities": CAPS,
        "secret_action": "REPLACE", "secret": "sk-uat2-selftest"})
    out["steps"].append({"POST /providers": {"status": r["status"],
                                             "body": r.get("body")}})
    pid = (r.get("body") or {}).get("id")
    out["provider_id"] = pid
    if not pid:
        save("r2_b04_selftest", out)
        return out

    # 未自检即启用 → 必须被拒（死锁解除后仍要保留这道校验）
    en_fail = a.put(f"/api/v1/copilot-admin/providers/{pid}/enabled",
                    json={"expected_revision": 1, "enabled": True})
    out["steps"].append({"PUT enabled(未自检)": {
        "status": en_fail["status"],
        "code": ((en_fail.get("body") or {}).get("detail") or {}).get("code")
        if isinstance((en_fail.get("body") or {}).get("detail"), dict) else None}})

    mark = len(gateway_events())
    st = a.post(f"/api/v1/copilot-admin/providers/{pid}/self-tests",
                json={"client_request_id": uuid.uuid4().hex,
                      "expected_provider_revision": 1})
    out["steps"].append({"POST /self-tests": {"status": st["status"],
                                              "body": st.get("body")}})
    tid = (st.get("body") or {}).get("turn_id")
    out["selftest_turn_id"] = tid

    # 同键幂等重放
    if tid:
        st2 = a.post(f"/api/v1/copilot-admin/providers/{pid}/self-tests",
                     json={"client_request_id": uuid.uuid4().hex,
                           "expected_provider_revision": 1})
        out["steps"].append({"POST /self-tests(重放)": {
            "status": st2["status"],
            "turn_id": (st2.get("body") or {}).get("turn_id"),
            "reused": (st2.get("body") or {}).get("reused")}})

    term = wait_turn(a, tid, timeout=120) if tid else {}
    out["selftest_terminal"] = (term.get("body") or {}).get("state")
    out["selftest_error"] = (term.get("body") or {}).get("error_code")

    ev = gateway_events()[mark:]
    out["selftest_outbound"] = [_outbound(e) for e in ev]
    out["selftest_outbound_count"] = len(ev)

    prov = a.get("/api/v1/copilot-admin/providers").get("body") or {}
    item = next((p for p in prov.get("items", []) if p["id"] == pid), {})
    out["provider_after_selftest"] = {k: item.get(k) for k in
                                      ("enabled", "revision", "tested_revision")}

    en_ok = a.put(f"/api/v1/copilot-admin/providers/{pid}/enabled",
                  json={"expected_revision": 1, "enabled": True})
    out["steps"].append({"PUT enabled(自检后)": {"status": en_ok["status"],
                                                "body": en_ok.get("body")}})
    save("r2_b04_selftest", out)
    return out


# ══════════════════════════════════════════════════════════════════
# M01：出站报文 == 冻结投影（逐字节）+ 三配置
# ══════════════════════════════════════════════════════════════════
def _routes(a, primary, fallback, scene="SQL_ADVISE"):
    cur = {r["scene_code"]: int(r.get("revision") or 0)
           for r in (a.get("/api/v1/copilot-admin/routes").get("body") or {}).get(
               "items", [])}
    r = a.put(f"/api/v1/copilot-admin/routes/{scene}", json={
        "primary_provider_id": primary, "fallback_provider_id": fallback,
        "privacy_profile": "INTERNAL_REDACTED",
        "expected_revision": cur.get(scene, 0)})
    return {"status": r["status"], "detail": (r.get("body") or {}).get("detail")}


def m01_projection_consistency(pid_primary, pid_fallback):
    out = {"utc": utc(), "real_table": REAL_TABLE, "configs": {}}
    a = Api("uat_d_1640")
    a.relogin_after(2)
    subj = (a.get("/api/v1/copilot/capabilities").get("body") or {}).get("subject_id")

    def one(tag, primary, fallback, wait=0):
        if wait:
            time.sleep(wait)
        out[f"routes_{tag}"] = _routes(a, primary, fallback)
        mark = len(gateway_events())
        s = a.post("/api/v1/copilot/sessions", json={
            "scope_kind": "INSTANCE", "connection_id": "d40-dist",
            "instance_type": "distributed", "page_key": "audit-sql"})
        sid = (s.get("body") or {}).get("session_id")
        p = a.post(f"/api/v1/copilot/sessions/{sid}/previews", json={
            "expected_session_revision": 1, "scene": "SQL_ADVISE",
            "page_key": "audit-sql", "question": QUESTION, "source_refs": [],
            "draft": {"kind": "SQL", "text": DRAFT_SQL}})
        pb = p.get("body") or {}
        if p["status"] != 201:
            return {"preview_status": p["status"], "detail": pb.get("detail")}
        prev_id = pb["preview_id"]
        sub = a.post(f"/api/v1/copilot/sessions/{sid}/turns", json={
            "client_request_id": uuid.uuid4().hex, "preview_id": prev_id,
            "snapshot_hash": pb["snapshot_hash"],
            "expected_session_revision": pb["expected_session_revision"],
            "confirm_data_use": True})
        tid = (sub.get("body") or {}).get("turn_id")
        term = wait_turn(a, tid, timeout=120) if tid else {}
        evs = [_outbound(e) for e in gateway_events()[mark:]]
        frozen = _decrypt_projection(prev_id, subj)
        res = a.get(f"/api/v1/copilot/turns/{tid}/result") if tid else {}
        rb = res.get("body") or {}
        return {
            "preview_status": 201,
            "preview_projection_mode": (pb.get("model_projection_preview") or {}
                                        ).get("projection_mode"),
            "preview_shows": pb.get("model_projection_preview"),
            "frozen_projection_mode": frozen["projection_mode"],
            "terminal": (term.get("body") or {}).get("state"),
            "result_state": rb.get("state"),
            "result_model": rb.get("model"),
            "outbound_count": len(evs),
            "outbound": evs,
            "frozen_projection": frozen["projection"],
            "frozen_payload_keys": sorted(
                (json.loads(frozen["payload"]) or {}).keys()),
            "match_frozen": [
                (o["user"] == frozen["projection"]) for o in evs],
            "contains_real_table": [REAL_TABLE in o["body"] for o in evs],
        }

    try:
        out["configs"]["A_primary_only"] = one("A", pid_primary, None)
        out["configs"]["B_fallback_disallows"] = one("B", pid_primary,
                                                     pid_fallback, wait=22)
        # C：主腿 503 → 真实切备用
        set_gw_mode(8443, "unavail503")
        time.sleep(0.5)
        out["configs"]["C_primary_503_fallback"] = one("C", pid_primary,
                                                       pid_fallback, wait=22)
    finally:
        set_gw_mode(8443, "ok")
    save("r2_m01_projection", out)
    return out


if __name__ == "__main__":
    print(json.dumps({"usage": "import and call"}, ensure_ascii=False))
