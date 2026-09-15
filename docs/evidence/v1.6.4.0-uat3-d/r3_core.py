"""智能体D / v1.6.4.0 第三轮 UAT：核心验收。

① R2-B01/R2-B02 复验：带证据的业务场景能否真正走到 SUCCEEDED
② M01 防回退：出站报文是否仍逐字节等于冻结投影
③ 三配置投影抽检
"""
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

from uat_d40_api import (Api, save, set_gw_mode, utc, wait_turn,  # noqa: E402
                         gateway_events)
from uat2_d40_core import _decrypt_projection, _outbound  # noqa: E402

REAL_TABLE = "d40_tab_00"
DRAFT = f"SELECT id, name FROM {REAL_TABLE} WHERE id = 100"


def one(a, subj, tag, scene, refs, q, draft=None, mark=None):
    s = a.post("/api/v1/copilot/sessions", json={
        "scope_kind": "INSTANCE", "connection_id": "d40-dist",
        "instance_type": "distributed", "page_key": "audit-sql"})
    sid = (s.get("body") or {}).get("session_id")
    mark = len(gateway_events()) if mark is None else mark
    body = {"expected_session_revision": 1, "scene": scene,
            "page_key": "audit-sql", "question": q, "source_refs": refs}
    if draft:
        body["draft"] = {"kind": "SQL", "text": draft}
    p = a.post(f"/api/v1/copilot/sessions/{sid}/previews", json=body)
    pb = p.get("body") or {}
    row = {"tag": tag, "scene": scene, "n_refs": len(refs),
           "preview_status": p["status"]}
    if p["status"] != 201:
        row["error"] = pb.get("detail")
        return row
    frozen = _decrypt_projection(pb["preview_id"], subj)
    fp = json.loads(frozen["projection"])
    row["preview_evidence_cards"] = len(pb.get("evidence_cards") or [])
    row["frozen_evidence"] = len(fp.get("evidence") or [])
    row["frozen_knowledge"] = len(fp.get("knowledge") or [])
    row["projection_mode"] = frozen["projection_mode"]
    sub = a.post(f"/api/v1/copilot/sessions/{sid}/turns", json={
        "client_request_id": uuid.uuid4().hex, "preview_id": pb["preview_id"],
        "snapshot_hash": pb["snapshot_hash"],
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
        o = evs[0]
        row["outbound_evidence"] = len(o["payload"].get("evidence") or [])
        row["outbound_knowledge"] = len(o["payload"].get("knowledge") or [])
        row["outbound_matches_frozen"] = (o["user"] == frozen["projection"])
        row["outbound_keys"] = o["payload_keys"]
    r = a.get(f"/api/v1/copilot/turns/{tid}/result") if tid else {}
    rb = r.get("body") or {}
    row["result_state"] = rb.get("state")
    row["answer_source"] = rb.get("answer_source")
    row["sources"] = len(rb.get("sources") or [])
    row["actions"] = len(rb.get("actions") or [])
    row["summary"] = ((rb.get("answer") or {}).get("summary") or "")[:120]
    row["findings"] = len((rb.get("answer") or {}).get("findings") or [])
    return row


def main():
    out = {"utc": utc()}
    set_gw_mode(8443, "ok")
    set_gw_mode(8444, "ok")
    a = Api("uat_d_1640")
    a.relogin_after(2)
    subj = (a.get("/api/v1/copilot/capabilities").get("body") or {}).get("subject_id")

    cases = [
        ("RULE_EXPLAIN", [{"kind": "rule", "rule_ids": ["R001", "R003"]}],
         "请解释 R001 与 R003 这两条规则的要求", None),
        ("SQL_ADVISE", [], f"请给出 SELECT * FROM {REAL_TABLE} 的修改建议", DRAFT),
        ("USAGE_HELP", [], "在线元数据审核任务失败时我应该先看什么证据？", None),
    ]
    out["cases"] = []
    for scene, refs, q, draft in cases:
        row = one(a, subj, f"{scene}", scene, refs, q, draft)
        out["cases"].append(row)
        print(json.dumps(row, ensure_ascii=False, default=str)[:700])
        time.sleep(22)
    save("r3_core", out)
    a.close()


if __name__ == "__main__":
    main()
