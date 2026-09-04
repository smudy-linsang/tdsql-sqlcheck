# -*- coding: utf-8 -*-
"""UAT-O-R1: verify R058 through the real metadata-enhanced HTTP endpoint.

The script is deliberately bound to the isolated UAT metadata database and a
local target connection.  Credentials are supplied through UAT_1632_PASSWORD;
no production account or secret is embedded in the evidence.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


META_DB = "tdsql_uat_o_1632_r1"
USERNAME = "uat1632_metadata_o"
TABLE = "uat1632_r058_tdsql_subp"
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def http(method: str, url: str, token: str = "", payload: dict | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def ensure_user(password: str) -> None:
    if os.environ.get("SQLCHECK_DB_NAME") != META_DB:
        raise SystemExit(f"Refusing metadata database other than {META_DB}")

    from backend.services.auth_service import auth_service, hash_password
    from backend.services.database import _get_connection, ensure_db

    ensure_db()
    if not auth_service.get_user(USERNAME, use_cache=False):
        _, error = auth_service.create_user(
            USERNAME, password, "admin", "v1.6.3.2 metadata UAT", "UAT-O-R1"
        )
        if error:
            raise RuntimeError(error)
    else:
        password_hash, salt = hash_password(password)
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE users SET password_hash=?, salt=?, failed_attempts=0, "
                "locked_until=NULL, status='active', role='admin' WHERE username=?",
                (password_hash, salt, USERNAME),
            )
            conn.commit()
        finally:
            conn.close()

    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE users SET must_change_password=0 WHERE username=?", (USERNAME,)
        )
        conn.commit()
    finally:
        conn.close()


def rule_ids(body: dict) -> set[str]:
    return {
        item["rule_id"]
        for item in body.get("audit_result", {}).get("violations", [])
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    password = os.environ["UAT_1632_PASSWORD"]
    base = os.environ.get("UAT_1632_BASE", "http://127.0.0.1:18832")
    ensure_user(password)

    status, login = http(
        "POST",
        f"{base}/api/v1/auth/login",
        payload={"username": USERNAME, "password": password},
    )
    if status != 200 or not login.get("token"):
        raise RuntimeError(f"login failed: HTTP {status}, {login}")
    token = login["token"]

    # The shared local target intentionally contains an ``xa`` database for
    # distributed-probe regression tests, so automatic probing conservatively
    # calls both aliases distributed.  Use the product's audited S0 lock for
    # the centralized leg; remove it again before leaving the fixture.
    lock_status, lock_body = http(
        "PUT",
        f"{base}/api/v1/tdsql/connections/uat1632_central/instance-type-lock",
        token,
        {
            "locked": True,
            "instance_type": "centralized",
            "reason": "UAT-O-R1 isolated architecture-scope boundary",
        },
    )
    if lock_status != 200:
        raise RuntimeError(f"centralized lock failed: HTTP {lock_status}, {lock_body}")

    cases = [
        ("UPDATE no LIMIT", f"UPDATE {TABLE} SET status=1 WHERE id>0", True),
        ("UPDATE LIMIT 1999", f"UPDATE {TABLE} SET status=1 WHERE id>0 LIMIT 1999", False),
        ("UPDATE LIMIT 2000", f"UPDATE {TABLE} SET status=1 WHERE id>0 LIMIT 2000", False),
        ("UPDATE LIMIT 2001", f"UPDATE {TABLE} SET status=1 WHERE id>0 LIMIT 2001", True),
        ("UPDATE placeholder", f"UPDATE {TABLE} SET status=1 WHERE id>0 LIMIT ?", True),
        ("UPDATE string decoy", f"UPDATE {TABLE} SET note='LIMIT 1' WHERE id>0", True),
        ("DELETE LIMIT 2000", f"DELETE FROM {TABLE} WHERE id>0 LIMIT 2000", False),
        ("DELETE LIMIT 2001", f"DELETE FROM {TABLE} WHERE id>0 LIMIT 2001", True),
    ]

    failures: list[str] = []
    try:
        for connection_id, should_be_distributed in (
            ("uat1632_dist", True),
            ("uat1632_central", False),
        ):
            http("POST", f"{base}/api/v1/tdsql/connections/{connection_id}/connect", token)
            for name, sql, distributed_expected in cases:
                status, body = http(
                    "POST",
                    f"{base}/api/v1/tdsql/audit/with-metadata",
                    token,
                    {"sql": sql, "connection_id": connection_id},
                )
                ids = rule_ids(body) if status == 200 else set()
                meta = body.get("table_metadata", {}).get(TABLE, {}) if status == 200 else {}
                effective_type = body.get("audit_result", {}).get("instance_type")
                expected = distributed_expected if should_be_distributed else False
                actual = "R058" in ids
                expected_type = "distributed" if should_be_distributed else "centralized"
                ok = (
                    status == 200
                    and bool(meta.get("is_shard_table"))
                    and actual == expected
                    and effective_type == expected_type
                )
                print(
                    f"[{'PASS' if ok else 'FAIL'}] {connection_id} | {name} | "
                    f"HTTP={status} type={effective_type} "
                    f"shard={meta.get('is_shard_table')} R058={actual}"
                )
                if not ok:
                    failures.append(
                        f"{connection_id}/{name}: status={status}, type={effective_type}, "
                        f"meta={meta}, ids={sorted(ids)}"
                    )
    finally:
        http(
            "PUT",
            f"{base}/api/v1/tdsql/connections/uat1632_central/instance-type-lock",
            token,
            {"locked": False},
        )

    if failures:
        print("Failures:")
        for failure in failures:
            print("  " + failure)
        return 1
    print("R058 metadata-enhanced boundary and architecture-scope matrix: 16/16 PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
