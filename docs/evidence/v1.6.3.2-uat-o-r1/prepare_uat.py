"""Prepare an isolated, reproducible v1.6.3.2 browser-UAT fixture.

The script refuses to run unless SQLCHECK_DB_NAME points at the dedicated UAT
database.  It creates two non-production users, two local test connections and
enough snapshots for every changed compare page to span three pages.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

META_DB = "tdsql_uat_o_1632_r1"
FIXTURE_PREFIX = "UAT1632-R1"
SNAPSHOT_MODULES = ("schema_audit", "slow_scan", "launch_check", "bigtable")


def main() -> None:
    if os.environ.get("SQLCHECK_DB_NAME") != META_DB:
        raise SystemExit(f"Refusing metadata database other than {META_DB}")

    password = os.environ["UAT_1632_PASSWORD"]
    target_password = os.environ["UAT_TDSQL_PASSWORD"]

    from backend.services.auth_service import auth_service
    from backend.services.connection_registry import registry
    from backend.services.database import _get_connection, ensure_db
    from backend.services.scan_snapshot_service import create_snapshot
    from backend.services.snapshot_extractors.base import IssueItem, fp

    ensure_db()
    auth_service.ensure_bootstrap_admin()

    for role in ("developer", "auditor"):
        username = f"uat1632_{role}"
        if not auth_service.get_user(username, use_cache=False):
            _, error = auth_service.create_user(
                username,
                password,
                role,
                f"v1.6.3.2 UAT {role}",
                "UAT-O-R1",
            )
            if error:
                raise RuntimeError(error)

    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE users SET must_change_password=0, status='active' "
            "WHERE username IN ('admin','uat1632_developer','uat1632_auditor')"
        )
        conn.commit()
    finally:
        conn.close()

    registry.save_connection(
        name="v1.6.3.2 UAT 分布式靶场",
        host="127.0.0.1",
        port=13306,
        username="root",
        password=target_password,
        database="tdsql_test",
        charset="utf8mb4",
        is_default=True,
        is_distributed=True,
        conn_id="uat1632_dist",
        operator="UAT-O-R1",
        description="Isolated local MySQL target; declared distributed for UAT",
    )
    registry.save_connection(
        name="v1.6.3.2 UAT 集中式靶场",
        # Use a distinct host spelling so the production identity unique key
        # can retain both declared architecture fixtures for the same server.
        host="localhost",
        port=13306,
        username="root",
        password=target_password,
        database="tdsql_test",
        charset="utf8mb4",
        is_default=False,
        is_distributed=False,
        conn_id="uat1632_central",
        operator="UAT-O-R1",
        description="Isolated local MySQL target; declared centralized for UAT",
    )

    start = datetime(2026, 9, 4, 9, 0, 0)
    created_ids: dict[str, list[int]] = {}
    for module in SNAPSHOT_MODULES:
        ids: list[int] = []
        for index in range(25):
            captured = start - timedelta(minutes=index)
            severity = "ERROR" if index % 2 == 0 else "WARNING"
            issues = [
                IssueItem(
                    key=fp(module, "shared-issue"),
                    object_name=f"uat_db.{module}_shared",
                    object_type="TABLE",
                    issue_type="UAT_SHARED",
                    severity=severity,
                    title=f"{module} shared issue",
                    detail=f"fixture snapshot {index + 1}",
                    suggestion="UAT fixture only",
                    attrs={"sample": index},
                ),
                IssueItem(
                    key=fp(module, f"unique-{index}"),
                    object_name=f"uat_db.{module}_{index + 1:02d}",
                    object_type="TABLE",
                    issue_type="UAT_UNIQUE",
                    severity="INFO",
                    title=f"{module} unique issue {index + 1:02d}",
                ),
            ]
            label_prefix = "monitordb" if module == "slow_scan" else module
            snapshot_id = create_snapshot(
                module,
                {
                    "biz_ref_id": f"{FIXTURE_PREFIX}-{module}-{index + 1:02d}",
                    "connection_id": "uat1632_dist",
                    "connection_name": "v1.6.3.2 UAT 分布式靶场",
                    "db_name": "uat_db",
                    "scan_label": f"{label_prefix} 跨页样本 {index + 1:02d}",
                    "scan_started_at": (captured - timedelta(seconds=10)).isoformat(),
                    "scan_finished_at": captured.isoformat(),
                    "time_window_start": (captured - timedelta(hours=1)).isoformat(),
                    "time_window_end": captured.isoformat(),
                    "created_by": "uat1632_developer",
                    "rule_set_id": "default",
                    "instance_type": "distributed",
                },
                issues,
                object_total=2,
            )
            ids.append(int(snapshot_id))
        created_ids[module] = ids

    # Two explicit negative fixtures exercise the shared compatibility guards.
    # They are one minute newer than the normal page-1 anchor, so they are easy
    # to select in the browser without depending on hidden row indexes.
    mismatch_issue = IssueItem(
        key=fp("uat1632", "mismatch"),
        object_name="uat_db.compatibility_guard",
        object_type="TABLE",
        issue_type="UAT_MISMATCH",
        severity="WARNING",
        title="compatibility guard fixture",
    )
    create_snapshot(
        "bigtable",
        {
            "biz_ref_id": f"{FIXTURE_PREFIX}-bigtable-instance-mismatch",
            "connection_id": "uat1632_central",
            "connection_name": "v1.6.3.2 UAT 集中式靶场",
            "db_name": "uat_db",
            "scan_label": "bigtable 实例不一致样本",
            "scan_finished_at": (start + timedelta(minutes=1)).isoformat(),
            "created_by": "uat1632_developer",
            "rule_set_id": "default",
            "instance_type": "centralized",
        },
        [mismatch_issue],
        object_total=1,
    )
    create_snapshot(
        "slow_scan",
        {
            "biz_ref_id": f"{FIXTURE_PREFIX}-slow-source-mismatch",
            "connection_id": "uat1632_dist",
            "connection_name": "v1.6.3.2 UAT 分布式靶场",
            "db_name": "uat_db",
            "scan_label": "digest 数据源不一致样本",
            "scan_finished_at": (start + timedelta(minutes=1)).isoformat(),
            "time_window_start": (start - timedelta(hours=1)).isoformat(),
            "time_window_end": start.isoformat(),
            "created_by": "uat1632_developer",
            "rule_set_id": "default",
            "instance_type": "distributed",
        },
        [mismatch_issue],
        object_total=1,
    )

    conn = _get_connection()
    try:
        rules = conn.execute("SELECT COUNT(*) AS c FROM rule_configs").fetchone()["c"]
        new_rules = conn.execute(
            "SELECT rule_id FROM rule_configs WHERE rule_id IN ('R120','R121') "
            "ORDER BY rule_id"
        ).fetchall()
        users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        snapshots = conn.execute(
            "SELECT module, COUNT(*) AS c FROM scan_snapshots GROUP BY module ORDER BY module"
        ).fetchall()
    finally:
        conn.close()

    print(
        {
            "metadata_db": META_DB,
            "rule_configs": rules,
            "new_rules": [row["rule_id"] for row in new_rules],
            "users": users,
            "snapshots": {row["module"]: row["c"] for row in snapshots},
            "page_anchor_ids": {
                module: {"page1": ids[0], "page2": ids[10], "page3": ids[20]}
                for module, ids in created_ids.items()
            },
        }
    )


if __name__ == "__main__":
    main()
