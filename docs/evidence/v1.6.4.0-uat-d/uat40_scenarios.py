"""智能体D / v1.6.4.0 UAT 场景脚本。

  s0_smoke        环境冒烟：登录/菜单/capabilities/管理端点清单（对 OpenAPI 实测）
  s1_routes       设计的 §12.2/§12.5 端点清单 vs 实际注册路由逐条比对
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from uat_d40_api import Api, creds, save, utc  # noqa: E402


def s0_smoke():
    out = {"utc": utc(), "steps": []}
    a = Api("uat_d_1640")
    a.relogin_after(2)
    out["steps"].append({"login": "ok", "username": a.username})

    for u in ("uat_d_1640b", "uat_d_1640dev", "uat_d_1640aud"):
        c = Api(u)
        c.relogin_after(2)
        cap = c.get("/api/v1/copilot/capabilities")
        out["steps"].append({"account": u, "capabilities_status": cap["status"],
                             "mode": (cap.get("body") or {}).get("mode")})
        c.close()

    cap = a.get("/api/v1/copilot/capabilities")
    out["capabilities"] = cap
    menus = a.get("/api/v1/auth/menus")
    out["menus_admin"] = menus

    # 实际注册的 Copilot 路由清单（OpenAPI 权威枚举）
    spec = a.get("/openapi.json")
    paths = (spec.get("body") or {}).get("paths") or {}
    cop = {}
    for p, ops in sorted(paths.items()):
        if "/copilot" in p:
            cop[p] = sorted(k.upper() for k in ops
                            if k in ("get", "post", "put", "patch", "delete"))
    out["registered_copilot_routes"] = cop
    save("s0_smoke", out)
    print(json.dumps({"capabilities_mode": (cap.get("body") or {}).get("mode"),
                      "routes": cop, "menus_admin": menus.get("body")},
                     ensure_ascii=False, indent=2, default=str))
    a.close()


# 设计 DETAIL §12.5/§12.2 明确列出的端点（用于逐条比对）
DESIGNED_ADMIN = [
    ("GET", "/api/v1/copilot-admin/endpoints"),
    ("GET", "/api/v1/copilot-admin/providers"),
    ("POST", "/api/v1/copilot-admin/providers"),
    ("PUT", "/api/v1/copilot-admin/providers/{provider_id}"),
    ("POST", "/api/v1/copilot-admin/providers/{provider_id}/self-tests"),
    ("PUT", "/api/v1/copilot-admin/providers/{provider_id}/enabled"),
    ("GET", "/api/v1/copilot-admin/routes"),
    ("PUT", "/api/v1/copilot-admin/routes/{scene}"),
    ("GET", "/api/v1/copilot-admin/grants"),
    ("PUT", "/api/v1/copilot-admin/grants"),
    ("POST", "/api/v1/copilot-admin/grants/approve"),
    ("GET", "/api/v1/copilot-admin/settings"),
    ("PUT", "/api/v1/copilot-admin/settings"),
    ("GET", "/api/v1/copilot-admin/health"),
    ("GET", "/api/v1/copilot-admin/feedback-summary"),
    ("GET", "/api/v1/copilot-audit/events"),
]

DESIGNED_USER = [
    ("GET", "/api/v1/copilot/capabilities"),
    ("GET", "/api/v1/copilot/help"),
    ("GET", "/api/v1/copilot/connections"),
    ("POST", "/api/v1/copilot/sessions"),
    ("GET", "/api/v1/copilot/sessions"),
    ("GET", "/api/v1/copilot/sessions/{session_id}"),
    ("POST", "/api/v1/copilot/sessions/{session_id}/archive"),
    ("GET", "/api/v1/copilot/sessions/{session_id}/turns"),
    ("POST", "/api/v1/copilot/sessions/{session_id}/previews"),
    ("POST", "/api/v1/copilot/sessions/{session_id}/turns"),
    ("GET", "/api/v1/copilot/turns/{turn_id}"),
    ("GET", "/api/v1/copilot/turns/{turn_id}/result"),
    ("POST", "/api/v1/copilot/turns/{turn_id}/cancel"),
    ("POST", "/api/v1/copilot/turns/{turn_id}/feedback"),
    ("POST", "/api/v1/copilot/turns/{turn_id}/actions/resolve"),
    ("GET", "/api/v1/copilot/turns/{turn_id}/export.html"),
]


def s1_routes():
    a = Api("uat_d_1640")
    a.relogin_after(2)
    spec = a.get("/openapi.json")
    paths = (spec.get("body") or {}).get("paths") or {}
    reg = {}
    for p, ops in paths.items():
        if "/copilot" in p:
            reg[p] = sorted(k.upper() for k in ops
                            if k in ("get", "post", "put", "patch", "delete"))

    def check(designed):
        rows = []
        for method, path in designed:
            ops = reg.get(path)
            rows.append({"method": method, "path": path,
                         "registered": bool(ops and method in ops),
                         "registered_methods": ops or []})
        return rows

    out = {"utc": utc(), "admin": check(DESIGNED_ADMIN),
           "user": check(DESIGNED_USER),
           "registered_all": reg,
           "extra_registered": sorted(
               p for p in reg if p not in {d[1] for d in DESIGNED_ADMIN + DESIGNED_USER})}
    save("s1_routes", out)
    for grp in ("admin", "user"):
        for r in out[grp]:
            flag = "OK  " if r["registered"] else "MISS"
            print(f"[{flag}] {grp:5s} {r['method']:6s} {r['path']}")
    print("\n实际注册但不在设计清单内的 Copilot 路由：")
    for p in out["extra_registered"]:
        print("   ", reg[p], p)
    a.close()


if __name__ == "__main__":
    {"s0_smoke": s0_smoke, "s1_routes": s1_routes}.get(
        sys.argv[1] if len(sys.argv) > 1 else "s0_smoke",
        lambda: print(__doc__))()
