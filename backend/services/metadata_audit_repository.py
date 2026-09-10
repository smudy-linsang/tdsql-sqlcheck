# -*- coding: utf-8 -*-
"""元数据在线审核任务的持久化与并发协调层（v1.6.3.5 / DU-2 / FIX-02 / D03）。

设计出处：docs/DETAIL-v1.6.3.5-大库在线元数据审核稳定性修复.md §6、§7.3。

职责（不在本层做业务提取/审核）：
  · 受理事务：`slot FOR UPDATE → job`（统一锁顺序防死锁），幂等重放、busy 拒绝、
    冻结执行上下文、写入 ACCEPTED 并占槽；
  · runner 认领（CAS 到 RUNNING + attempt_token fencing）、进度/心跳更新；
  · 状态机 CAS 迁移（带 attempt_token + state 守卫，检查影响行数）；
  · 原子发布（严格写 audit_history + 关联 report_id；max_allowed_packet 预检，
    不吞错、不截断成成功）；
  · 取消意图、槽位释放、回收确认。

所有 child 更新必须 `WHERE id=? AND attempt_token=? AND state IN (...)` 并核对影响行数；
迟到/过期 attempt 不得覆盖终态。元数据库为 MySQL，占位符用 `?`（兼容游标转 %s）。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from backend.services.database import _get_connection

logger = logging.getLogger("tdsql.metadata_repo")

# ── 状态与阶段封闭枚举（应用层校验，DB 不用 ENUM）──────────────────────────
STATE_ACCEPTED = "ACCEPTED"
STATE_RUNNING = "RUNNING"
STATE_PUBLISHING = "PUBLISHING"
STATE_PUBLISHED = "PUBLISHED"
STATE_SUCCEEDED = "SUCCEEDED"
STATE_STOPPING = "STOPPING"
STATE_FAILED = "FAILED"
STATE_CANCELLED = "CANCELLED"
STATE_RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
TERMINAL_STATES = {STATE_SUCCEEDED, STATE_FAILED, STATE_CANCELLED}
ACTIVE_STATES = {STATE_ACCEPTED, STATE_RUNNING, STATE_PUBLISHING,
                 STATE_PUBLISHED, STATE_STOPPING, STATE_RECOVERY_REQUIRED}

PHASE_WAITING = "WAITING"
PHASE_ENUMERATING = "ENUMERATING"
PHASE_EXTRACTING = "EXTRACTING"
PHASE_AUDITING = "AUDITING"
PHASE_SERIALIZING = "SERIALIZING"
PHASE_SNAPSHOTTING = "SNAPSHOTTING"
PHASE_PERSISTING = "PERSISTING"
PHASE_CLEANUP = "CLEANUP"
PHASE_DONE = "DONE"

SLOT_ID = 1
# 发布前包大小预检余量（转义膨胀按 2 倍 + 64 KiB）
_PACKET_MARGIN = 64 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _row_to_dict(row) -> Optional[dict]:
    return dict(row) if row is not None else None


class MetadataJobError(Exception):
    """业务错误：携带稳定 code 与中文 message（不落 traceback/凭据）。"""
    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class MetadataJobRepository:
    """元数据审核任务 repository（短事务 + 唯一 slot 原子受理）。"""

    # ── 受理 ────────────────────────────────────────────────────────────
    def create_job(self, *, created_by: str, request_id: str,
                   idempotency_key: str, request_hash: str,
                   connection_id: str, db_name: str, request_json: str,
                   execution_context_json: str, connection_fingerprint: str,
                   report_deadline_seconds: int) -> tuple[dict, bool]:
        """受理事务。返回 (job_row, created_new)。

        幂等：同 (created_by, idempotency_key) 且 request_hash 相同 → 返回已有 job
        （created_new=False）；hash 不同 → 409/IDEMPOTENCY_CONFLICT。
        busy：active_job_id 非空 → 409/METADATA_BUSY（不排长队）。
        锁顺序固定 slot → job。
        """
        # D-02：受理前先回收"受理后超期未被认领"的幽灵任务（释放唯一槽），
        # 使 runner 已死时新请求仍能自愈，不被永久 METADATA_BUSY 挡住。
        try:
            from backend.services import metadata_job_process as _jp
            self.reclaim_stale_accepted(_jp.MetadataLimits.START_TIMEOUT)
        except Exception:
            pass   # 回收失败不阻断本次受理
        conn = _get_connection()
        try:
            now = _now()
            deadline = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + report_deadline_seconds,
                timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            conn.execute("SELECT * FROM metadata_audit_slot WHERE id=? FOR UPDATE",
                         (SLOT_ID,))
            slot = conn.execute(
                "SELECT * FROM metadata_audit_slot WHERE id=?", (SLOT_ID,)).fetchone()
            # 幂等重放：同 key 同 hash → 返回已有 job（不受 busy 限制）
            existing = conn.execute(
                "SELECT * FROM metadata_audit_jobs WHERE created_by=? AND idempotency_key=?",
                (created_by, idempotency_key)).fetchone()
            if existing is not None:
                ex = _row_to_dict(existing)
                if ex["request_hash"] == request_hash:
                    conn.commit()
                    return ex, False
                conn.rollback()
                raise MetadataJobError(
                    "IDEMPOTENCY_CONFLICT",
                    "本次提交标识与原参数不一致，请重新发起。", 409)
            # busy：槽位被占
            slot_d = _row_to_dict(slot) if slot else None
            if slot_d and slot_d.get("active_job_id"):
                conn.rollback()
                raise MetadataJobError(
                    "METADATA_BUSY",
                    "当前已有一个元数据审核任务正在执行，请稍后重试。", 409)
            job_id = _new_job_id()
            conn.execute(
                """INSERT INTO metadata_audit_jobs
                   (id, created_by, request_id, idempotency_key, request_hash,
                    connection_id, db_name, request_json, execution_context_json,
                    connection_fingerprint, state, phase, created_at, updated_at,
                    deadline_at, artifact_state)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, created_by, request_id, idempotency_key, request_hash,
                 connection_id, db_name, request_json, execution_context_json,
                 connection_fingerprint, STATE_ACCEPTED, PHASE_WAITING,
                 now, now, deadline, "NONE"))
            # 占槽
            conn.execute(
                "UPDATE metadata_audit_slot SET active_job_id=?, updated_at=? WHERE id=?",
                (job_id, now, SLOT_ID))
            conn.commit()
            row = conn.execute(
                "SELECT * FROM metadata_audit_jobs WHERE id=?", (job_id,)).fetchone()
            return _row_to_dict(row), True
        except MetadataJobError:
            raise
        except Exception as e:
            conn.rollback()
            logger.error("受理元数据审核任务失败: %s", e, exc_info=True)
            raise MetadataJobError("REPOSITORY_ERROR", f"任务受理失败: {e}", 500) from e
        finally:
            conn.close()

    # ── 查询 ────────────────────────────────────────────────────────────
    def get_job(self, job_id: str) -> Optional[dict]:
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM metadata_audit_jobs WHERE id=?", (job_id,)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    def get_job_by_idempotency(self, created_by: str, key: str) -> Optional[dict]:
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM metadata_audit_jobs WHERE created_by=? AND idempotency_key=?",
                (created_by, key)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    def list_jobs(self, created_by: Optional[str], limit: int = 20,
                  offset: int = 0, state: str = "") -> tuple[list, int]:
        conn = _get_connection()
        try:
            where, params = [], []
            if created_by:
                where.append("created_by=?")
                params.append(created_by)
            if state:
                where.append("state=?")
                params.append(state)
            w = ("WHERE " + " AND ".join(where)) if where else ""
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM metadata_audit_jobs {w}", params).fetchone()
            rows = conn.execute(
                f"SELECT * FROM metadata_audit_jobs {w} "
                f"ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                params + [int(limit), int(offset)]).fetchall()
            return [dict(r) for r in rows], int((total or {}).get("c", 0))
        finally:
            conn.close()

    # ── runner 认领（CAS 到 RUNNING + fencing token）─────────────────────
    def claim_next_accepted(self, runner_id: str, attempt_token: str,
                            heartbeat_now: str) -> Optional[dict]:
        """认领一个 ACCEPTED 任务：CAS ACCEPTED→RUNNING，写 runner_id/attempt_token/
        child 启动期限。返回认领到的 job；无则 None。"""
        conn = _get_connection()
        try:
            conn.execute("SELECT * FROM metadata_audit_slot WHERE id=? FOR UPDATE",
                         (SLOT_ID,))
            slot = _row_to_dict(conn.execute(
                "SELECT * FROM metadata_audit_slot WHERE id=?", (SLOT_ID,)).fetchone())
            job_id = slot.get("active_job_id") if slot else None
            if not job_id:
                conn.commit()
                return None
            job = _row_to_dict(conn.execute(
                "SELECT * FROM metadata_audit_jobs WHERE id=? FOR UPDATE",
                (job_id,)).fetchone())
            if not job or job["state"] != STATE_ACCEPTED:
                conn.commit()
                return None
            now = _now()
            cur = conn.execute(
                """UPDATE metadata_audit_jobs SET state=?, phase=?, runner_id=?,
                   attempt_token=?, started_at=?, updated_at=?, heartbeat_at=?
                   WHERE id=? AND state=?""",
                (STATE_RUNNING, PHASE_ENUMERATING, runner_id, attempt_token,
                 now, now, heartbeat_now, job_id, STATE_ACCEPTED))
            if getattr(cur, "rowcount", 1) < 1:
                conn.rollback()
                return None
            conn.commit()
            job["state"] = STATE_RUNNING
            job["attempt_token"] = attempt_token
            return job
        except Exception as e:
            conn.rollback()
            logger.error("认领元数据任务失败: %s", e, exc_info=True)
            return None
        finally:
            conn.close()

    # ── CAS 状态迁移（attempt_token + state 守卫）─────────────────────────
    def cas_state(self, job_id: str, attempt_token: str, from_states: tuple,
                  to_state: str, *, phase: Optional[str] = None,
                  error_code: Optional[str] = None,
                  error_message: Optional[str] = None,
                  extra: Optional[dict] = None) -> bool:
        """条件迁移；返回是否命中（影响行数>0）。未命中=迟到/过期，调用方不得继续。"""
        sets = ["state=?", "updated_at=?"]
        params: list = [to_state, _now()]
        if phase is not None:
            sets.append("phase=?")
            params.append(phase)
        if error_code is not None:
            sets.append("error_code=?")
            params.append(error_code)
        if error_message is not None:
            sets.append("error_message=?")
            params.append((error_message or "")[:1024])
        for k, v in (extra or {}).items():
            sets.append(f"{k}=?")
            params.append(v)
        placeholders = ",".join(["?"] * len(from_states))
        params += [job_id, attempt_token, *from_states]
        conn = _get_connection()
        try:
            cur = conn.execute(
                f"UPDATE metadata_audit_jobs SET {', '.join(sets)} "
                f"WHERE id=? AND attempt_token=? AND state IN ({placeholders})",
                params)
            hit = getattr(cur, "rowcount", 1) >= 1
            conn.commit()
            return hit
        finally:
            conn.close()

    def update_progress(self, job_id: str, attempt_token: str, phase: str,
                        progress_json: str) -> None:
        conn = _get_connection()
        try:
            now = _now()
            conn.execute(
                """UPDATE metadata_audit_jobs SET phase=?, progress_json=?,
                   progress_at=?, updated_at=? WHERE id=? AND attempt_token=?""",
                (phase, progress_json, now, now, job_id, attempt_token))
            conn.commit()
        finally:
            conn.close()

    def runner_heartbeat(self, job_id: str, attempt_token: str) -> None:
        conn = _get_connection()
        try:
            now = _now()
            conn.execute(
                """UPDATE metadata_audit_jobs SET heartbeat_at=?, updated_at=?
                   WHERE id=? AND attempt_token=?""",
                (now, now, job_id, attempt_token))
            conn.commit()
        finally:
            conn.close()

    # ── 取消意图 ────────────────────────────────────────────────────────
    def request_cancel(self, job_id: str) -> Optional[dict]:
        conn = _get_connection()
        try:
            now = _now()
            conn.execute(
                """UPDATE metadata_audit_jobs SET cancel_requested_at=?, updated_at=?
                   WHERE id=? AND state IN (?,?,?,?)""",
                (now, now, job_id, STATE_ACCEPTED, STATE_RUNNING,
                 STATE_PUBLISHING, STATE_PUBLISHED))
            conn.commit()
            return self.get_job(job_id)
        finally:
            conn.close()

    def is_cancel_requested(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        return bool(job and job.get("cancel_requested_at"))

    # ── 原子发布（严格写 audit_history + 关联 report_id）──────────────────
    def publish(self, job_id: str, attempt_token: str, *,
                audit_columns_values: tuple, results_json: str) -> int:
        """严格原子发布：先包大小预检，再事务 INSERT audit_history + 关联 job.report_id。

        audit_columns_values 与 audit_history 的 21 列对齐（见 _save_audit_history）。
        返回 report_id。任何失败抛 MetadataJobError（不吞错、不截断成成功）。
        """
        conn = _get_connection()
        try:
            # max_allowed_packet 预检（mogrify 精确化以 results_json UTF-8 字节近似，
            # 含转义膨胀按 2 倍 + 64 KiB 余量；超限明确失败，不自动 SET GLOBAL/截断）
            payload = len(results_json.encode("utf-8")) * 2 + _PACKET_MARGIN
            row = conn.execute("SELECT @@session.max_allowed_packet AS p").fetchone()
            max_pkt = int((row or {}).get("p") or 0)
            if max_pkt and payload > max_pkt:
                raise MetadataJobError(
                    "PERSIST_PAYLOAD_TOO_LARGE",
                    f"审核结果编码后约 {payload} 字节，超过元数据库 max_allowed_packet "
                    f"({max_pkt})；请评估元数据库容量后重试。", 507)

            conn.execute("SELECT * FROM metadata_audit_slot WHERE id=? FOR UPDATE",
                         (SLOT_ID,))
            job = _row_to_dict(conn.execute(
                "SELECT * FROM metadata_audit_jobs WHERE id=? FOR UPDATE",
                (job_id,)).fetchone())
            if not job or job["attempt_token"] != attempt_token:
                conn.rollback()
                raise MetadataJobError("STALE_ATTEMPT", "执行凭证已过期，终止发布。", 409)
            if job["state"] == STATE_PUBLISHED and job.get("report_id"):
                conn.commit()
                return int(job["report_id"])    # 幂等：已发布返回已有 report_id
            if job["state"] != STATE_PUBLISHING:
                conn.rollback()
                raise MetadataJobError(
                    "INVALID_STATE", f"当前状态 {job['state']} 不可发布。", 409)

            now = _now()
            cur = conn.execute(
                """INSERT INTO audit_history (audit_type, source, total_sql, passed, failed,
                    error_count, warning_count, pass_rate, results_json,
                    created_by, project_id, gate_passed, gate_detail, created_at,
                    connection_id, db_name, rule_set_id,
                    instance_type, instance_type_source, skipped_rules_count,
                    report_context_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                audit_columns_values)
            report_id = int(getattr(cur, "lastrowid", 0) or 0)
            if not report_id:
                conn.rollback()
                raise MetadataJobError("REPORT_SAVE_FAILED", "报告落库未获得 report_id。", 500)
            upd = conn.execute(
                """UPDATE metadata_audit_jobs SET state=?, report_id=?, updated_at=?,
                   artifact_state='READY' WHERE id=? AND attempt_token=? AND state=?""",
                (STATE_PUBLISHED, report_id, now, job_id, attempt_token,
                 STATE_PUBLISHING))
            if getattr(upd, "rowcount", 1) < 1:
                conn.rollback()
                raise MetadataJobError("STALE_ATTEMPT", "发布时状态已变化。", 409)
            conn.commit()
            return report_id
        except MetadataJobError:
            raise
        except Exception as e:
            conn.rollback()
            logger.error("发布元数据审核结果失败: %s", e, exc_info=True)
            raise MetadataJobError("REPORT_SAVE_FAILED", f"报告落库失败: {e}", 500) from e
        finally:
            conn.close()

    # ── 槽位释放 / 完成 ──────────────────────────────────────────────────
    def slot_heartbeat(self, runner_id: str) -> None:
        """runner 监督心跳：置 accepting=1 + runner_heartbeat_at=now（受理前置校验依据）。"""
        conn = _get_connection()
        try:
            conn.execute(
                """UPDATE metadata_audit_slot SET runner_id=?, runner_heartbeat_at=?,
                   accepting=1, updated_at=? WHERE id=?""",
                (runner_id, _now(), _now(), SLOT_ID))
            conn.commit()
        finally:
            conn.close()

    def slot_set_accepting(self, accepting: bool) -> None:
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE metadata_audit_slot SET accepting=?, updated_at=? WHERE id=?",
                (1 if accepting else 0, _now(), SLOT_ID))
            conn.commit()
        finally:
            conn.close()

    def slot_state(self) -> Optional[dict]:
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM metadata_audit_slot WHERE id=?", (SLOT_ID,)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    def release_slot(self, job_id: str) -> None:
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE metadata_audit_slot SET active_job_id=NULL, updated_at=? "
                "WHERE id=? AND active_job_id=?", (_now(), SLOT_ID, job_id))
            conn.commit()
        finally:
            conn.close()

    def complete(self, job_id: str, attempt_token: str, *, exit_code: int,
                 cleanup_ok: bool) -> bool:
        """PUBLISHED → SUCCEEDED（回收确认后）。返回是否命中。"""
        return self.cas_state(
            job_id, attempt_token, (STATE_PUBLISHED,), STATE_SUCCEEDED,
            phase=PHASE_DONE,
            extra={"finished_at": _now(), "exit_code": int(exit_code),
                   "cleanup_ok": 1 if cleanup_ok else 0})

    def fail(self, job_id: str, attempt_token: str, *, error_code: str,
             error_message: str, from_states: tuple = tuple(ACTIVE_STATES)) -> bool:
        """→ FAILED（回收确认后由调用方负责 release_slot）。返回是否命中。"""
        return self.cas_state(
            job_id, attempt_token, from_states, STATE_FAILED, phase=PHASE_CLEANUP,
            error_code=error_code, error_message=error_message,
            extra={"finished_at": _now()})

    def cancel(self, job_id: str, attempt_token: str, *, cleanup_ok: bool = True) -> bool:
        """→ CANCELLED（终态写 finished_at，与 fail/complete 同口径）。

        UAT-O-1635-R2 D-01：取消任务的 finished_at 必须落库，否则终态耗时会随查询
        时间无限膨胀（finished_at 缺失时 elapsed 回退到当前时间）。
        """
        return self.cas_state(
            job_id, attempt_token,
            (STATE_ACCEPTED, STATE_RUNNING, STATE_PUBLISHING, STATE_STOPPING),
            STATE_CANCELLED, phase=PHASE_CLEANUP, error_code="CANCELLED",
            error_message="任务已被用户取消。",
            extra={"finished_at": _now(), "cleanup_ok": 1 if cleanup_ok else 0})

    def reclaim_stale_accepted(self, start_timeout_seconds: int) -> Optional[str]:
        """回收"受理后超期未被认领"的任务：判 FAILED/START_TIMEOUT 并释放槽。

        UAT-O-1635-R2 D-02：runner 受理后失效/被 SIGKILL 时，唯一受理槽被幽灵任务
        永久占用。本方法只处理"确证从未被认领"的过期 ACCEPTED 任务（不碰 RUNNING、
        不抢占其他 runner 的槽、不发信号不杀 PID）。返回被回收的 job_id 或 None。

        守卫（缺一不可）：job.state='ACCEPTED' 且 slot.active_job_id=job.id 且
        超期（now - created_at > start_timeout_seconds）。锁序保持 slot → job。
        """
        conn = _get_connection()
        try:
            conn.execute("SELECT * FROM metadata_audit_slot WHERE id=? FOR UPDATE",
                         (SLOT_ID,))
            slot = _row_to_dict(conn.execute(
                "SELECT * FROM metadata_audit_slot WHERE id=?", (SLOT_ID,)).fetchone())
            job_id = (slot or {}).get("active_job_id")
            if not job_id:
                conn.commit()
                return None
            job = _row_to_dict(conn.execute(
                "SELECT * FROM metadata_audit_jobs WHERE id=? FOR UPDATE",
                (job_id,)).fetchone())
            if not job or job["state"] != STATE_ACCEPTED:
                conn.commit()
                return None
            # 超期判定：now(UTC) - created_at > start_timeout_seconds
            from backend.services import metadata_job_process as _jp
            created = _jp.parse_utc(job.get("created_at"))
            if created is None:
                conn.commit()
                return None
            from datetime import datetime, timezone
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age <= start_timeout_seconds:
                conn.commit()
                return None
            now = _now()
            cur = conn.execute(
                """UPDATE metadata_audit_jobs SET state=?, phase=?, error_code=?,
                   error_message=?, finished_at=?, cleanup_ok=1, updated_at=?
                   WHERE id=? AND state=?""",
                (STATE_FAILED, PHASE_CLEANUP, "START_TIMEOUT",
                 "受理后执行器未在期限内认领，任务已作废并释放受理槽。",
                 now, now, job_id, STATE_ACCEPTED))
            if getattr(cur, "rowcount", 1) < 1:
                conn.rollback()
                return None
            conn.execute(
                "UPDATE metadata_audit_slot SET active_job_id=NULL, updated_at=? "
                "WHERE id=? AND active_job_id=?", (now, SLOT_ID, job_id))
            conn.commit()
            logger.warning("回收过期未认领任务 job_id=%s age_s=%.1f", job_id, age)
            return job_id
        except Exception as e:
            conn.rollback()
            logger.error("回收过期未认领任务失败: %s", e, exc_info=True)
            return None
        finally:
            conn.close()


def _new_job_id() -> str:
    import uuid
    return uuid.uuid4().hex


repository = MetadataJobRepository()
