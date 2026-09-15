"""定位：bundle.search 有结果，execute_search_help 却返回 0 的原因。"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
ROOT = HERE.parents[2]
sys.path.insert(0, str(R1))
sys.path.insert(0, str(ROOT))
import _boot  # noqa: F401,E402

import os  # noqa: E402
os.environ.update({
    "COPILOT_KEYRING_FILE": str(ROOT / "data/reports/uat_d_1640/copilot-keyring.json"),
    "COPILOT_ENDPOINTS_FILE": str(ROOT / "data/reports/uat_d_1640/copilot-endpoints.json"),
    "SQLCHECK_DB_NAME": "uat_d_1640_meta", "SQLCHECK_DB_HOST": "127.0.0.1",
    "SQLCHECK_DB_PORT": "13306", "SQLCHECK_DB_USER": "root",
    "SQLCHECK_DB_PASSWORD": "tdsql_test_2024",
})

from backend.services.copilot.knowledge import store  # noqa: E402
from backend.services.copilot.tools import ToolContext, execute_search_help  # noqa: E402
from backend.services.copilot.redaction import utf8_len  # noqa: E402

Q = "在线元数据审核任务失败时我应该先看什么证据？"


class _Ident:
    username = "uat_d_1640"
    subject_id = "x"
    role = "admin"


def main():
    out = {}
    out["load"] = store.load()
    b = store.bundle
    raw = b.search(Q, product_family="TDSQL-MYSQL")
    out["search_TDSQL-MYSQL_upper"] = len(raw)
    raw2 = b.search(Q, product_family="TDSQL-MySQL")
    out["search_TDSQL-MySQL"] = len(raw2)
    out["first_keys"] = sorted(raw2[0].keys()) if raw2 else []
    # 复刻 execute_search_help 的截断逻辑
    total, kept = 0, []
    for r in raw2[:8]:
        content = r["content"][:1200]
        total += utf8_len(content)
        if total > 8192:
            out["break_at_total"] = total
            break
        kept.append(r["knowledge_id"])
    out["kept_by_manual_loop"] = kept
    out["total_bytes"] = total

    ctx = ToolContext(_Ident(), None,
                      {"id": "s", "scope_kind": "GLOBAL_HELP", "connection_id": None},
                      identifiers_allowed=False, knowledge_store=store)
    res = execute_search_help(ctx, {"query": Q}, 9e18)
    out["execute_search_help_count"] = len(res.get("knowledge") or [])
    out["execute_search_help_status"] = res.get("knowledge_status")
    out["ctx_store_is_same"] = (ctx.knowledge_store is store)
    out["ctx_bundle_is_none"] = (ctx.knowledge_store.bundle is None)
    (HERE / "r2_knowledge_probe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:1500])


if __name__ == "__main__":
    main()
