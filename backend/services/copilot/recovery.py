# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：B组故障恢复对账（CP-W02，DETAIL §11.6）。

仅 runner 或维护入口在持有 tdsql_copilot_runner 命名锁时执行；B组正式结构、
全部台账及本版 hash 先验收通过，runtime 保持 UNAVAILABLE/accepting=false。
同一故障 epoch 下幂等恢复，不用一次健康 GET 触发修复/重发。维护上下文是
固定 SYSTEM，只访问 copilot_* 及迁移台账；不调用用户 API、源业务读取或目标库。

五步对账：
  1. 按 subject_id 核对授权（REVOKED/不存在 → 吊销 grant），不重复加 revision；
  2. 遗留活动态轮次 → INTERRUPTED（EXECUTOR_INTERRUPTED），不自动推理重跑；
  3. 预算一次性结算（CAS 活动→终态同事务为一次性标记，重复恢复不二次收费）；
  4. 会话活动指针/预览消费/孤儿引用/日预算/空间对账（小批 ≤100 行）；
  5. 全部通过后由调用方 mark_ready（CAS 校验 epoch/hash 未变）。
"""
from __future__ import annotations

import logging
from typing import Any

from backend.services.copilot.repository import (
    AuditRepo, SessionRepo, TurnRepo, utcnow6,
)

logger = logging.getLogger("tdsql.copilot.recovery")

_BATCH = 100


def reconcile_after_recovery(conn, dry_run: bool = False) -> dict[str, Any]:
    """B组故障恢复对账（调用方须已持命名锁并完成结构验收）。

    返回 {"ok": bool, "grants_revoked": n, "turns_interrupted": n,
          "sessions_released": n, "problems": [...]}
    任一步失败 ok=False 且保持 UNAVAILABLE；可在同一 epoch 下重复执行（幂等）。
    """
    result: dict[str, Any] = {"ok": False, "grants_revoked": 0,
                              "turns_interrupted": 0, "sessions_released": 0,
                              "problems": []}
    try:
        # ── 1. 死授权清理：REVOKED/不存在 subject 的 grant 置 REVOKED ──
        rows = conn.execute(
            "SELECT g.subject_id, g.connection_id FROM copilot_instance_grants g "
            "LEFT JOIN copilot_subjects s ON g.subject_id = s.subject_id "
            "AND s.state = 'ACTIVE' "
            "WHERE s.subject_id IS NULL AND g.approval_state != 'REVOKED' "
            "ORDER BY g.subject_id LIMIT ?", (_BATCH,)).fetchall()
        for r in rows:
            r = dict(r)
            if not dry_run:
                cur = conn.execute(
                    "UPDATE copilot_instance_grants SET approval_state = 'REVOKED', "
                    "enabled = 0, allow_schema_identifiers = 0, "
                    "revision = revision + 1, updated_at = UTC_TIMESTAMP(6) "
                    "WHERE subject_id = ? AND connection_id = ? "
                    "AND approval_state != 'REVOKED'",
                    (r["subject_id"], r["connection_id"]))
                if cur.rowcount == 1:
                    result["grants_revoked"] += 1
                    AuditRepo.record(
                        conn, "GRANT_CHANGE", "system", None,
                        "copilot_instance_grants", r["connection_id"], "RECONCILE_REVOKE",
                        detail={"reason": "subject_revoked_or_missing"})

        # ── 2. 遗留活动态轮次 → INTERRUPTED（禁止自动推理重跑）──
        rows = conn.execute(
            "SELECT id, session_id, state, owner_subject_id, created_at, "
            "reserved_tokens FROM copilot_turns WHERE state IN "
            "('ACCEPTED','RUNNING','CANCEL_REQUESTED') ORDER BY id LIMIT ?",
            (_BATCH,)).fetchall()
        for r in rows:
            r = dict(r)
            if dry_run:
                result["turns_interrupted"] += 1
                continue
            # 活动→终态 CAS 事务为一次性结算标记（重复恢复不二次收费/释放）
            cur = conn.execute(
                "UPDATE copilot_turns SET state = 'INTERRUPTED', phase = 'DONE', "
                "error_code = 'EXECUTOR_INTERRUPTED', finished_at = UTC_TIMESTAMP(6), "
                "lease_until = NULL, updated_at = UTC_TIMESTAMP(6) "
                "WHERE id = ? AND state IN ('ACCEPTED','RUNNING','CANCEL_REQUESTED')",
                (r["id"],))
            if cur.rowcount != 1:
                continue
            result["turns_interrupted"] += 1
            # 释放会话活动指针
            if r.get("session_id"):
                SessionRepo.release_activity(conn, r["session_id"], r["id"])
            # ── 3. 预算保守结算：是否发送/usage 未知按原预留上界记账，不记 0 ──
            reserved = int(r.get("reserved_tokens") or 0)
            if reserved > 0:
                day = str(r.get("created_at") or "")[:10] or utcnow6()[:10]
                for principal in (f"user:{r['owner_subject_id']}", "global"):
                    conn.execute(
                        "UPDATE copilot_daily_budgets SET "
                        "reserved_tokens = GREATEST(0, reserved_tokens - ?), "
                        "charged_tokens = charged_tokens + ?, "
                        "updated_at = UTC_TIMESTAMP(6) "
                        "WHERE principal = ? AND day_utc = ?",
                        (reserved, reserved, principal, day))
                conn.execute(
                    "UPDATE copilot_turns SET charged_tokens = ? WHERE id = ?",
                    (reserved, r["id"]))
            AuditRepo.record(
                conn, "PUBLISH", "system", None, "copilot_turns", r["id"],
                "RECONCILE_INTERRUPT",
                detail={"from_state": r.get("state"), "reason": "module_recovery"},
                session_id=r.get("session_id"), turn_id=r["id"])
            conn.commit()

        # ── 4. 孤儿活动指针核对（会话指向非活动 turn 的指针清理）──
        rows = conn.execute(
            "SELECT s.id AS sid, s.active_turn_id AS tid FROM copilot_sessions s "
            "LEFT JOIN copilot_turns t ON s.active_turn_id = t.id AND t.state IN "
            "('ACCEPTED','RUNNING','CANCEL_REQUESTED') "
            "WHERE s.active_turn_id IS NOT NULL AND t.id IS NULL LIMIT ?",
            (_BATCH,)).fetchall()
        for r in rows:
            r = dict(r)
            if not dry_run:
                conn.execute(
                    "UPDATE copilot_sessions SET active_turn_id = NULL, "
                    "revision = revision + 1, updated_at = UTC_TIMESTAMP(6) "
                    "WHERE id = ? AND active_turn_id = ?",
                    (r["sid"], r["tid"]))
            result["sessions_released"] += 1

        # 负预算/空间防护：不为平账粗暴置 0，发现问题保持 UNAVAILABLE
        neg = conn.execute(
            "SELECT COUNT(*) AS c FROM copilot_daily_budgets "
            "WHERE reserved_tokens < 0 OR charged_tokens < 0").fetchone()
        if neg and int(dict(neg).get("c", 0)) > 0:
            result["problems"].append("日预算出现负值，需人工核查")

        if not dry_run:
            conn.commit()
        result["ok"] = not result["problems"]
    except Exception as e:
        logger.error("B组恢复对账失败: %s", e, exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        result["problems"].append(f"对账异常: {type(e).__name__}")
        result["ok"] = False
    return result
