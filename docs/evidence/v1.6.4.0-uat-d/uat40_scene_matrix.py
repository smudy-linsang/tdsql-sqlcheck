"""探测各场景的来源必填要求与三闸投影模式（GATE §5.1 三闸实测准备）。"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from uat_d40_api import Api, save, utc  # noqa: E402

DRAFT = {"kind": "SQL",
         "text": "SELECT id, name FROM d40_tab_00 WHERE id = 100"}
CASES = [
    ("SQL_ADVISE", []),
    ("SQL_ADVISE", [{"kind": "rule", "rule_ids": ["R001"]}]),
    ("RULE_EXPLAIN", [{"kind": "rule", "rule_ids": ["R001"]}]),
    ("USAGE_HELP", []),
    ("DIAGNOSTIC_HELP", []),
    ("RULE_EXPLAIN", []),
]


def main():
    a = Api("uat_d_1640")
    a.relogin_after(2)
    rows = []
    for scene, refs in CASES:
        s = a.post("/api/v1/copilot/sessions", json={
            "scope_kind": "INSTANCE", "connection_id": "d40-dist",
            "instance_type": "distributed", "page_key": "audit-sql"})
        sid = (s.get("body") or {}).get("session_id")
        p = a.post(f"/api/v1/copilot/sessions/{sid}/previews", json={
            "expected_session_revision": 1, "scene": scene, "page_key": "audit-sql",
            "question": "SELECT * FROM d40_tab_00 有什么风险",
            "source_refs": refs, "draft": DRAFT})
        b = p.get("body") or {}
        det = b.get("detail")
        err = det.get("code") if isinstance(det, dict) else det
        rows.append({"scene": scene, "n_refs": len(refs), "status": p["status"],
                     "projection_mode": b.get("projection_mode"),
                     "identifiers_included": b.get("identifiers_included"),
                     "err": err,
                     "preview_id": b.get("preview_id"),
                     "session_id": sid,
                     "projection_preview": b.get("model_projection_preview"),
                     "evidence_cards": b.get("evidence_cards")})
        print(f"{scene:16s} refs={len(refs)} -> HTTP {p['status']} "
              f"mode={b.get('projection_mode')} "
              f"ids={b.get('identifiers_included')} err={str(err)[:160]}")
    save("s4_scene_matrix", {"utc": utc(), "rows": rows})
    a.close()


if __name__ == "__main__":
    main()
