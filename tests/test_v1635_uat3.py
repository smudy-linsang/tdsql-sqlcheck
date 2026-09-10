# -*- coding: utf-8 -*-
"""v1.6.3.5 / D 第三轮 UAT 整改回归锁（D-01 取消任务耗时 / D-02 过期未认领任务回收）。

D-01：取消任务必须写 finished_at，终态耗时稳定不膨胀（不回退当前时间）。
D-02：受理后超期未认领的幽灵任务须回收并释放唯一受理槽。
"""
import json
from datetime import datetime, timezone, timedelta

import pytest

from backend.services.database import ensure_db, _get_connection
from backend.services.metadata_audit_repository import repository as repo
from backend.services import metadata_audit_repository as R


@pytest.fixture(autouse=True)
def _clean():
    ensure_db()
    conn = _get_connection()
    conn.execute("UPDATE metadata_audit_slot SET active_job_id=NULL, accepting=1, "
                 "runner_heartbeat_at=UTC_TIMESTAMP(6) WHERE id=1")
    conn.execute("DELETE FROM metadata_audit_jobs WHERE created_by LIKE 'd3test_%'")
    conn.commit(); conn.close()
    yield
    conn = _get_connection()
    conn.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
    conn.execute("DELETE FROM metadata_audit_jobs WHERE created_by LIKE 'd3test_%'")
    conn.commit(); conn.close()


def _mk(user="d3test_a", key="k1"):
    return repo.create_job(
        created_by=user, request_id="r", idempotency_key=key, request_hash=key,
        connection_id="c1", db_name="d1", request_json="{}",
        execution_context_json='{}', connection_fingerprint="fp",
        report_deadline_seconds=1800)


def _set_created_at(job_id, seconds_ago):
    conn = _get_connection()
    past = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).strftime(
        "%Y-%m-%d %H:%M:%S.%f")
    conn.execute("UPDATE metadata_audit_jobs SET created_at=? WHERE id=?",
                 (past, job_id))
    conn.commit(); conn.close()


# ══ D-01：取消任务写 finished_at、耗时稳定 ══
def test_cancelled_job_writes_finished_at():
    job, _ = _mk(key="kc1")
    repo.claim_next_accepted("runner-x", "tok", R._now())
    assert repo.cancel(job["id"], "tok") is True
    final = repo.get_job(job["id"])
    assert final["state"] == R.STATE_CANCELLED
    assert final["finished_at"] is not None   # D-01 核心：finished_at 落库


def test_cancelled_elapsed_stable():
    """D-01（含 D 复测 NIT 强化）：取消任务耗时不随查询时刻膨胀。

    直接断言不变式：elapsed == finished_at - started_at（而非 now - started_at），
    并跨 1 秒以上两次查询保持一致——避免整数秒在 <1s 间隔内对缺陷态也误判通过。
    """
    import time as _t
    from backend.api.metadata_audit import _job_summary
    from backend.services import metadata_job_process as _jp
    job, _ = _mk(key="kc2")
    repo.claim_next_accepted("runner-x", "tok", R._now())
    repo.cancel(job["id"], "tok")
    j = repo.get_job(job["id"])
    s1 = _job_summary(j)["elapsed_seconds"]
    _t.sleep(1.1)   # 跨过整数秒边界，确保缺陷态（now-started）会变大
    s2 = _job_summary(j)["elapsed_seconds"]
    assert s1 is not None and s1 == s2, "取消任务耗时必须稳定不膨胀"
    # 不变式：elapsed 等于 finished_at-started_at（用 finished_at 而非当前时间）
    st = _jp.parse_utc(j["started_at"]); fin = _jp.parse_utc(j["finished_at"])
    assert st is not None and fin is not None
    assert s1 == int((fin - st).total_seconds())


def test_terminal_without_finished_at_is_none():
    """终态但 finished_at 为空 → elapsed 为 None（不回退当前时间）。"""
    from backend.api.metadata_audit import _job_summary
    job = {"id": "x" * 32, "state": "CANCELLED", "phase": "CLEANUP", "db_name": "d",
           "execution_context_json": "{}", "error_code": "CANCELLED", "error_message": "x",
           "exit_code": None, "progress_json": None,
           "started_at": "2026-09-10T12:00:00", "finished_at": None, "created_at": None,
           "report_id": None, "snapshot_id": None, "cleanup_ok": 1}
    assert _job_summary(job)["elapsed_seconds"] is None


def test_all_terminal_states_have_finished_at():
    """SUCCEEDED/FAILED/CANCELLED 三个终态路径都落 finished_at。"""
    # FAILED
    j1, _ = _mk(key="kf"); repo.claim_next_accepted("r", "t1", R._now())
    repo.fail(j1["id"], "t1", error_code="X", error_message="y")
    assert repo.get_job(j1["id"])["finished_at"] is not None
    repo.release_slot(j1["id"])   # 终态释放槽，供下一个任务
    # CANCELLED
    j2, _ = _mk(user="d3test_b", key="kcx"); repo.claim_next_accepted("r", "t2", R._now())
    repo.cancel(j2["id"], "t2")
    assert repo.get_job(j2["id"])["finished_at"] is not None
    repo.release_slot(j2["id"])
    # SUCCEEDED（PUBLISHED→complete）
    j3, _ = _mk(user="d3test_c", key="kcs"); repo.claim_next_accepted("r", "t3", R._now())
    repo.cas_state(j3["id"], "t3", (R.STATE_RUNNING,), R.STATE_PUBLISHED)
    repo.complete(j3["id"], "t3", exit_code=0, cleanup_ok=True)
    assert repo.get_job(j3["id"])["finished_at"] is not None
    repo.release_slot(j3["id"])


# ══ D-02：过期未认领任务回收 ══
def test_stale_accepted_reclaimed_allows_new_job():
    """31 秒前的 ACCEPTED 幽灵任务被回收（FAILED/START_TIMEOUT），新受理成功（非 409）。"""
    job, _ = _mk(key="ks1")
    _set_created_at(job["id"], 60)   # 60 秒前（超 30s 期限）
    reclaimed = repo.reclaim_stale_accepted(30)
    assert reclaimed == job["id"]
    final = repo.get_job(job["id"])
    assert final["state"] == R.STATE_FAILED and final["error_code"] == "START_TIMEOUT"
    # 槽已释放，新受理成功
    job2, created = _mk(user="d3test_d", key="ks2")
    assert created is True


def test_fresh_accepted_not_reclaimed():
    """5 秒内的新鲜 ACCEPTED 不被回收。"""
    job, _ = _mk(key="kf1")
    _set_created_at(job["id"], 5)
    assert repo.reclaim_stale_accepted(30) is None
    assert repo.get_job(job["id"])["state"] == R.STATE_ACCEPTED


def test_running_job_never_reclaimed():
    """RUNNING 任务不被回收（即使超期）。"""
    job, _ = _mk(key="kr1")
    repo.claim_next_accepted("runner-x", "tok", R._now())   # ACCEPTED→RUNNING
    _set_created_at(job["id"], 120)
    assert repo.reclaim_stale_accepted(30) is None
    assert repo.get_job(job["id"])["state"] == R.STATE_RUNNING


def test_reclaim_skips_when_slot_owned_by_other_job():
    """slot.active_job_id 非该 job 时不动作（防误杀他人任务）。"""
    job, _ = _mk(key="ko1")
    _set_created_at(job["id"], 120)
    # 把 slot 指向另一个不存在的 job（模拟归属不一致）
    conn = _get_connection()
    conn.execute("UPDATE metadata_audit_slot SET active_job_id='otherjob' WHERE id=1")
    conn.commit(); conn.close()
    try:
        assert repo.reclaim_stale_accepted(30) is None
        assert repo.get_job(job["id"])["state"] == R.STATE_ACCEPTED  # 未被误回收
    finally:
        conn = _get_connection()
        conn.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
        conn.commit(); conn.close()


# ══ D-02 接线锁（D 复测 M3/M4：防"方法在但没人调"假绿）══
def _has_real_call(src: str, call_name: str) -> bool:
    """检查源码中存在对 call_name 的**真实调用行**（排除注释行，防"注释里含名字"假绿）。"""
    for line in src.split("\n"):
        s = line.strip()
        if s.startswith("#"):
            continue
        if (call_name + "(") in s:
            return True
    return False


def test_reclaim_is_wired_into_acceptance_path():
    """受理路径必须调用回收（防 M3：方法在但 create_job 没调/被注释）。"""
    import inspect
    src = inspect.getsource(R.MetadataJobRepository.create_job)
    assert _has_real_call(src, "self.reclaim_stale_accepted"), \
        "create_job 中没有对 reclaim_stale_accepted 的真实调用（可能只剩注释）"


def test_reclaim_is_wired_into_runner_tick():
    """runner 每轮必须调用回收（防 M4：runner._tick 没调/被注释）。"""
    import inspect
    from backend.workers.metadata_runner import MetadataRunner
    src = inspect.getsource(MetadataRunner._tick)
    assert _has_real_call(src, "self.repo.reclaim_stale_accepted"), \
        "runner._tick 中没有对 reclaim_stale_accepted 的真实调用（可能只剩注释）"


def test_stale_reclaim_selfheal_end_to_end():
    """行为级接线锁：超期未认领占用槽时，经 create_job 生产受理路径必须自愈——
    新任务受理成功（非 409）且幽灵任务判 FAILED/START_TIMEOUT。
    这是 D-02 的唯一用户可感知入口，锁住"生产接线真的生效"而非只锁方法本身。"""
    # 造 60 秒前受理、从未被认领的幽灵任务并占用唯一槽
    ghost, _ = _mk(key="ghost1")
    _set_created_at(ghost["id"], 60)
    assert repo.get_job(ghost["id"])["state"] == R.STATE_ACCEPTED
    assert repo.slot_state()["active_job_id"] == ghost["id"]
    # 生产受理路径（create_job 内部会先 reclaim）——新任务应受理成功而非 409
    new_job, created = _mk(user="d3test_new", key="newjob1")
    assert created is True, "幽灵任务占槽时新受理应自愈成功（reclaim 释放槽）"
    # 幽灵任务被判 FAILED/START_TIMEOUT
    g = repo.get_job(ghost["id"])
    assert g["state"] == R.STATE_FAILED and g["error_code"] == "START_TIMEOUT"
    assert g["finished_at"] is not None
    # 新任务占用槽
    assert repo.slot_state()["active_job_id"] == new_job["id"]


# ══ D-03：前端 submission 生命周期 + 两动作 + generation 守卫（静态契约）══
from pathlib import Path

_APP_JS = Path(__file__).resolve().parents[1] / "frontend" / "static" / "js" / "app.js"
_INDEX_HTML = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


def test_meta_submission_is_read_not_only_written():
    """D-03：meta_submission 必须有读取方（_loadSubmission），不能只写不读。"""
    js = _APP_JS.read_text(encoding="utf-8")
    assert "_loadSubmission" in js, "缺少 _loadSubmission 读取函数"
    # _loadSubmission 必须被实际调用（不只是定义）
    assert js.count("_loadSubmission()") >= 1, "_loadSubmission 未被调用（submission 仍只写不读）"
    # submission_state 生命周期字段存在
    assert "submission_state" in js


def test_two_distinct_actions_recover_and_rescan():
    """D-03：UI 拆成"重新扫描"（新 key）与"恢复查看"（GET）两个明确动作。"""
    js = _APP_JS.read_text(encoding="utf-8")
    html = _INDEX_HTML.read_text(encoding="utf-8")
    # 两个独立入口
    assert "recoverMetadataJob" in js and "runExtractAndAudit" in js
    assert "recoverMetadataJob" in html and "runExtractAndAudit" in html
    assert "恢复查看" in html, "缺'恢复查看上次任务'入口文案"
    assert "拉取元数据并执行文件审核" in html, "缺'重新扫描'入口"
    # 恢复动作只 GET 不 POST
    import re
    m = re.search(r"const _recoverActiveJob=async\(\)\=>\{(.*?)\n    \};", js, re.DOTALL)
    assert m, "_recoverActiveJob 未定义"
    assert "metadata-jobs`" in m.group(1) or "/metadata-jobs" in m.group(1)
    assert "method:'POST'" not in m.group(1), "_recoverActiveJob 不得 POST（恢复不能新建任务）"


def test_load_metadata_results_has_generation_guard():
    """D-03：loadMetadataResults 必须有 generation 守卫（防旧响应覆盖新视图）。"""
    js = _APP_JS.read_text(encoding="utf-8")
    import re
    m = re.search(r"const loadMetadataResults=async\(.*?\n    \};", js, re.DOTALL)
    assert m, "loadMetadataResults 未定义"
    body = m.group(0)
    assert "_metaPollGen" in body, "loadMetadataResults 缺 _metaPollGen generation 守卫"
    assert "gen!==_metaPollGen" in body, "loadMetadataResults 缺 generation 比对"
