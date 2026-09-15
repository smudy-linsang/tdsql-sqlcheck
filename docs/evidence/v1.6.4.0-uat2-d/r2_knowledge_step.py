"""澄清：copilot_bootstrap_check() 之后知识检索是否可用（逐步取证）。"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
ROOT = HERE.parents[2]
sys.path.insert(0, str(R1))
sys.path.insert(0, str(ROOT))
import _boot  # noqa: F401,E402

os.environ.update({
    "COPILOT_KEYRING_FILE": str(ROOT / "data/reports/uat_d_1640/copilot-keyring.json"),
    "COPILOT_ENDPOINTS_FILE": str(ROOT / "data/reports/uat_d_1640/copilot-endpoints.json"),
    "SQLCHECK_DB_NAME": "uat_d_1640_meta", "SQLCHECK_DB_HOST": "127.0.0.1",
    "SQLCHECK_DB_PORT": "13306", "SQLCHECK_DB_USER": "root",
    "SQLCHECK_DB_PASSWORD": "tdsql_test_2024",
})

from backend.services.copilot import copilot_bootstrap_check, local_ready  # noqa: E402
from backend.services.copilot.knowledge import store  # noqa: E402
from backend.services.copilot.tools import ToolContext, execute_search_help  # noqa: E402

Q = "在线元数据审核任务失败时我应该先看什么证据？"


class _Ident:
    username = "uat_d_1640"
    subject_id = "x"
    role = "admin"


def _probe(tag, out):
    ctx = ToolContext(_Ident(), None,
                      {"id": "s", "scope_kind": "GLOBAL_HELP", "connection_id": None},
                      identifiers_allowed=False, knowledge_store=store)
    res = execute_search_help(ctx, {"query": Q}, 9e18)
    out[tag] = {
        "bundle_none": store.bundle is None,
        "store_status": store.status,
        "bundle_search": len(store.bundle.search(Q, product_family="TDSQL-MySQL"))
        if store.bundle else 0,
        "execute_count": len(res.get("knowledge") or []),
        "execute_status": res.get("knowledge_status"),
    }


def main():
    out = {}
    _probe("0_before_bootstrap", out)
    copilot_bootstrap_check()
    out["local_ready"] = local_ready()
    _probe("1_after_bootstrap", out)
    store.load()
    _probe("2_after_explicit_load", out)
    (HERE / "r2_knowledge_step.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:2000])


if __name__ == "__main__":
    main()
