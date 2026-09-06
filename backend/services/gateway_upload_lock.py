# -*- coding: utf-8 -*-
"""v1.6.3.4 / D05：网关大日志上传的跨 worker 非阻塞槽（文件锁 + 状态文件）

设计出处：docs/DETAIL-v1.6.3.4-报告实例标识与分区统计及审核网关修复.md §6.3/§6.4

单机跨 worker 上传槽（Mr.Linsang 裁定：同时只允许一个网关日志任务，拒绝者
不入队、不分析其文件）：
  · Linux 使用同一私有临时根的 advisory file lock（fcntl.flock）；
  · Windows 使用等价文件锁（msvcrt.locking）；
  · 进程退出由 OS 释放；锁文件不删除再重建（避免 inode 变化制造两个锁）；
  · 不是进程内 Semaphore；所有 worker 必须共享同一锁路径（部署前验证）。
  · 多应用主机不属于本版已批准的单槽实现范围（须先经评审）。

状态文件（同目录独立文件）供 429 的 Retry-After 估算：持槽者原子更新
owner_nonce、阶段、该阶段 monotonic deadline。该元信息**只供提示**，文件锁
才是准入权威，不能根据倒计时强行偷锁/杀任务；任务可能转入下一阶段或超出
软预算，故不承诺预计完成时间。只在仍为同一 owner 时清理本任务状态，重启
残留不作为忙闲判断依据。
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import tempfile
import time

logger = logging.getLogger("tdsql.gateway_lock")

# 阶段常量（§6.4：receiving 用收体剩余预算，processing 用协调剩余预算）
STAGE_RECEIVING = "receiving"
STAGE_PROCESSING = "processing"

# Retry-After 估算边界（§6.4：向上取整并限制为 5—600 秒；缺失/过期/不可解析/
# 任务在收尾时回退 600）
_RETRY_AFTER_MIN = 5
_RETRY_AFTER_MAX = 600
_RETRY_AFTER_FALLBACK = 600


def resolve_lock_dir(tmp_dir: str = "") -> str:
    """解析锁目录：GATEWAY_TMP_DIR 优先，否则系统临时目录下的独立子目录。

    绝不放 Web 静态目录。目录仅运行账号可访问（0o700）。所有 worker 必须
    解析到同一路径（部署前验证），否则单槽约束失效。
    """
    base = (tmp_dir or "").strip()
    if not base:
        base = os.path.join(tempfile.gettempdir(), "tdsql_gateway_upload")
    try:
        os.makedirs(base, mode=0o700, exist_ok=True)
    except OSError as e:
        logger.warning("网关上传锁目录创建失败 %s: %s", base, e)
    return base


class GatewayUploadSlot:
    """跨 worker 非阻塞上传槽（advisory 文件锁 + 状态文件）。

    用法（§6.4 第 4 条：仅释放本请求实际取得的槽）::

        slot = GatewayUploadSlot(tmp_dir)
        nonce = secrets.token_hex(8)
        if not slot.try_acquire(nonce):
            retry = slot.peek_retry_after()   # 忙 → 429 + Retry-After
            ...
            return
        try:
            slot.update_stage(STAGE_RECEIVING, now + receive_budget)
            ... 收体 ...
            slot.update_stage(STAGE_PROCESSING, now + processing_budget)
            ... 分析/落库 ...
        finally:
            slot.release()   # 取槽前被拒的请求 _acquired=False，不会释放他人锁
    """

    def __init__(self, tmp_dir: str = ""):
        self.lock_dir = resolve_lock_dir(tmp_dir)
        self.lock_path = os.path.join(self.lock_dir, "gateway_upload.lock")
        self.state_path = os.path.join(self.lock_dir, "gateway_upload_state.json")
        self._fd = None
        self._acquired = False
        self._owner_nonce = ""

    @property
    def acquired(self) -> bool:
        return self._acquired

    def try_acquire(self, owner_nonce: str) -> bool:
        """非阻塞尝试取锁。成功 True 并写初始状态；忙返回 False。

        锁文件持久存在（O_CREAT 但不 O_EXCL/O_TRUNC，保留 inode，不删除再重建）；
        advisory lock 由 OS 在进程退出时释放。取锁失败（忙）时关闭 fd，绝不
        影响持锁者。
        """
        if self._acquired:
            return True
        try:
            self._fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as e:
            logger.warning("网关上传锁文件打开失败 %s: %s", self.lock_path, e)
            self._fd = None
            return False
        # 确保文件至少 1 字节，供 Windows msvcrt.locking 锁定（Linux flock 无此要求）
        try:
            if os.fstat(self._fd).st_size == 0:
                os.write(self._fd, b"\n")
            os.lseek(self._fd, 0, os.SEEK_SET)
        except OSError:
            pass
        try:
            if sys.platform == "win32":
                import msvcrt
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # 忙：锁被其他 worker 持有。关闭本请求 fd，绝不动他人锁。
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            return False
        self._acquired = True
        self._owner_nonce = owner_nonce
        # 写初始状态（receiving 阶段，deadline 由调用方随后用真实预算刷新）
        self._write_state(STAGE_RECEIVING, time.monotonic() + _RETRY_AFTER_MAX)
        return True

    def update_stage(self, stage: str, deadline_mono: float):
        """更新阶段与 monotonic deadline（仅持槽者调用；未持槽静默忽略）。

        deadline_mono 用 time.monotonic()——同机多 worker 的 monotonic 基准一致
        （系统级），故 peek_retry_after 可跨进程比较剩余预算。
        """
        if self._acquired:
            self._write_state(stage, deadline_mono)

    def _write_state(self, stage: str, deadline_mono: float):
        """原子写状态文件（临时文件 + os.replace）。失败仅影响 Retry-After 估算。"""
        state = {
            "owner_nonce": self._owner_nonce,
            "stage": stage,
            "deadline_mono": deadline_mono,
            "pid": os.getpid(),
            "updated_wall": time.time(),
        }
        tmp = f"{self.state_path}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, self.state_path)
        except OSError as e:
            logger.debug("网关上传状态文件写入失败（忽略）: %s", e)
            try:
                os.remove(tmp)
            except OSError:
                pass

    def release(self):
        """释放锁（仅本请求实际取得的槽）+ 清理本任务状态（仅同一 owner）。

        §6.4 第 4 条：已取槽后超限/取消要释放；取槽前被 Content-Length 拒绝或
        取锁失败的请求不得释放他人的槽——故仅当 _acquired 为 True 才释放。
        重启残留状态文件不作为忙闲判断依据（准入只看文件锁）。
        """
        if not self._acquired or self._fd is None:
            return
        # 清理状态文件：只在仍为同一 owner 时清理本任务状态
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as f:
                    st = json.load(f)
                if st.get("owner_nonce") == self._owner_nonce:
                    os.remove(self.state_path)
        except (OSError, ValueError):
            pass
        # 释放 advisory lock 并关闭 fd
        try:
            if sys.platform == "win32":
                import msvcrt
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        self._acquired = False
        self._owner_nonce = ""

    def peek_retry_after(self) -> int:
        """读状态文件估算 Retry-After（整数秒，5—600）。

        §6.4：receiving 用收体剩余预算，processing 用协调剩余预算，向上取整并
        限制为 5—600 秒；缺失/过期/不可解析/任务在收尾时回退 600。该元信息只供
        提示，文件锁才是准入权威，不能据此强行偷锁/杀任务；不承诺预计完成时间。
        """
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            return _RETRY_AFTER_FALLBACK
        deadline_mono = st.get("deadline_mono")
        if not isinstance(deadline_mono, (int, float)):
            return _RETRY_AFTER_FALLBACK
        # 同机多 worker monotonic 基准一致，可跨进程比较
        remaining = float(deadline_mono) - time.monotonic()
        if remaining <= 0:
            # 过期或任务在收尾（未刷新到下一阶段）→ 回退保守值
            return _RETRY_AFTER_FALLBACK
        retry = int(math.ceil(remaining))
        return max(_RETRY_AFTER_MIN, min(_RETRY_AFTER_MAX, retry))


def new_owner_nonce() -> str:
    """生成本请求的 owner_nonce（用于状态文件归属判定，防误清他人状态）。"""
    import secrets
    return secrets.token_hex(8)
