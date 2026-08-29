"""Create an isolated role that can audit metadata but cannot see instance management."""
import os
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

if os.environ.get("SQLCHECK_DB_NAME") != "tdsql_uat_o_r7_1622_20260829":
    raise SystemExit("Refusing non-round-seven database")

from backend.services.auth_service import (
    auth_service,
    create_custom_role,
    set_role_permissions,
)
from backend.services.database import _get_connection, ensure_db


ensure_db()
role_id = "uat_o_r7_metadata_only"
result = create_custom_role(
    role_id, "UAT-O R7 元数据只读", "Synthetic UAT role without instances menu")
if result.get("error") and "已存在" not in result["error"]:
    raise RuntimeError(result["error"])
set_role_permissions(role_id, {
    "dashboard": 1,
    "schema-extractor-audit": 1,
    "instances": 0,
})

username = "uat_o_metadata_only_r7"
if not auth_service.get_user(username, use_cache=False):
    _, error = auth_service.create_user(
        username, os.environ["UAT_O_PASSWORD"], role_id,
        "UAT-O 元数据只读", "Synthetic UAT fixture")
    if error:
        raise RuntimeError(error)

conn = _get_connection()
try:
    conn.execute(
        "UPDATE users SET must_change_password=0, status='active' WHERE username=?",
        (username,),
    )
    conn.commit()
finally:
    conn.close()

print("UNASSIGNED_ROLE_READY", role_id, username)
