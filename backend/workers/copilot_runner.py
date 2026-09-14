# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：Copilot 独立执行器（CP-W06，DETAIL §10.4/§11.3/§11.4）。

部署一实例；获取元数据库命名锁 tdsql_copilot_runner（锁连接丢失立即停发新模型
请求）；独立心跳任务每 2 秒更新 runtime；领取时 runtime 锁下选最早 ACCEPTED，
CAS 为 RUNNING 并生成 attempt_token（fencing token），最多
COPILOT_RUNNER_CONCURRENCY 个协程执行；任务租约 20 秒、每 2 秒续约。

启动顺序：命名锁 → B组读验/必要对账 → 模型/开关允许才 accepting=true。
发现 B组故障：accepting=false、不领取、不新出站、尽力停止本地执行；
故障/重启不把旧任务自动重新发送给供应商（旧 attempt 受 epoch 与终态双重拦截）。

停机顺序：runtime.accepting=false → 取消/中断在途任务 → 停止 HTTP 协程与
文本子进程 → 释放命名锁。
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("tdsql.copilot.runner")

_HEARTBEAT_SECONDS = 2.0
_POLL_SECONDS = 1.0
_LEASE_SECONDS = 20
_SCHEMA_RECHECK_SECONDS = 60


def _utcnow6() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class CopilotRunner:
    def __init__(self):
        from backend.services.copilot.policy import Limits
        host = os.uname().nodename if hasattr(os, "uname") else os.getenv(
            "COMPUTERNAME", "win")
        self.runner_id = f"copilot-{host}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.concurrency = Limits.get("COPILOT_RUNNER_CONCURRENCY")
        self.queue_max_seconds = Limits.get("COPILOT_QUEUE_MAX_SECONDS")
        self._stop = asyncio.Event()
        self._lock_conn = None
        self._active_tasks: set[asyncio.Task] = set()
        self._last_schema_check = 0.0

    # ── 命名锁 ───────────────────────────────────────────
    def _acquire_lock(self) -> None:
        from backend.services.database import _get_connection
        from backend.services.copilot.schema import RUNNER_LOCK
        self._lock_conn = _get_connection()
        row = self._lock_conn.execute(
            "SELECT GET_LOCK(?, 30) AS got", (RUNNER_LOCK,)).fetchone()
        if int(dict(row or {}).get("got") or 0) != 1:
            raise SystemExit("无法获取 tdsql_copilot_runner 命名锁（已有 runner 在运行）")

    def _release_lock(self) -> None:
        try:
            if self._lock_conn is not None:
                from backend.services.copilot.schema import RUNNER_LOCK
                self._lock_conn.execute("SELECT RELEASE_LOCK(?)", (RUNNER_LOCK,))
                self._lock_conn.close()
        except Exception:
            pass

    def _check_lock_alive(self) -> bool:
        try:
            row = self._lock_conn.execute("SELECT 1 AS ok").fetchone()
            return bool(row)
        except Exception:
            return False

    # ── 启动：结构验收/对账/准入 ──────────────────────────
    def _startup_gate(self) -> None:
        from backend.services.copilot import schema as schema_mod
        from backend.services.copilot.policy import copilot_enabled, policy_available, Limits
        from backend.services.copilot.crypto import crypto_available
        from backend.services.copilot.repository import RuntimeRepo
        from backend.services.database import _get_connection, ensure_db

        ensure_db()
        problems = Limits.validate()
        if problems:
            raise SystemExit("Copilot 部署参数越界: " + "; ".join(problems))
        conn = _get_connection()
        try:
            ready, reason, st = schema_mod.evaluate_ready(conn)
            if not ready:
                # 尝试空库/故障对账（首次部署也要区分“结构正常未启用”与“结构失败”）
                from backend.services.copilot import recovery
                problems_v = schema_mod.verify_business_schema(conn)
                if problems_v:
                    logger.error("B组结构验收失败: %s", problems_v[:5])
                    schema_mod.mark_unavailable(conn, "startup verify failed")
                    return
                rec = recovery.reconcile_after_recovery(conn)
                if not rec.get("ok"):
                    logger.error("启动对账失败: %s", rec)
                    return
                schema_mod.mark_ready(conn, schema_mod.module_schema_revision())
                ready = True
            RuntimeRepo.set_accepting(False, self.runner_id)
            self._last_schema_check = time.monotonic()
            if not copilot_enabled():
                logger.info("COPILOT_ENABLED=false，runner 保持未受理模式")
                return
            if not crypto_available():
                logger.warning("COPILOT 加密组件不可用，runner 不受理")
                return
            RuntimeRepo.set_accepting(True, self.runner_id)
            logger.info("Copilot runner 进入受理状态 runner_id=%s", self.runner_id)
        finally:
            conn.close()

    # ── 周期结构复验（60秒）────────────────────────────────
    def _periodic_schema_check(self) -> bool:
        from backend.services.copilot import schema as schema_mod
        from backend.services.database import _get_connection
        if time.monotonic() - self._last_schema_check < _SCHEMA_RECHECK_SECONDS:
            return True
        self._last_schema_check = time.monotonic()
        conn = _get_connection()
        try:
            ready, _reason, _st = schema_mod.evaluate_ready(conn)
            if not ready:
                from backend.services.copilot.repository import RuntimeRepo
                RuntimeRepo.set_accepting(False, self.runner_id)
                logger.error("B组结构复验失败，停止受理")
                return False
            return True
        except Exception as e:
            logger.error("B组结构复验异常（失败关闭）: %s", e)
            from backend.services.copilot.repository import RuntimeRepo
            try:
                RuntimeRepo.set_accepting(False, self.runner_id)
            except Exception:
                pass
            return False
        finally:
            conn.close()

    # ── 主循环 ───────────────────────────────────────────
    async def run(self) -> None:
        from backend.services.copilot.repository import RuntimeRepo
        self._acquire_lock()
        try:
            self._startup_gate()
        except SystemExit:
            self._release_lock()
            raise
        except Exception as e:
            logger.error("runner 启动门禁异常: %s", e, exc_info=True)
            self._release_lock()
            raise SystemExit(f"runner 启动门禁失败: {e}")

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError):
                pass

        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            while not self._stop.is_set():
                try:
                    if not self._check_lock_alive():
                        logger.error("命名锁连接丢失，停止受理并退出")
                        self._stop_accepting_safe()
                        break
                    if not self._periodic_schema_check():
                        await asyncio.sleep(_POLL_SECONDS)
                        continue
                    self._reclaim_stale()
                    self._fail_queue_timeouts()
                    await self._claim_and_run()
                except Exception as e:
                    logger.error("runner tick 异常: %s", e, exc_info=True)
                await asyncio.sleep(_POLL_SECONDS)
        finally:
            self._stop_accepting_safe()
            if self._active_tasks:
                await asyncio.wait(self._active_tasks, timeout=20)
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except Exception:
                pass
            self._release_lock()
            logger.info("Copilot runner 已停止")

    def _stop_accepting_safe(self) -> None:
        try:
            from backend.services.copilot.repository import RuntimeRepo
            RuntimeRepo.set_accepting(False, self.runner_id)
        except Exception:
            pass

    async def _heartbeat_loop(self) -> None:
        from backend.services.copilot.repository import RuntimeRepo
        while True:
            try:
                RuntimeRepo.heartbeat(self.runner_id)
            except Exception as e:
                logger.error("心跳失败: %s", e)
            await asyncio.sleep(_HEARTBEAT_SECONDS)

    def _reclaim_stale(self) -> None:
        """租约过期且 runner 失联的任务 → INTERRUPTED（不自动重发模型）。"""
        from backend.services.copilot.repository import SessionRepo, TurnRepo
        from backend.services.database import _get_connection
        conn = _get_connection()
        try:
            for row in TurnRepo.stale_leases(conn, limit=20):
                if row.get("runner_id") == self.runner_id:
                    continue  # 本 runner 在途任务由任务协程自行管理
                if TurnRepo.force_terminal_admin(conn, row["id"], "INTERRUPTED",
                                                 "EXECUTOR_INTERRUPTED"):
                    turn = TurnRepo.get(conn, row["id"])
                    if turn:
                        SessionRepo.release_activity(conn, turn["session_id"],
                                                     turn["id"])
            conn.commit()
        finally:
            conn.close()

    def _fail_queue_timeouts(self) -> None:
        from backend.services.copilot.repository import SessionRepo, TurnRepo
        from backend.services.database import _get_connection
        conn = _get_connection()
        try:
            for row in TurnRepo.queue_timeouts(conn, self.queue_max_seconds):
                if TurnRepo.force_terminal_admin(conn, row["id"], "FAILED",
                                                 "QUEUE_TIMEOUT"):
                    turn = TurnRepo.get(conn, row["id"])
                    if turn:
                        SessionRepo.release_activity(conn, turn["session_id"],
                                                     turn["id"])
            conn.commit()
        finally:
            conn.close()

    async def _claim_and_run(self) -> None:
        from backend.services.copilot.policy import copilot_enabled, Limits
        from backend.services.copilot.repository import RuntimeRepo, TurnRepo
        from backend.services.database import _get_connection
        if not copilot_enabled():
            return
        if len(self._active_tasks) >= self.concurrency:
            return
        conn = _get_connection()
        try:
            rt = RuntimeRepo.get(conn)
            if not rt or not int(rt.get("accepting") or 0):
                return
            # 领取前再鉴权：模块结构 READY + 代次一致
            from backend.services.copilot import schema as schema_mod
            ready, _r, _st = schema_mod.evaluate_ready(conn)
            if not ready:
                return
            token = uuid.uuid4().hex
            turn = TurnRepo.claim_next(conn, self.runner_id, token, _LEASE_SECONDS)
            if not turn:
                conn.commit()
                return
            conn.commit()
        finally:
            conn.close()
        task = asyncio.create_task(self._execute_turn(turn["id"], token))
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    async def _execute_turn(self, turn_id: str, token: str) -> None:
        from backend.services.copilot.workflow import TurnExecutor
        from backend.services.copilot import crypto as crypto_mod
        from backend.services.copilot.repository import (
            PreviewRepo, SessionRepo, TurnRepo,
        )
        from backend.services.database import _get_connection
        conn = _get_connection()
        try:
            turn = TurnRepo.get_for_update(conn, turn_id)
            if not turn or turn.get("attempt_token") != token:
                conn.rollback()
                return
            preview = PreviewRepo.get(conn, turn["preview_id"])
            session = SessionRepo.get(conn, turn["session_id"])
            if not preview or not session:
                conn.rollback()
                TurnRepo.publish_terminal(conn, turn_id, token, "FAILED",
                                          error_code="EVIDENCE_UNAVAILABLE",
                                          error_message="预览或会话不存在")
                conn.commit()
                return
            # 代次冻结校验：受理时代次须与当前 READY 代次一致
            from backend.services.copilot import schema as schema_mod
            ready, _r, st = schema_mod.evaluate_ready(conn)
            if not ready or int(st["module_schema_epoch"]) != \
                    int(turn.get("module_schema_epoch") or 0):
                conn.rollback()
                TurnRepo.publish_terminal(conn, turn_id, token, "FAILED",
                                          error_code="CONTEXT_CHANGED",
                                          error_message="模块结构代次已变化")
                conn.commit()
                return
            conn.commit()
            try:
                keyring = crypto_mod.load_keyring()
            except Exception:
                keyring = None
            executor = TurnExecutor(
                conn=None, turn=turn, preview=preview, session=session,
                identity_like={"username": turn["owner"],
                               "subject_id": turn["owner_subject_id"]},
                crypto_keyring=keyring)
            # 执行器需要自有连接（与领取连接分离）
            executor.conn = _get_connection()
            try:
                await asyncio.to_thread(self._run_executor_sync, executor)
            finally:
                try:
                    executor.conn.close()
                except Exception:
                    pass
        finally:
            conn.close()

    def _run_executor_sync(self, executor) -> None:
        executor.run()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    runner = CopilotRunner()
    asyncio.run(runner.run())


if __name__ == "__main__":
    main()
