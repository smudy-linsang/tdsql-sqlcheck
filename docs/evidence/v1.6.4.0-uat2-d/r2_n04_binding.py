"""核验：会话列表返回字段 vs 抽屉下拉绑定字段（N-04 修复是否可用）。"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
ROOT = HERE.parents[2]
sys.path.insert(0, str(R1))
sys.path.insert(0, str(ROOT))
import _boot  # noqa: F401,E402

from uat_d40_api import Api, utc  # noqa: E402


def main():
    a = Api("uat_d_1640")
    a.relogin_after(2)
    out = {"utc": utc()}
    sessions = a.get("/api/v1/copilot/sessions?limit=5").get("body") or {}
    items = sessions.get("items") or []
    out["session_item_keys"] = sorted(items[0].keys()) if items else []
    out["first_item"] = items[0] if items else None

    # 抽屉「新建会话」按钮：copilot.createSession() 无参调用会怎样
    r = a.post("/api/v1/copilot/sessions", json={
        "instance_type": "unknown", "page_key": "copilot-page"})
    out["create_without_scope_kind"] = {
        "status": r["status"],
        "detail": str((r.get("body") or {}).get("detail"))[:260]}
    r2 = a.post("/api/v1/copilot/sessions", json={
        "scope_kind": "GLOBAL_HELP", "instance_type": "unknown",
        "page_key": "copilot-page"})
    out["create_with_scope_kind"] = {"status": r2["status"],
                                     "keys": sorted((r2.get("body") or {}).keys())}
    # 页面模板绑定的是 s.session_id；抽屉绑定的是 s.id
    import re
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    page_sel = re.search(r'copilot-session-bar.*?</el-select>', html, re.S)
    drawer_sel = re.search(r'N-04（UAT D40）.*?</el-select>', html, re.S)
    out["page_binding"] = re.findall(r':(?:key|value)="([^"]+)"',
                                     page_sel.group(0)) if page_sel else None
    out["drawer_binding"] = re.findall(r':(?:key|value)="([^"]+)"',
                                       drawer_sel.group(0)) if drawer_sel else None
    (HERE / "r2_n04_binding.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:1800])
    a.close()


if __name__ == "__main__":
    main()
