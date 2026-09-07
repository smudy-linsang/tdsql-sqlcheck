# -*- coding: utf-8 -*-
"""v1.6.3.4 / D06：网关日志分析子进程生命周期管理（REQ-04，DETAIL §6.5）

封装分析子进程的启动、输出排空、超时回收与清理确认：
  · 子进程通过 sys.executable 与受控 argv 启动，不用 shell；
  · stdout/stderr 分开连续排空，内存只留各 64 KiB 尾部（不 capture_output 无限累积）；
  · Linux 独立进程组，超时/取消按 TERM→等候最多 5 秒→仍存活则 KILL→wait/reap；
  · Windows 用标准库 ctypes 管理 Job Object（KILL_ON_JOB_CLOSE）约束子树，
    不声称 CREATE_NEW_PROCESS_GROUP 本身能杀子孙进程；无 POSIX TERM 等价证据时
    记录 term_not_applicable，不伪造 exit_after_term=true；
  · 记录 pid、returncode、timed_out、exit_after_term、forced_kill 和清理结果；
    只有确认退出后才能删其目录、释放分析资源。
  · 不修改此前已验收的其他模块进程退出行为。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque

logger = logging.getLogger("tdsql.gateway_process")

# stdout/stderr 内存尾部保留（§6.5：各 64 KiB）
_TAIL_BYTES = 64 * 1024
# TERM 等待最多 5 秒，KILL 后回收另预留 5 秒（§6.3.1 超时链）
_TERM_WAIT_SECONDS = 5
_KILL_REAP_SECONDS = 5
# 每次排空读取的块大小
_DRAIN_CHUNK = 65536

# Windows Job Object 常量
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9


class ProcessResult:
    """分析子进程执行结果（§6.5 要求记录的字段）。"""

    def __init__(self):
        self.pid = None
        self.returncode = None
        self.timed_out = False
        self.exit_after_term = False      # TERM 后正常退出（Linux）
        self.forced_kill = False          # TERM 无效，KILL 强杀
        self.term_not_applicable = False  # Windows 无 POSIX TERM 等价
        self.stdout_tail = ""
        self.stderr_tail = ""
        self.duration_ms = 0
        self.cleanup_ok = False           # 只有确认退出才 True
        self.start_error = ""             # 启动失败原因（如脚本缺失）

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "exit_after_term": self.exit_after_term,
            "forced_kill": self.forced_kill,
            "term_not_applicable": self.term_not_applicable,
            "duration_ms": self.duration_ms,
            "cleanup_ok": self.cleanup_ok,
        }


def _drain_stream(stream, tail_buf: deque, lock: threading.Lock):
    """连续排空一个流，内存只留尾部 _TAIL_BYTES（§6.5）。

    在独立线程中运行，避免子进程输出塞满管道缓冲区导致死锁；
    不用 capture_output（会把全部输出累积进内存）。
    """
    try:
        while True:
            chunk = stream.read(_DRAIN_CHUNK)
            if not chunk:
                break
            with lock:
                tail_buf.append(chunk)
                # 只保留尾部 _TAIL_BYTES：从队首丢弃旧块
                total = sum(len(x) for x in tail_buf)
                while total > _TAIL_BYTES and len(tail_buf) > 1:
                    dropped = tail_buf.popleft()
                    total -= len(dropped)
    except Exception:                                    # noqa: BLE001
        pass
    finally:
        try:
            stream.close()
        except Exception:                                # noqa: BLE001
            pass


# ── Windows Job Object（ctypes，约束子进程树）──────────────────────────
def _create_windows_job():
    """创建带 KILL_ON_JOB_CLOSE 的 Job Object。失败返回 None（降级单进程管理）。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job, _JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception as e:                                # noqa: BLE001
        logger.warning("Windows Job Object 创建失败（降级为单进程管理）: %s", e)
        return None


def _assign_to_job(job, pid: int):
    """把已启动的进程加入 Job Object（约束其子树）。"""
    if job is None or sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_TERMINATE = 0x0001
        PROCESS_SET_QUOTA = 0x0100
        handle = kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA, False, pid)
        if handle:
            kernel32.AssignProcessToJobObject(job, handle)
            kernel32.CloseHandle(handle)
    except Exception as e:                                # noqa: BLE001
        logger.warning("进程加入 Job Object 失败: %s", e)


def _close_windows_job(job):
    """关闭 Job Object；KILL_ON_JOB_CLOSE 会终止任何残留子树。"""
    if job is None or sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(job)
    except Exception:                                    # noqa: BLE001
        pass


def _terminate(proc, is_win: bool, result: ProcessResult):
    """超时/取消的进程回收：TERM→等候最多 5 秒→仍存活则 KILL→wait/reap。"""
    if is_win:
        # Windows 没有 POSIX TERM 等价证据：Job Object 的 KILL_ON_JOB_CLOSE 在
        # 关闭句柄时终止子树。这里 terminate() 等价 TerminateProcess（强杀），
        # 故记录 term_not_applicable，不伪造 exit_after_term。
        result.term_not_applicable = True
        try:
            proc.terminate()
        except Exception:                                # noqa: BLE001
            pass
        try:
            proc.wait(timeout=_TERM_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            result.forced_kill = True
            try:
                proc.kill()
                proc.wait(timeout=_KILL_REAP_SECONDS)
            except Exception:                            # noqa: BLE001
                pass
    else:
        # Linux：TERM 整个进程组（子进程用 start_new_session 独立进程组）
        try:
            os.killpg(os.getpgid(proc.pid), 15)          # SIGTERM
        except Exception:                                # noqa: BLE001
            try:
                proc.terminate()
            except Exception:                            # noqa: BLE001
                pass
        try:
            proc.wait(timeout=_TERM_WAIT_SECONDS)
            result.exit_after_term = True
        except subprocess.TimeoutExpired:
            result.forced_kill = True
            try:
                os.killpg(os.getpgid(proc.pid), 9)       # SIGKILL
            except Exception:                            # noqa: BLE001
                try:
                    proc.kill()
                except Exception:                        # noqa: BLE001
                    pass
            try:
                proc.wait(timeout=_KILL_REAP_SECONDS)
            except Exception:                            # noqa: BLE001
                pass


def run_analysis_process(cmd, timeout: float, env=None, cwd=None) -> ProcessResult:
    """执行分析子进程并管理其完整生命周期（§6.5）。

    Args:
        cmd: 受控 argv 列表（sys.executable + 脚本 + 参数），绝不用 shell
        timeout: 墙钟超时（秒），来自 GATEWAY_ANALYSIS_TIMEOUT_SECONDS
        env: 环境变量（须含 repo PYTHONPATH，供分析器延迟导入 backend.*）
        cwd: 工作目录

    Returns:
        ProcessResult。只有 cleanup_ok=True（确认退出）后，调用方才可删除
        子进程目录、释放分析资源。
    """
    result = ProcessResult()
    is_win = sys.platform == "win32"
    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "env": env,
        "cwd": cwd,
        "shell": False,
    }
    job = None
    if is_win:
        job = _create_windows_job()
    else:
        # Linux 独立进程组，便于 TERM/KILL 整个子树
        popen_kwargs["start_new_session"] = True

    start = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except Exception as e:                                # noqa: BLE001
        logger.error("分析子进程启动失败: %s", e)
        result.start_error = str(e)[:500]
        result.returncode = None
        result.cleanup_ok = True    # 未启动，无需回收
        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    result.pid = proc.pid
    if job is not None:
        _assign_to_job(job, proc.pid)

    # 独立线程连续排空 stdout/stderr（内存只留尾部），避免管道塞满死锁
    out_buf, err_buf = deque(), deque()
    lock = threading.Lock()
    t_out = threading.Thread(target=_drain_stream, args=(proc.stdout, out_buf, lock),
                             daemon=True)
    t_err = threading.Thread(target=_drain_stream, args=(proc.stderr, err_buf, lock),
                             daemon=True)
    t_out.start()
    t_err.start()

    try:
        proc.wait(timeout=timeout)
        result.timed_out = False
    except subprocess.TimeoutExpired:
        result.timed_out = True
        logger.warning("分析子进程超时(%ss)，开始回收 pid=%s", timeout, proc.pid)
        _terminate(proc, is_win, result)
    finally:
        # 等排空线程收尾（有界），再取尾部文本
        t_out.join(timeout=_KILL_REAP_SECONDS)
        t_err.join(timeout=_KILL_REAP_SECONDS)
        with lock:
            result.stdout_tail = b"".join(out_buf).decode("utf-8", errors="replace")
            result.stderr_tail = b"".join(err_buf).decode("utf-8", errors="replace")
        result.returncode = proc.returncode
        result.duration_ms = int((time.monotonic() - start) * 1000)
        # 关闭 Job Object（KILL_ON_JOB_CLOSE 终止任何残留子树）
        _close_windows_job(job)
        # 只有确认退出（returncode 非 None）才算清理成功
        result.cleanup_ok = proc.returncode is not None
        if not result.cleanup_ok:
            logger.error("分析子进程 pid=%s 无法确认退出，转故障处置（不宣称已清理）",
                         proc.pid)
    return result
