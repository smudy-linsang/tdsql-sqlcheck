"""智能体D / v1.6.4.0 第二轮 UAT：驱动入口。

  run b04   自检端到端（B04 + B03）
  run m01   出站报文 == 冻结投影（M01）
  run all   b04 → m01
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(R1))
sys.path.insert(0, str(ROOT))
import _boot  # noqa: F401,E402

from uat2_d40_core import b04_selftest_e2e, m01_projection_consistency  # noqa: E402


def main(mode):
    res = {}
    if mode in ("b04", "all"):
        out = b04_selftest_e2e()
        res["b04"] = {k: v for k, v in out.items() if k != "steps"}
        print("=== B04 自检端到端 ===")
        for st in out.get("steps", []):
            for k, v in st.items():
                print(f"  {k:26s} -> {json.dumps(v, ensure_ascii=False)[:220]}")
        print(f"  终态          = {out.get('selftest_terminal')}"
              f" / err={out.get('selftest_error')}")
        print(f"  出站次数      = {out.get('selftest_outbound_count')}")
        for o in out.get("selftest_outbound", []):
            print(f"     port={o['port']} keys={o['payload_keys']} "
                  f"has_tools={o['has_tools']}")
        print(f"  provider 自检后 = {out.get('provider_after_selftest')}")

    if mode in ("m01", "all"):
        from uat_d40_api import Api
        a = Api("uat_d_1640")
        a.relogin_after(2)
        items = (a.get("/api/v1/copilot-admin/providers").get("body") or {}).get(
            "items", [])
        pa = next((p["id"] for p in items
                   if p["endpoint_id"] == "ep-uat-a" and p["enabled"]), None)
        fb = next((p["id"] for p in items
                   if p["endpoint_id"] == "ep-uat-b" and p["enabled"]), None)
        a.close()
        print(f"[m01] primary={pa} fallback={fb}")
        if not pa:
            print("[m01] 无已启用主 provider，跳过")
            return res
        out = m01_projection_consistency(pa, fb)
        res["m01"] = out
        print("\n=== M01 出站报文 vs 冻结投影 ===")
        for name, c in out["configs"].items():
            print(f"\n-- {name} --")
            if c.get("preview_status") != 201:
                print("   preview 失败:", json.dumps(c, ensure_ascii=False)[:300])
                continue
            print(f"   预览 projection_mode = {c['preview_projection_mode']}"
                  f" | 冻结 = {c['frozen_projection_mode']}")
            print(f"   终态 = {c['terminal']}"
                  f" | result_state = {c.get('result_state')}")
            print(f"   出站次数 = {c['outbound_count']}")
            print(f"   出站 == 冻结投影 : {c['match_frozen']}")
            print(f"   报文含真实表名   : {c['contains_real_table']}")
            print(f"   冻结投影顶层键   : {sorted((c['frozen_projection'] or {}).keys())}")
            for o in c["outbound"]:
                print(f"     -- port={o['port']} payload_keys={o['payload_keys']}")
    return res


if __name__ == "__main__":
    m = sys.argv[1] if len(sys.argv) > 1 else "b04"
    main(m)
