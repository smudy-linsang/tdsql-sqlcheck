# -*- coding: utf-8 -*-
"""元数据审核任务的资源护栏与进程管理（v1.6.3.5 / DU-2 / FIX-02 / D06）。

设计出处：docs/DETAIL-v1.6.3.5-大库在线元数据审核稳定性修复.md §5.2/§5.4。

实现设计 §5.2 的全部参数与 §5.4 的资源预检：
  · 10 个 METADATA_* 参数（含范围校验，越界 fail-closed）；
  · child RSS 采样（Linux 读 /proc/<pid>/status VmRSS；Windows 经 ctypes）；
  · 磁盘可用空间受理前置检查（statvfs f_bavail / shutil.disk_usage）；
  · 25% MemTotal 启动硬校验（rss_limit*4 > 有效容量 → 拒绝 runner 启动，fail-closed）。

跨平台：Linux 用 /proc；Windows 本机开发经 ctypes（GlobalMemoryStatusEx 读物理总量、
读不到 child RSS 则返回 None → 该采样项跳过而非误判）。
"""

import ctypes
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("tdsql.metadata_job_process")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# ── UAT-M3：PyMySQL/TDSQL 常见错误码语义化（运维/用户可读）──────────────────
_DB_ERR_HINTS = [
    ("2013", "目标数据库连接中断（请检查网络、防火墙、目标 TDSQL 实例是否存活）"),
    ("2003", "无法连接目标数据库（请检查实例地址、端口与网络连通性）"),
    ("2002", "无法连接目标数据库（目标实例未监听或网络不通）"),
    ("2006", "目标数据库连接被中断（server has gone away，可能超时或实例重启）"),
    ("1045", "目标数据库鉴权失败（请检查连接账号与密码）"),
    ("1044", "目标数据库账号无权访问该库（请检查账号授权）"),
    ("1049", "目标数据库不存在（请检查 database/库名配置）"),
    ("1146", "目标数据库中对象不存在（表/视图可能已被删除或库名有误）"),
    ("1213", "目标数据库发生死锁（可稍后重试）"),
    ("1205", "目标数据库锁等待超时（可稍后重试）"),
]


def humanize_db_error(text: str) -> str:
    """把 PyMySQL 原始错误（如 `(2013, 'Lost connection...')`）映射为可读中文提示。

    命中已知错误码则在原文前补一句中文诊断；未命中原样返回（不丢信息）。
    """
    import re
    s = str(text or "")
    m = re.search(r"\((\d{4})\s*,", s)
    if m:
        code = m.group(1)
        for c, hint in _DB_ERR_HINTS:
            if c == code:
                return f"{hint}。原始错误: {s}"
    return s


# ── 参数表（设计 §5.2 默认值 + 允许范围）────────────────────────────────
class MetadataLimits:
    MAX_CONCURRENT = 1  # 固定 1，其他值启动拒绝
    JOB_TIMEOUT = _env_int("METADATA_JOB_TIMEOUT_SECONDS", 1800)         # 300-7200
    START_TIMEOUT = _env_int("METADATA_START_TIMEOUT_SECONDS", 30)       # 固定 30
    CHILD_RSS_LIMIT_MIB = _env_int("METADATA_CHILD_RSS_LIMIT_MIB", 1024)  # 512-4096
    SQL_MAX_MIB = _env_int("METADATA_SQL_MAX_MIB", 256)                  # 16-1024
    RESULTS_MAX_MIB = _env_int("METADATA_RESULTS_MAX_MIB", 256)          # 16-1024
    ARTIFACT_MAX_MIB = _env_int("METADATA_ARTIFACT_MAX_MIB", 1024)       # 64-4096
    ARTIFACT_TOTAL_MAX_MIB = _env_int("METADATA_ARTIFACT_TOTAL_MAX_MIB", 10240)  # 1024-1048576
    MIN_FREE_BYTES = _env_int("METADATA_MIN_FREE_BYTES", 2147483648)     # 2 GiB


def validate_limits() -> list:
    """参数范围校验；返回违规列表（空=合法）。"""
    errs = []
    def chk(name, val, lo, hi):
        if not (lo <= val <= hi):
            errs.append(f"{name}={val} 超出允许范围 [{lo},{hi}]")
    chk("METADATA_JOB_TIMEOUT_SECONDS", MetadataLimits.JOB_TIMEOUT, 300, 7200)
    chk("METADATA_CHILD_RSS_LIMIT_MIB", MetadataLimits.CHILD_RSS_LIMIT_MIB, 512, 4096)
    chk("METADATA_SQL_MAX_MIB", MetadataLimits.SQL_MAX_MIB, 16, 1024)
    chk("METADATA_RESULTS_MAX_MIB", MetadataLimits.RESULTS_MAX_MIB, 16, 1024)
    chk("METADATA_ARTIFACT_MAX_MIB", MetadataLimits.ARTIFACT_MAX_MIB, 64, 4096)
    chk("METADATA_ARTIFACT_TOTAL_MAX_MIB", MetadataLimits.ARTIFACT_TOTAL_MAX_MIB, 1024, 1048576)
    if MetadataLimits.ARTIFACT_TOTAL_MAX_MIB < MetadataLimits.ARTIFACT_MAX_MIB:
        errs.append("METADATA_ARTIFACT_TOTAL_MAX_MIB 必须 >= METADATA_ARTIFACT_MAX_MIB")
    if MetadataLimits.MAX_CONCURRENT != 1:
        errs.append("METADATA_MAX_CONCURRENT 固定为 1（非调优项）")
    if MetadataLimits.MIN_FREE_BYTES <= 0:
        errs.append("METADATA_MIN_FREE_BYTES 必须为正整数")
    return errs


# ── 资源读数（跨平台）────────────────────────────────────────────────────
def host_total_bytes():
    """物理内存总量（字节）。Linux 读 /proc/meminfo MemTotal；Windows 经 ctypes。读不到返回 None。"""
    if os.name == "posix":
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) * 1024   # kB → bytes
        except (OSError, ValueError, IndexError):
            return None
        return None
    # Windows
    try:
        class _MEMSTAT(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        stat = _MEMSTAT()
        stat.dwLength = ctypes.sizeof(_MEMSTAT)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return int(stat.ullTotalPhys)
    except Exception:
        return None
    return None


def read_rss_bytes(pid: int):
    """读子进程 RSS（字节）。Linux 读 /proc/<pid>/status VmRSS；Windows 返回 None（跳过而非误判）。"""
    if os.name != "posix":
        return None
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def disk_free_bytes(path: str):
    """产物所在文件系统的可用空间（字节，以服务用户可用空间计）。读不到返回 None。"""
    try:
        usage = shutil.disk_usage(path)
        return int(usage.free)
    except (OSError, FileNotFoundError):
        return None


def effective_capacity_bytes():
    """有效容量 = min(host_total, 有限 cgroup 限额)。读不到返回 host_total（可能为 None）。"""
    total = host_total_bytes()
    if total is None:
        return None
    cap = total
    if os.name == "posix":
        for cand in ("/sys/fs/cgroup/memory/memory.limit_in_bytes",        # cgroup v1
                     "/sys/fs/cgroup/memory.max"):                          # cgroup v2
            try:
                with open(cand, "r") as f:
                    raw = f.read().strip()
                if raw and raw != "max":
                    lim = int(raw)
                    # 超大无限制哨兵值不算容量
                    if 0 < lim < (1 << 60):
                        cap = min(cap, lim)
            except (OSError, ValueError):
                continue
    return cap


def precheck_runner_start() -> tuple:
    """runner 启动硬校验（设计 §5.4）：25% MemTotal 规则 + 参数范围。

    返回 (ok, reasons)。rss_limit*4 > 有效容量 → 拒绝；读数缺失 fail-closed。
    """
    reasons = []
    errs = validate_limits()
    reasons.extend(errs)
    rss_limit = MetadataLimits.CHILD_RSS_LIMIT_MIB * 1024 * 1024
    cap = effective_capacity_bytes()
    if cap is None:
        reasons.append("无法读取主机物理内存（MemTotal），fail-closed 拒绝启动")
    elif rss_limit * 4 > cap:
        reasons.append(
            f"CHILD_RSS_LIMIT={rss_limit} 字节超过有效容量 {cap} 的 25%，拒绝启动")
    return (len(reasons) == 0), reasons


def check_disk_before_accept(artifact_root: str) -> tuple:
    """受理前置磁盘检查。返回 (ok, code, message)。

    有效门槛 = max(MIN_FREE_BYTES, 单任务产物上限 + 1 GiB)。
    """
    free = disk_free_bytes(artifact_root)
    if free is None:
        return False, "STORAGE_CHECK_UNAVAILABLE", "产物目录核验不可用（读不到可用空间）"
    need = max(MetadataLimits.MIN_FREE_BYTES,
               MetadataLimits.ARTIFACT_MAX_MIB * 1024 * 1024 + 1024 * 1024 * 1024)
    if free < need:
        return False, "INSUFFICIENT_STORAGE", (
            f"产物目录可用空间不足：{free} 字节 < 门槛 {need} 字节")
    return True, None, None


# ── 受管子进程执行（含 RSS 采样 + 取消 + 超时 + stderr 尾部捕获）────────────
class WorkerResult:
    def __init__(self):
        self.returncode = None
        self.timed_out = False
        self.cancelled = False
        self.rss_exceeded = False
        self.cleanup_ok = False
        self.stderr_tail = ""
        self.stdout_tail = ""


def run_metadata_worker(cmd: list, *, timeout: int, env: dict = None,
                        cwd: str = None, cancel_check=None) -> WorkerResult:
    """派生并受管执行元数据审核子进程。

    监督循环每 1 秒：读 child RSS（超限 → 终止并标 RESOURCE_LIMIT）、复核取消意图
    （cancel_check() → 终止并标 CANCELLED）、复核总时限（monotonic 超时 → 终止并标
    JOB_TIMEOUT）。终止统一 TERM→5s→KILL→reap。stdout/stderr 由排空线程各自保留
    尾部（防 PIPE 堵塞），供失败诊断（M-01）。

    Args:
        cancel_check: 可调用，返回 True 表示应取消（None=不检查）。
    """
    import subprocess
    import threading
    import time as _time
    from collections import deque

    res = WorkerResult()
    rss_limit = MetadataLimits.CHILD_RSS_LIMIT_MIB * 1024 * 1024

    # Linux 独立进程组；Windows 由默认句柄管理（kill-on-close 由系统负责）
    popen_kw = {}
    if os.name == "posix":
        popen_kw["start_new_session"] = True

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, cwd=cwd, **popen_kw)
    res.pid = proc.pid

    out_tail, err_tail = deque(maxlen=256), deque(maxlen=256)

    def _drain(stream, dq):
        try:
            for line in iter(stream.readline, ""):
                dq.append(line)
        except Exception:
            pass

    t_out = threading.Thread(target=_drain, args=(proc.stdout, out_tail), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, err_tail), daemon=True)
    t_out.start(); t_err.start()

    deadline = _time.monotonic() + timeout

    def _terminate():
        """TERM → 5s → KILL → reap。"""
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), 15)   # SIGTERM 到进程组
            else:
                proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                if os.name == "posix":
                    os.killpg(os.getpgid(proc.pid), 9)   # SIGKILL
                else:
                    proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass

    while proc.poll() is None:
        # RSS 采样（Linux；Windows 返回 None 跳过）
        rss = read_rss_bytes(proc.pid)
        if rss is not None and rss > rss_limit:
            res.rss_exceeded = True
            _terminate()
            break
        # 取消意图
        if cancel_check is not None:
            try:
                if cancel_check():
                    res.cancelled = True
                    _terminate()
                    break
            except Exception:
                pass
        # 总时限
        if _time.monotonic() > deadline:
            res.timed_out = True
            _terminate()
            break
        _time.sleep(1.0)

    try:
        res.returncode = proc.wait(timeout=10)
    except Exception:
        _terminate()
        res.returncode = proc.returncode
    t_out.join(timeout=2); t_err.join(timeout=2)
    res.stdout_tail = "".join(out_tail)[-65536:]
    res.stderr_tail = "".join(err_tail)[-65536:]
    res.cleanup_ok = (proc.poll() is not None)
    return res
