"""智能体D / v1.6.3.5 UAT：直接判定 check_permission 与 visible_menus，定位 403 原因。"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                   "SQLCHECK_DB_NAME": "uat_d_1635_r3_meta", "AUTH_ENABLED": "true"})
sys.path.insert(0, str(ROOT))

from backend.services import auth_service as A  # noqa: E402

path = "/api/v1/audit/metadata-jobs"
out = {"path": path, "per_role": {}}
for role in ("admin", "dba", "developer", "auditor"):
    menus = sorted(A.get_visible_menus(role))
    out["per_role"][role] = {
        "visible_menus_has_schema_extractor_audit": "schema-extractor-audit" in menus,
        "visible_menu_count": len(menus),
        "check_permission_GET": A.check_permission(role, "GET", path),
        "check_permission_POST": A.check_permission(role, "POST", path),
    }
# 命中的菜单前缀
sorted_prefixes = sorted(A._PATH_TO_MENU.keys(), key=len, reverse=True)
hit = next((p for p in sorted_prefixes if path == p or path.startswith(p + "/")), None)
out["matched_prefix"] = hit
out["matched_menu"] = A._PATH_TO_MENU.get(hit) if hit else None
out["developer_denied_prefixes"] = list(getattr(A, "_DEVELOPER_DENIED_PREFIXES", []))
dest = Path(__file__).resolve().parent / "probe-check-permission.json"
dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
