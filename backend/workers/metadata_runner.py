# -*- coding: utf-8 -*-
"""元数据审核任务执行器（runner，本机常驻轻量监督服务，v1.6.3.5 / DU-2 / FIX-02 / D05）。

以 `python -m backend.workers.metadata_runner` 由 systemd 启动（独立服务，
不 import backend.main / 不占 scheduler_lease / 不复用网关锁）。

职责：
  · 启动硬校验：参数范围 + 25% MemTotal 预检（§5.4），不通过 fail-closed 拒绝启动；
  · 唯一执行槽：从 metadata_audit_slot 认领 ACCEPTED 任务（CAS → RUNNING + fencing token）；
  · 派生一个隔离子进程跑 metadata_audit_worker，监督循环每秒复核 RSS/取消/超时，
    TERM→5s→KILL→reap；RSS 超限 → RESOURCE_LIMIT；
  · 子进程失败时把 stderr 尾部（脱敏）记入日志与任务 error_message（M-01）；
  · 回收确认后才置终态并释放槽位；无法确认进程退出则 RECOVERY_REQUIRED 继续占槽。
"""

import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone

from backend.services import metadata_job_process as jp

logger = logging.getLogger("tdsql.metadata_runner")

_HEARTBEAT_SECONDS = 2.0
_POLL_SECONDS = 1.0


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _sanitize_log(text: str, limit: int = 8192) -> str:
    """stderr 尾部脱敏 + 限长（去 CRLF，防日志注入；不落全量输出）。"""
    s = (text or "").replace("\r", " ")[:limit]
    return s


class MetadataRunner:
    """单实例元数据审核执行器监督进程。"""

    def __init__(self):
        from backend.services.metadata_audit_repository import repository
        self.repo = repository
        host = os.uname().nodename if hasattr(os, "uname") else os.getenv("COMPUTERNAME", "win")
        self.runner_id = f"{host}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.job_timeout = jp.MetadataLimits.JOB_TIMEOUT
        # 启动硬校验：参数范围 + 25% MemTotal 预检（§5.4），不通过 fail-closed
        ok, reasons = jp.precheck_runner_start()
        if not ok:
            for r in reasons:
                logger.error("runner 启动预检失败: %s", r)
            raise SystemExit("metadata_runner 资源预检失败，拒绝启动: " + "; ".join(reasons))

    def _log(self, msg, **kw):
        kv = " ".join(f"{k}={v}" for k, v in kw.items())
        logger.info("%s %s", msg, kv)

    def run_forever(self):
        self._log("metadata_runner 启动", runner_id=self.runner_id)
        self.repo.slot_heartbeat(self.runner_id)
        while True:
            try:
                self.repo.slot_heartbeat(self.runner_id)
                self._tick()
            except Exception as e:
                logger.error("runner tick 异常: %s", e, exc_info=True)
            time.sleep(_POLL_SECONDS)

    def _tick(self):
        # D-02 + FIXREQ-v1.6.3.6-01 §2.1：每轮先收敛"无主悬挂"任务（ACCEPTED 幽灵 +
        # PUBLISHING/PUBLISHED 悬挂），释放唯一槽
        try:
            self.repo.reclaim_stale_unowned(jp.MetadataLimits.START_TIMEOUT,
                                            jp.MetadataLimits.PUBLISH_TIMEOUT)
        except Exception as e:
            logger.error("回收无主悬挂任务异常: %s", e)
        token = uuid.uuid4().hex
        job = self.repo.claim_next_accepted(self.runner_id, token, _utcnow())
        if not job:
            return
        self._log("认领任务", job_id=job["id"], attempt=token)
        self._run_job(job, token)

    def _run_job(self, job: dict, token: str):
        from backend.services import metadata_audit_repository as R
        job_id = job["id"]
        cmd = [sys.executable, "-m", "backend.workers.metadata_audit_worker",
               "--job-id", job_id, "--attempt-token", token]
        env = dict(os.environ)
        from pathlib import Path
        repo_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")

        self._log("启动子进程", job_id=job_id, pid_budget_mib=jp.MetadataLimits.CHILD_RSS_LIMIT_MIB)
        started = time.monotonic()
        # 任务执行期间持续上报 runner 心跳（R2-01：否则长任务期间 runner 看似离线）
        import threading
        hb_stop = threading.Event()

        def _hb_loop():
            while not hb_stop.is_set():
                try:
                    self.repo.slot_heartbeat(self.runner_id)
                except Exception:
                    pass
                hb_stop.wait(2.0)
        hb_thread = threading.Thread(target=_hb_loop, daemon=True)
        hb_thread.start()
        try:
            # 受管子进程：RSS 采样 + 取消意图复核 + 总时限
            res = jp.run_metadata_worker(
                cmd, timeout=self.job_timeout, env=env, cwd=repo_root,
                cancel_check=lambda: self.repo.is_cancel_requested(job_id))
        finally:
            hb_stop.set()
            hb_thread.join(timeout=3)

        final = self.repo.get_job(job_id)
        elapsed = int(time.monotonic() - started)
        self._log("子进程结束", job_id=job_id, rc=res.returncode, elapsed_s=elapsed,
                  cleanup_ok=res.cleanup_ok, timed_out=res.timed_out,
                  cancelled=res.cancelled, rss_exceeded=res.rss_exceeded)

        err_tail = _sanitize_log(res.stderr_tail)
        if res.rss_exceeded:
            self.repo.fail(job_id, token, error_code="RESOURCE_LIMIT",
                           error_message=f"子进程内存超过保护上限 "
                                         f"{jp.MetadataLimits.CHILD_RSS_LIMIT_MIB} MiB，已终止回收。")
        elif res.cancelled:
            # D-01：取消终态写 finished_at（与 fail/complete 同口径），耗时不膨胀
            self.repo.cancel(job_id, token, cleanup_ok=res.cleanup_ok)
        elif res.timed_out:
            self.repo.fail(job_id, token, error_code="JOB_TIMEOUT",
                           error_message=f"任务超过保护预算 {self.job_timeout}s，已终止回收。")
        elif final and final.get("state") == R.STATE_PUBLISHED:
            ok = self.repo.complete(job_id, token, exit_code=res.returncode or 0,
                                    cleanup_ok=res.cleanup_ok)
            if not ok:   # FIXREQ-v1.6.3.6-01 §2.2：CAS 未命中不得静默继续放槽
                logger.error("complete() 未命中，任务仍未收敛 job=%s state=%s",
                             job_id, (self.repo.get_job(job_id) or {}).get("state"))
                self._mark_recovery(job_id, token)
                return   # RECOVERY_REQUIRED 不释放槽位
        elif final and final.get("state") == R.STATE_FAILED:
            pass   # worker 已自置 FAILED（业务失败原因已落库）
        elif res.returncode not in (0, None):
            # child 异常退出且未发布 → 记录 stderr 尾部供诊断（M-01）
            detail = f"子进程退出码 {res.returncode}，未发布结果。"
            if err_tail:
                detail += f" stderr 尾部: {err_tail[-400:]}"
            logger.error("元数据任务 child 异常退出 job=%s rc=%s stderr_tail=%s",
                         job_id, res.returncode, err_tail[-800:])
            self.repo.fail(job_id, token, error_code="CHILD_EXITED",
                           error_message=detail)
        # 更新 exit_code
        if res.returncode is not None:
            try:
                self.repo.cas_state(job_id, token, tuple(R.TERMINAL_STATES) + tuple(R.ACTIVE_STATES),
                                    self.repo.get_job(job_id)["state"],
                                    extra={"exit_code": int(res.returncode),
                                           "cleanup_ok": 1 if res.cleanup_ok else 0})
            except Exception:
                pass
        if not res.cleanup_ok and (final and final.get("state") in R.ACTIVE_STATES):
            self._mark_recovery(job_id, token)
            return   # RECOVERY_REQUIRED 不释放槽位
        # FIXREQ-v1.6.3.6-01 §2.2：释放槽位前通用护栏——任务非终态则转 RECOVERY_REQUIRED、
        # 不放槽，杜绝"槽已释放、任务非终态"的不一致（UAT36-01/D-04 的产生源）。
        cur = self.repo.get_job(job_id) or {}
        if cur.get("state") not in R.TERMINAL_STATES:
            logger.error("任务非终态却准备释放槽位，转入 RECOVERY_REQUIRED job=%s state=%s",
                         job_id, cur.get("state"))
            self._mark_recovery(job_id, token)
            return
        self.repo.release_slot(job_id)
        self._log("释放槽位", job_id=job_id)

    def _mark_recovery(self, job_id: str, token: str):
        from backend.services import metadata_audit_repository as R
        self.repo.cas_state(job_id, token,
                            (R.STATE_RUNNING, R.STATE_PUBLISHING, R.STATE_PUBLISHED, R.STATE_STOPPING),
                            R.STATE_RECOVERY_REQUIRED, phase=R.PHASE_CLEANUP,
                            error_code="RECOVERY_REQUIRED",
                            error_message="后台回收或成果核对未完成，已阻止新任务，需要运维处理。")


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    MetadataRunner().run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
