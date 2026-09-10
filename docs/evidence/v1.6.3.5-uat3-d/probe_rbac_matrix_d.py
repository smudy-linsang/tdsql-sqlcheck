"""智能体D / v1.6.3.5 UAT：RBAC 菜单矩阵与在线元数据审核可达性探针。"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                   "SQLCHECK_DB_NAME": "uat_d_1635_r3_meta", "AUTH_ENABLED": "true"})
sys.path.insert(0, str(ROOT))

from backend.services.database import _get_connection  # noqa: E402
from backend.services.auth_service import auth_service  # noqa: E402

cx = _get_connection()
total = cx.execute("SELECT COUNT(*) AS n FROM role_permissions").fetchone()["n"]
rows = cx.execute("SELECT role_id, menu_key, visible FROM role_permissions "
                  "WHERE menu_key='schema-extractor-audit' ORDER BY role_id").fetchall()
per_role = cx.execute("SELECT role_id, COUNT(*) AS n FROM role_permissions "
                      "WHERE visible=1 GROUP BY role_id").fetchall()
cx.close()

out = {"role_permissions_rows": total,
       "schema_extractor_audit_rows": [dict(r) for r in rows],
       "visible_menu_count_per_role": {r["role_id"]: r["n"] for r in per_role},
       "service_visible_menus": {}}
for role in ("admin", "dba", "developer", "auditor"):
    try:
        out["service_visible_menus"][role] = sorted(
            auth_service.get_visible_menus(role))
    except Exception as e:  # noqa: BLE001
        out["service_visible_menus"][role] = f"ERR {e}"

dest = Path(__file__).resolve().parent / "probe-rbac-matrix.json"
dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
