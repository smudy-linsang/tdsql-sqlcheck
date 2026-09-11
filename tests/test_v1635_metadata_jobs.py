# -*- coding: utf-8 -*-
"""v1.6.3.5 / DU-2 — 元数据审核任务 repository 状态机/幂等/退役回归（需本地元数据库）。

覆盖：受理幂等（同 key 同 hash 返回同 job）、busy 拒绝、CAS 状态迁移带 fencing、
发布原子性（PUBLISHING→PUBLISHED+report_id）、终态不可被迟到 attempt 覆盖、
旧 extract-and-audit 路径 410 零副作用。
"""
import json

import pytest

from backend.services.database import ensure_db, _get_connection
from backend.services.metadata_audit_repository import repository as repo
from backend.services.metadata_audit_repository import MetadataJobError
from backend.services import metadata_audit_repository as R


@pytest.fixture(autouse=True)
def _clean_slot():
    """每个用例前后清理 slot 与测试 job，避免跨用例污染（slot 全局唯一 id=1）。"""
    ensure_db()
    conn = _get_connection()
    conn.execute("UPDATE metadata_audit_slot SET active_job_id=NULL, accepting=1, "
                 "runner_heartbeat_at=UTC_TIMESTAMP(6) WHERE id=1")
    conn.execute("DELETE FROM metadata_audit_jobs WHERE created_by LIKE 'utest_%'")
    conn.commit()
    conn.close()
    yield
    conn = _get_connection()
    conn.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
    conn.execute("DELETE FROM metadata_audit_jobs WHERE created_by LIKE 'utest_%'")
    conn.commit()
    conn.close()


def _mk(user="utest_a", key="k1", hash_="h1", cid="conn1", db="db1"):
    return repo.create_job(
        created_by=user, request_id="r1", idempotency_key=key, request_hash=hash_,
        connection_id=cid, db_name=db, request_json="{}",
        execution_context_json='{"context_hash":"x"}', connection_fingerprint="fp",
        report_deadline_seconds=1800)


def test_create_job_idempotent_same_key_same_hash():
    job1, created1 = _mk()
    assert created1 is True and job1["state"] == R.STATE_ACCEPTED
    # 同 key 同 hash → 返回同一 job，不新建
    job2, created2 = _mk()
    assert created2 is False and job2["id"] == job1["id"]


def test_create_job_idempotency_conflict_on_different_hash():
    job1, _ = _mk(key="kc", hash_="hA")
    # 同 key 不同 hash → 409 IDEMPOTENCY_CONFLICT
    with pytest.raises(MetadataJobError) as ei:
        _mk(key="kc", hash_="hB")
    assert ei.value.code == "IDEMPOTENCY_CONFLICT"
    # 但 job1 已占槽，先释放再验冲突判断优先于 busy（同 key 不同 hash 仍 409）
    repo.release_slot(job1["id"])
    with pytest.raises(MetadataJobError) as ei2:
        _mk(key="kc", hash_="hB")
    assert ei2.value.code == "IDEMPOTENCY_CONFLICT"


def test_busy_reject_when_slot_occupied():
    job1, _ = _mk(user="utest_busy1", key="k_busy1")
    # 槽位被 job1 占用，新用户新 key 应 409 METADATA_BUSY
    with pytest.raises(MetadataJobError) as ei:
        _mk(user="utest_busy2", key="k_busy2", hash_="hx")
    assert ei.value.code == "METADATA_BUSY"
    repo.release_slot(job1["id"])
    # 释放后可受理
    job2, created2 = _mk(user="utest_busy2", key="k_busy2", hash_="hx")
    assert created2 is True


def test_claim_and_cas_state_machine():
    job, _ = _mk(user="utest_cas", key="k_cas")
    jid = job["id"]
    token = "tok123"
    # 认领：ACCEPTED → RUNNING
    claimed = repo.claim_next_accepted("runner-x", token, R._now())
    assert claimed and claimed["id"] == jid and claimed["state"] == R.STATE_RUNNING
    # 错误 token 不能迁移状态（fencing）
    ok = repo.cas_state(jid, "WRONG_TOKEN", (R.STATE_RUNNING,), R.STATE_PUBLISHING)
    assert ok is False
    assert repo.get_job(jid)["state"] == R.STATE_RUNNING
    # 正确 token 迁移
    ok = repo.cas_state(jid, token, (R.STATE_RUNNING,), R.STATE_PUBLISHING,
                        phase=R.PHASE_PERSISTING)
    assert ok is True
    assert repo.get_job(jid)["state"] == R.STATE_PUBLISHING


def test_publish_atomic_links_report_id():
    job, _ = _mk(user="utest_pub", key="k_pub")
    jid = job["id"]
    token = "tok_pub"
    repo.claim_next_accepted("runner-x", token, R._now())
    repo.cas_state(jid, token, (R.STATE_RUNNING,), R.STATE_PUBLISHING,
                   phase=R.PHASE_PERSISTING)
    results = [{"sql": "CREATE TABLE t (id INT);", "sql_type": "CREATE",
                "passed": True, "violations": [], "line_number": 1,
                "file_path": "f.sql"}]
    results_json = json.dumps(results, ensure_ascii=False)
    cols = ("extracted_schema", "f.sql", 1, 1, 0, 0, 0, 100.0, results_json,
            "utest_pub", "", None, "", R._now(), "conn1", "db1", None,
            "centralized", "auto", 0, None,
            # v1.6.3.6 / R2-M-05：补 3 个跳过计数（publish 再补 omitted_results 成 25 列）
            None, 0, 0)
    report_id = repo.publish(jid, token, audit_columns_values=cols,
                             results_json=results_json)
    assert report_id > 0
    final = repo.get_job(jid)
    assert final["state"] == R.STATE_PUBLISHED
    assert int(final["report_id"]) == report_id
    # 幂等：重复发布返回同一 report_id，不重复 INSERT
    report_id2 = repo.publish(jid, token, audit_columns_values=cols,
                              results_json=results_json)
    assert report_id2 == report_id


def test_complete_and_terminal_state():
    job, _ = _mk(user="utest_done", key="k_done")
    jid = job["id"]
    token = "tok_done"
    repo.claim_next_accepted("runner-x", token, R._now())
    # 未发布直接 complete 不命中（不在 PUBLISHED）
    assert repo.complete(jid, token, exit_code=0, cleanup_ok=True) is False
    # fail 到终态
    assert repo.fail(jid, token, error_code="WORKER_ERROR", error_message="x") is True
    assert repo.get_job(jid)["state"] == R.STATE_FAILED
    # 终态不可被 forward 迁移命中：complete() 的 from_states 只含 PUBLISHED，不命中 FAILED
    assert repo.complete(jid, token, exit_code=0, cleanup_ok=True) is False
    assert repo.get_job(jid)["state"] == R.STATE_FAILED
    # 旧 attempt_token 不能覆盖终态（fencing）
    assert repo.cas_state(jid, "STALE_TOKEN", (R.STATE_RUNNING,), R.STATE_FAILED) is False
    assert repo.get_job(jid)["state"] == R.STATE_FAILED


def test_retired_extract_and_audit_returns_410():
    """旧路径 410 零副作用：不创建 job、不占槽。"""
    import asyncio
    from backend.api.sql_audit import extract_and_audit

    class _State:
        username = "utest_retire"
        request_id = "req-retire"

    class _Req:
        state = _State()
        headers = {"user-agent": "utest-agent"}

    slot_before = repo.slot_state()
    resp = asyncio.run(extract_and_audit(_Req()))
    assert resp.status_code == 410
    body = json.loads(resp.body.decode("utf-8"))
    assert body["code"] == "ENDPOINT_RETIRED"
    assert isinstance(body["detail"], str)   # detail 必须是字符串（旧 JS 可显示）
    assert body["request_id"] == "req-retire"
    # 零副作用：槽位与 job 表无新增
    slot_after = repo.slot_state()
    assert (slot_before or {}).get("active_job_id") == (slot_after or {}).get("active_job_id")
    conn = _get_connection()
    n = conn.execute(
        "SELECT COUNT(*) c FROM metadata_audit_jobs WHERE created_by='utest_retire'"
    ).fetchone()
    conn.close()
    assert (dict(n) or {}).get("c", 0) == 0
