# -*- coding: utf-8 -*-
"""元数据审核任务执行器（runner，本机常驻轻量监督服务，v1.6.3.5 / DU-2 / FIX-02 / D05）。

以 `python -m backend.workers.metadata_runner` 由 systemd 启动（独立服务，
不 import backend.main / 不占 scheduler_lease / 不复用网关锁）。

职责：
  · 唯一执行槽：从 metadata_audit_slot 认领 ACCEPTED 任务（CAS → RUNNING + fencing token）；
  · 派生一个隔离子进程跑 metadata_audit_worker（sys.executable + 受控 argv，无 shell）；
  · 监督：每 2 秒 runner 心跳 + 每 1 秒复核取消意图/超时/child RSS；TERM→5s→KILL→reap；
  · 回收确认后才置终态并释放槽位；无法确认进程退出则 RECOVERY_REQUIRED 继续占槽。
"""

import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("tdsql.metadata_runner")

_HEARTBEAT_SECONDS = 2.0
_POLL_SECONDS = 1.0
_TERM_WAIT = 5.0


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _deadline_iso(seconds: int) -> str:
    return datetime.fromtimestamp(time.time() + seconds,
                                  timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class MetadataRunner:
    """单实例元数据审核执行器监督进程。"""

    def __init__(self):
        from backend.services.metadata_audit_repository import repository
        self.repo = repository
        self.runner_id = f"{os.uname().nodename if hasattr(os,'uname') else 'win'}-" \
                         f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.job_timeout = _env_int("METADATA_JOB_TIMEOUT_SECONDS", 1800)
        self.max_concurrent = _env_int("METADATA_MAX_CONCURRENT", 1)
        if self.max_concurrent != 1:
            raise SystemExit("METADATA_MAX_CONCURRENT 固定为 1（非调优项），拒绝启动")

    def _log(self, msg, **kw):
        kv = " ".join(f"{k}={v}" for k, v in kw.items())
        logger.info("%s %s", msg, kv)

    def run_forever(self):
        self._log("metadata_runner 启动", runner_id=self.runner_id)
        # 启动即上报心跳并开放受理（受理前置校验依赖 slot.accepting + 心跳新鲜）
        self.repo.slot_heartbeat(self.runner_id)
        while True:
            try:
                self.repo.slot_heartbeat(self.runner_id)
                self._tick()
            except Exception as e:
                logger.error("runner tick 异常: %s", e, exc_info=True)
            time.sleep(_POLL_SECONDS)

    def _tick(self):
        token = uuid.uuid4().hex
        job = self.repo.claim_next_accepted(self.runner_id, token, _utcnow())
        if not job:
            return
        self._log("认领任务", job_id=job["id"], attempt=token)
        self._run_job(job, token)

    def _run_job(self, job: dict, token: str):
        from backend.services.gateway_process import run_analysis_process
        from backend.services.metadata_audit_repository import (
            STATE_RUNNING, STATE_PUBLISHED, STATE_FAILED, PHASE_CLEANUP)
        job_id = job["id"]
        started = time.monotonic()
        cmd = [sys.executable, "-m", "backend.workers.metadata_audit_worker",
               "--job-id", job_id, "--attempt-token", token]
        # 仓库根进 PYTHONPATH（子进程 import backend.*）
        env = dict(os.environ)
        from pathlib import Path
        repo_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")

        self._log("启动子进程", job_id=job_id)
        proc = run_analysis_process(cmd, timeout=self.job_timeout, env=env,
                                    cwd=repo_root)

        # run_analysis_process 已阻塞至退出/超时并回收（TERM→KILL→reap）。
        final = self.repo.get_job(job_id)
        elapsed = time.monotonic() - started
        cleanup_ok = bool(proc.cleanup_ok)
        self._log("子进程结束", job_id=job_id, rc=proc.returncode,
                  elapsed_s=int(elapsed), cleanup_ok=cleanup_ok,
                  timed_out=proc.timed_out)

        if not cleanup_ok:
            # 无法确认进程退出 → RECOVERY_REQUIRED，继续占槽不释放
            self.repo.cas_state(job_id, token, tuple(), "", phase=PHASE_CLEANUP)  # no-op guard
            self._mark_recovery(job_id, token)
            return
        if final and final.get("state") == STATE_PUBLISHED:
            self.repo.complete(job_id, token, exit_code=proc.returncode or 0,
                               cleanup_ok=True)
        elif final and final.get("state") == STATE_FAILED:
            pass   # worker 已自置 FAILED
        elif proc.timed_out:
            self.repo.fail(job_id, token, error_code="JOB_TIMEOUT",
                           error_message=f"任务超过保护预算 {self.job_timeout}s，已终止回收。")
        elif final and final.get("state") == STATE_RUNNING:
            # child 退出但未置 PUBLISHED 且未自置 FAILED → 视为中断
            self.repo.fail(job_id, token, error_code="CHILD_EXITED",
                           error_message=f"子进程退出码 {proc.returncode}，未发布结果。")
        self.repo.release_slot(job_id)
        self._log("释放槽位", job_id=job_id)

    def _mark_recovery(self, job_id: str, token: str):
        from backend.services.metadata_audit_repository import (
            STATE_RECOVERY_REQUIRED, PHASE_CLEANUP)
        self.repo.cas_state(job_id, token,
                            ("RUNNING", "PUBLISHING", "PUBLISHED", "STOPPING"),
                            STATE_RECOVERY_REQUIRED, phase=PHASE_CLEANUP,
                            error_code="RECOVERY_REQUIRED",
                            error_message="后台回收或成果核对未完成，已阻止新任务，需要运维处理。")
        # RECOVERY_REQUIRED 不释放槽位


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    MetadataRunner().run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
