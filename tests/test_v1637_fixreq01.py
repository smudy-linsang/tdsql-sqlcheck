# -*- coding: utf-8 -*-
"""FIXREQ-v1.6.3.6-01（UAT36-01 + D-04）终态收敛缺口与前端活动判定整改回归锁 L1-L10。

施工规约（§4）：每条锁先注入缺陷确认变红、再恢复确认变绿（变异自证见
scratch/mutation_selfcheck_v1637.py）。约束：不新增表、只新增 API 字段、不动其它模块。

覆盖：
  L1  PUBLISHED 悬挂(有 report) → SUCCEEDED + finished_at + 释放槽
  L2  PUBLISHING 悬挂(无 report) → FAILED/PERSIST_TIMEOUT
  L3  ACCEPTED + 槽空(D-04) → 仍收敛(不因槽空跳过)
  L4  槽指向他人时 orphan 不动(不改状态/不放槽)
  L5  PUBLISHED 未超时 → 不动作
  L6  complete() 未命中 → 不释放槽 + RECOVERY_REQUIRED(双护栏)
  L7  _job_summary 暴露 slot_owned
  L8  前端契约：活动判定看 state+slot_owned，两按钮不共用禁用源
  L9  _job_summary 暴露 stale_running(RUNNING+心跳超时)
  L10 §2.1b 决策锁：RUNNING 即使超期极久也不自动收敛
"""
import re
from datetime import datetime, timezone, timedelta

import pytest

from backend.services.database import ensure_db, _get_connection
from backend.services.metadata_audit_repository import repository as repo
from backend.services import metadata_audit_repository as R

_USER = "d3v1637"


@pytest.fixture(autouse=True)
def _clean():
    ensure_db()
    conn = _get_connection()
    conn.execute("UPDATE metadata_audit_slot SET active_job_id=NULL, accepting=1, "
                 "runner_heartbeat_at=UTC_TIMESTAMP(6) WHERE id=1")
    conn.execute("DELETE FROM metadata_audit_jobs WHERE created_by LIKE ?",
                 (f"{_USER}%",))
    conn.commit(); conn.close()
    yield
    conn = _get_connection()
    conn.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
    conn.execute("DELETE FROM metadata_audit_jobs WHERE created_by LIKE ?",
                 (f"{_USER}%",))
    conn.commit(); conn.close()


def _past(seconds_ago):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).strftime(
        "%Y-%m-%d %H:%M:%S.%f")


def _mk(key, user=_USER):
    job, _ = repo.create_job(
        created_by=user, request_id="r", idempotency_key=key, request_hash=key,
        connection_id="c1", db_name="d1", request_json="{}",
        execution_context_json="{}", connection_fingerprint="fp",
        report_deadline_seconds=1800)
    return job


def _claim(job_id, tok="tok"):
    """ACCEPTED → RUNNING（占用槽 + 设 attempt_token）。"""
    repo.claim_next_accepted("runner-x", tok, R._now())
    return tok


def _to_state(job_id, tok, state, from_state=R.STATE_RUNNING):
    repo.cas_state(job_id, tok, (from_state,), state)


def _set(job_id, created_ago=None, updated_ago=None, hb_ago=None, report_id="__none__"):
    conn = _get_connection()
    if created_ago is not None:
        conn.execute("UPDATE metadata_audit_jobs SET created_at=? WHERE id=?",
                     (_past(created_ago), job_id))
    if updated_ago is not None:
        conn.execute("UPDATE metadata_audit_jobs SET updated_at=? WHERE id=?",
                     (_past(updated_ago), job_id))
    if hb_ago is not None:
        conn.execute("UPDATE metadata_audit_jobs SET heartbeat_at=? WHERE id=?",
                     (_past(hb_ago), job_id))
    if report_id != "__none__":
        conn.execute("UPDATE metadata_audit_jobs SET report_id=? WHERE id=?",
                     (report_id, job_id))
    conn.commit(); conn.close()


def _set_slot(active_job_id):
    conn = _get_connection()
    conn.execute("UPDATE metadata_audit_slot SET active_job_id=? WHERE id=1",
                 (active_job_id,))
    conn.commit(); conn.close()


def _job_dict(state="RUNNING", hb=None, jid="j1"):
    return {"id": jid, "state": state, "phase": "AUDITING", "db_name": "d",
            "execution_context_json": "{}", "progress_json": None,
            "started_at": None, "finished_at": None, "created_at": None,
            "report_id": None, "snapshot_id": None, "cleanup_ok": 1,
            "error_code": None, "error_message": None, "exit_code": None,
            "heartbeat_at": hb}


# ══ L1：PUBLISHED 悬挂(有 report) → SUCCEEDED + finished_at + 释放槽 ══
def test_reclaim_published_orphan_converges():
    job = _mk(key="l1"); tok = _claim(job["id"])
    _to_state(job["id"], tok, R.STATE_PUBLISHED)
    _set(job["id"], report_id=999, updated_ago=700)   # 有成果 + 超 600s
    r = repo.reclaim_stale_unowned(30, 600)
    assert job["id"] in r["published"]
    final = repo.get_job(job["id"])
    assert final["state"] == R.STATE_SUCCEEDED
    assert final["finished_at"] is not None
    assert repo.slot_state()["active_job_id"] is None   # 槽指向它 → 释放


# ══ L2：PUBLISHING 悬挂(无 report) → FAILED/PERSIST_TIMEOUT ══
def test_reclaim_publishing_without_report_fails():
    job = _mk(key="l2"); tok = _claim(job["id"])
    _to_state(job["id"], tok, R.STATE_PUBLISHING)
    _set(job["id"], updated_ago=700)   # report_id 为空 + 超时
    r = repo.reclaim_stale_unowned(30, 600)
    assert job["id"] in r["published"]
    final = repo.get_job(job["id"])
    assert final["state"] == R.STATE_FAILED
    assert final["error_code"] == "PERSIST_TIMEOUT"


# ══ L3：ACCEPTED + 槽空(D-04) → 仍收敛(不因槽空跳过) ══
def test_reclaim_accepted_unowned_still_works():
    job = _mk(key="l3")            # ACCEPTED，create_job 占槽
    _set(job["id"], created_ago=60)
    _set_slot(None)                # 槽空（D-04 幽灵形态）
    r = repo.reclaim_stale_unowned(30, 600)
    assert job["id"] in r["accepted"]
    assert repo.get_job(job["id"])["state"] == R.STATE_FAILED


# ══ L4：槽指向他人时 orphan 不动(不改状态/不放槽) ══
def test_reclaim_does_not_touch_slot_owned_by_other():
    job = _mk(key="l4")            # ACCEPTED
    _set(job["id"], created_ago=60)   # 超时
    _set_slot("other_job_owner")   # 槽指向他人 → job 是无主 orphan 但槽非空
    r = repo.reclaim_stale_unowned(30, 600)
    # 槽被他人占用：ACCEPTED orphan 不收敛（不抢槽、不改状态）
    assert job["id"] not in r["accepted"]
    assert repo.get_job(job["id"])["state"] == R.STATE_ACCEPTED
    assert repo.slot_state()["active_job_id"] == "other_job_owner"   # 槽未被动


# ══ L5：PUBLISHED 未超时 → 不动作 ══
def test_reclaim_skips_fresh_published():
    job = _mk(key="l5"); tok = _claim(job["id"])
    _to_state(job["id"], tok, R.STATE_PUBLISHED)
    _set(job["id"], report_id=999, updated_ago=5)   # 新鲜（5s < 600s）
    r = repo.reclaim_stale_unowned(30, 600)
    assert job["id"] not in r["published"]
    assert repo.get_job(job["id"])["state"] == R.STATE_PUBLISHED


# ══ L6：complete() 未命中 → 不释放槽 + RECOVERY_REQUIRED（§2.2 双护栏）══
def test_complete_miss_does_not_release_slot(monkeypatch):
    from backend.workers import metadata_runner as MR
    from backend.services import metadata_job_process as jp
    runner = MR.MetadataRunner()
    calls = {"release": 0, "recovery": 0}

    class _Res:
        returncode = 0; cleanup_ok = True; timed_out = False
        cancelled = False; rss_exceeded = False; stderr_tail = ""

    monkeypatch.setattr(jp, "run_metadata_worker", lambda *a, **k: _Res())
    monkeypatch.setattr(runner.repo, "get_job",
                        lambda jid: {"id": jid, "state": R.STATE_PUBLISHED})
    monkeypatch.setattr(runner.repo, "complete", lambda *a, **k: False)   # CAS 未命中
    monkeypatch.setattr(runner.repo, "slot_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(runner.repo, "release_slot",
                        lambda jid: calls.__setitem__("release", calls["release"] + 1))
    monkeypatch.setattr(runner, "_mark_recovery",
                        lambda jid, tok: calls.__setitem__("recovery", calls["recovery"] + 1))
    monkeypatch.setattr(runner, "_log", lambda *a, **k: None)
    runner._run_job({"id": "j1"}, "tok")
    assert calls["recovery"] >= 1, "complete() 未命中必须转 RECOVERY_REQUIRED"
    assert calls["release"] == 0, "complete() 未命中不得释放槽（防静默放槽）"


# ══ L7：_job_summary 暴露 slot_owned ══
def test_job_summary_exposes_slot_owned():
    from backend.api.metadata_audit import _job_summary
    j = _job_dict(state="RUNNING", hb=R._now())
    assert _job_summary(j, "j1")["slot_owned"] is True       # 槽指向它
    assert _job_summary(j, "other")["slot_owned"] is False   # 槽指向他人
    assert _job_summary(j, None)["slot_owned"] is False      # 槽空


# ══ L8：前端契约锁——活动判定看 state+slot_owned，两按钮不共用禁用源 ══
def test_recover_treats_unowned_as_inactive():
    with open("frontend/static/js/app.js", encoding="utf-8") as f:
        app = f.read()
    with open("frontend/index.html", encoding="utf-8") as f:
        html = f.read()
    # 活动判定函数体必须同时引用 state 与 slot_owned（识别真实逻辑，非子串包含）
    m = re.search(r"_isReallyActive\s*=\s*\(d\)\s*=>\s*([^\n;]+)", app)
    assert m, "未找到 _isReallyActive 活动判定函数"
    body = m.group(1)
    assert "d.state" in body and "slot_owned" in body, \
        f"活动判定须同时看 state 与 slot_owned：{body}"
    assert "stale_running" in body, "活动判定须排除 stale_running（§2.1b）"
    # 恢复按钮不得再绑 extractAuditing（两按钮不共用同一禁用源）
    btn = re.search(r':disabled="([^"]*)"[^>]*@click="recoverMetadataJob"', html)
    assert btn, "未找到恢复按钮"
    assert btn.group(1) != "extractAuditing", \
        "恢复按钮不得与拉取按钮共用 extractAuditing 禁用源（无逃生出口）"


# ══ L9：_job_summary 暴露 stale_running（RUNNING + 心跳超 JOB_TIMEOUT）══
def test_job_summary_exposes_stale_running():
    from backend.api.metadata_audit import _job_summary
    stale_hb = _past(99999)      # 远超 JOB_TIMEOUT(1800)
    fresh_hb = R._now()
    # RUNNING + 心跳超时 → True
    assert _job_summary(_job_dict("RUNNING", stale_hb), "j1")["stale_running"] is True
    # RUNNING + 心跳新鲜 → False
    assert _job_summary(_job_dict("RUNNING", fresh_hb), "j1")["stale_running"] is False
    # RUNNING + 无心跳 → True（fail-closed）
    assert _job_summary(_job_dict("RUNNING", None), "j1")["stale_running"] is True
    # 非 RUNNING（即便心跳超时）→ False
    assert _job_summary(_job_dict("PUBLISHED", stale_hb), "j1")["stale_running"] is False


# ══ L10：§2.1b 决策锁——RUNNING 即使超期极久也不自动收敛 ══
def test_reclaim_never_touches_running():
    job = _mk(key="l10"); tok = _claim(job["id"])   # ACCEPTED → RUNNING，槽指向它
    _set(job["id"], created_ago=99999, updated_ago=99999, hb_ago=99999)   # 全部极久
    r = repo.reclaim_stale_unowned(30, 600)
    assert job["id"] not in r["accepted"] and job["id"] not in r["published"]
    assert repo.get_job(job["id"])["state"] == R.STATE_RUNNING   # 状态不变
    assert repo.slot_state()["active_job_id"] == job["id"]       # 槽不动
