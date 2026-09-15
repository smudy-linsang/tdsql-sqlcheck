"""智能体D / v1.6.4.0 第二轮 UAT：受控实验——业务场景带证据后 runner 是否仍判"无证据"。

假设 H：「_payload() 改为投影后，_collect() 读 payload['source_refs'] 恒为空 →
       runner 收集不到任何证据 → 模型答案引用校验失败 → 降级/FAILED」。
判据：
  1) 预览 evidence_cards ≥ 1（预览侧正常）；
  2) 冻结投影 evidence ≥ 1（封存正常）；
  3) 出站报文含 evidence（发送正常）；
  4) 但轮次终态为 FAILED(EVIDENCE_UNAVAILABLE) 或 DEGRADED(OUTPUT_INVALID)。
若 1-3 成立而 4 仍失败 → 假设成立。
"""
import json
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(R1))
sys.path.insert(0, str(HERE.parents[2]))
import _boot  # noqa: F401,E402

from uat_d40_api import Api, save, utc, wait_turn, gateway_events  # noqa: E402
from uat2_d40_core import _decrypt_projection, _outbound  # noqa: E402


def main():
    out = {"utc": utc(), "cases": []}
    a = Api("uat_d_1640")
    a.relogin_after(2)
    subj = (a.get("/api/v1/copilot/capabilities").get("body") or {}).get("subject_id")

    cases = [
        ("RULE_EXPLAIN", [{"kind": "rule", "rule_ids": ["R001", "R003"]}],
         "请解释 R001 与 R003 这两条规则的要求"),
        ("SQL_ADVISE", [], "请给出 SELECT * FROM d40_tab_00 的修改建议"),
    ]
    for scene, refs, q in cases:
        s = a.post("/api/v1/copilot/sessions", json={
            "scope_kind": "INSTANCE", "connection_id": "d40-dist",
            "instance_type": "distributed", "page_key": "audit-sql"})
        sid = (s.get("body") or {}).get("session_id")
        mark = len(gateway_events())
        p = a.post(f"/api/v1/copilot/sessions/{sid}/previews", json={
            "expected_session_revision": 1, "scene": scene,
            "page_key": "audit-sql", "question": q, "source_refs": refs})
        pb = p.get("body") or {}
        row = {"scene": scene, "n_refs": len(refs),
               "preview_status": p["status"]}
        if p["status"] != 201:
            row["error"] = pb.get("detail")
            out["cases"].append(row)
            time.sleep(22)
            continue
        frozen = _decrypt_projection(pb["preview_id"], subj)
        fp = json.loads(frozen["projection"])
        row["preview_evidence_cards"] = len(pb.get("evidence_cards") or [])
        row["frozen_projection_evidence"] = len(fp.get("evidence") or [])
        row["frozen_projection_knowledge"] = len(fp.get("knowledge") or [])
        row["frozen_top_keys"] = sorted(fp.keys())

        sub = a.post(f"/api/v1/copilot/sessions/{sid}/turns", json={
            "client_request_id": uuid.uuid4().hex,
            "preview_id": pb["preview_id"], "snapshot_hash": pb["snapshot_hash"],
            "expected_session_revision": pb["expected_session_revision"],
            "confirm_data_use": True})
        tid = (sub.get("body") or {}).get("turn_id")
        term = wait_turn(a, tid, timeout=120) if tid else {}
        tb = term.get("body") or {}
        row["terminal"] = tb.get("state")
        row["terminal_error"] = tb.get("error_code")
        evs = [_outbound(e) for e in gateway_events()[mark:]]
        row["outbound_count"] = len(evs)
        if evs:
            row["outbound_evidence"] = len(evs[0]["payload"].get("evidence") or [])
            row["outbound_knowledge"] = len(evs[0]["payload"].get("knowledge") or [])
            row["outbound_matches_frozen"] = (evs[0]["user"] == frozen["projection"])
        r = a.get(f"/api/v1/copilot/turns/{tid}/result") if tid else {}
        rb = r.get("body") or {}
        row["result_state"] = rb.get("state")
        row["result_model"] = rb.get("model")
        out["cases"].append(row)
        print(json.dumps(row, ensure_ascii=False, default=str)[:600])
        time.sleep(22)
    save("r2_evidence_chain", out)
    a.close()


if __name__ == "__main__":
    main()
