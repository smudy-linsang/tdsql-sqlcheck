"""查最近若干 turn 的终态与错误码（诊断 M01 场景 FAILED 的原因）。"""
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
    "SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
    "SQLCHECK_DB_USER": "root", "SQLCHECK_DB_PASSWORD": "tdsql_test_2024",
    "SQLCHECK_DB_NAME": "uat_d_1640_meta",
    "COPILOT_KEYRING_FILE": str(ROOT / "data/reports/uat_d_1640/copilot-keyring.json"),
    "COPILOT_ENDPOINTS_FILE": str(ROOT / "data/reports/uat_d_1640/copilot-endpoints.json"),
})

from backend.services.database import _get_connection, ensure_db  # noqa: E402


def main():
    ensure_db()
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT id, scene, turn_kind, state, phase, error_code, error_message, "
            "created_at, finished_at FROM copilot_turns "
            "ORDER BY created_at DESC LIMIT 8").fetchall()
        for r in rows:
            d = dict(r)
            print(json.dumps({k: str(v)[:220] for k, v in d.items()},
                             ensure_ascii=False))
        # 取最近一个 FAILED 的响应封套看限制/原因
        f = conn.execute(
            "SELECT id, response_envelope, error_code FROM copilot_turns "
            "WHERE state='FAILED' ORDER BY created_at DESC LIMIT 1").fetchone()
        if f:
            print("\n最近 FAILED turn:", dict(f)["id"], dict(f)["error_code"])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
