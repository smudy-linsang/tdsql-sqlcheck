"""连接领域异常（v1.6.2.2-UAT-O-19 / O-24）

把 pymysql 底层异常归一为稳定的领域语义，API 层据此映射为可读的 4xx，
而不是让原始异常穿透成裸 500（离线实例跑巡检曾返回 Internal Server Error，
前端只能显示 HTTP 500，无法给出连接失败的真实原因）。

约定：
- 领域异常只描述"连接/认证/库/monitordb 不可用"这类可预期的环境问题；
- v1.6.2.2-UAT-O-24：translate_db_error 是严格白名单映射——仅识别明确的
  MySQL/TDSQL errno 或稳定错误短语；未知异常返回 None，调用端必须原样抛出，
  由统一 500 处理器记录完整堆栈并返回 X-Request-ID，绝不伪装成连接失败
  （否则 RuntimeError 等代码缺陷会被误报为 422，破坏监控与故障定位）。
- v1.6.2.2-UAT-O-25：双白名单——errno 只从可信驱动异常链提取；消息兜底
  只在异常类型属于连接异常族时才启用。RuntimeError/AttributeError/TypeError 等
  程序异常即使消息碰巧含 "can't connect" 等短语也一律返回 None。
- v1.6.2.2-UAT-O-28：OSError 全家族不再视为可信连接异常——
  PermissionError/FileNotFoundError 继承 OSError，消息含 access denied/
  unknown database 时会被伪装成数据库认证/库不存在。收窄为：文本兜底仅用于
  可信驱动异常；内建网络异常只认精确类型（ConnectionRefused/Reset/Aborted/
  TimeoutError）或 OSError.errno 属于明确网络错误码集合（ECONNREFUSED/
  ETIMEDOUT/EHOSTUNREACH/ECONNRESET/ENETUNREACH 及 Windows 对应码）。
"""
import errno as _errno_mod
from typing import Optional

# MySQL/TDSQL 稳定错误码白名单（errno-first，不做消息模糊包含）
_ERRNO_CONNECTION = {2003, 2004, 2005, 2006, 2013}   # 无法连接/拒绝/主机未知/连接丢失/超时
_ERRNO_AUTH = {1045}                                  # 认证失败
_ERRNO_DB_NOT_FOUND = {1049}                          # 库不存在

try:
    import pymysql
    _DRIVER_ERROR_TYPES = (
        pymysql.err.OperationalError,   # 2003/1045/1049 等均承载于此
        pymysql.err.InterfaceError,     # 驱动接口层连接失败
        pymysql.err.InternalError,
    )
except Exception:                                     # 驱动缺失时消息兜底仍可工作
    _DRIVER_ERROR_TYPES = ()

# 内建网络异常的明确类型（类型本身即语义，无需文本匹配）
_NET_EXACT_TYPES = (
    ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError, TimeoutError,
)

# 明确网络错误码集合（POSIX + Windows WSA）：仅这些 errno 的 OSError 视为连接问题
_NETWORK_ERRNOS = {
    _errno_mod.ECONNREFUSED, _errno_mod.ETIMEDOUT, _errno_mod.EHOSTUNREACH,
    _errno_mod.ECONNRESET, _errno_mod.ENETUNREACH,
    10054, 10060, 10061, 10065,  # WSAECONNRESET / WSAETIMEDOUT / WSAECONNREFUSED / WSAEHOSTUNREACH
}


class InstanceConnectionError(Exception):
    """实例连接环境类错误的基类（可预期、应以 4xx 呈现）"""

    default_message = "实例连接失败"

    def __init__(self, message: str = "", cause: BaseException = None):
        super().__init__(message or self.default_message)
        self.cause = cause


class ConnectionRefusedError_(InstanceConnectionError):
    """目标拒绝连接 / 网络不可达 / 超时"""
    default_message = "目标实例拒绝连接或网络不可达"


class AuthenticationFailedError(InstanceConnectionError):
    """用户名/口令错误或被拒绝"""
    default_message = "实例认证失败（用户名/口令错误）"


class DatabaseNotFoundError(InstanceConnectionError):
    """目标库不存在"""
    default_message = "目标数据库不存在"


class MonitorDbUnavailableError(InstanceConnectionError):
    """monitordb 不可用"""
    default_message = "monitordb 不可用"


def _errno_of(exc: BaseException) -> Optional[int]:
    """从可信驱动异常链提取 MySQL/TDSQL errno（v1.6.2.2-UAT-O-25）。

    只沿 __cause__/__context__ 链提取 PyMySQL 驱动异常 args 中的整数 errno；
    任意 BaseException.args 中的整数不得被直接认定为 errno。
    """
    cur: Optional[BaseException] = exc
    for _ in range(8):  # 防御：限制链遍历深度
        if cur is None:
            break
        if _DRIVER_ERROR_TYPES and isinstance(cur, _DRIVER_ERROR_TYPES):
            for arg in getattr(cur, "args", ()):
                if isinstance(arg, int) and not isinstance(arg, bool):
                    return arg
        cur = cur.__cause__ if cur.__cause__ is not None else cur.__context__
    return None


def translate_db_error(exc: BaseException) -> Optional[InstanceConnectionError]:
    """严格双白名单翻译：已知连接类错误 → 领域异常；未知异常 → None（不得伪装）。

    判定顺序（v1.6.2.2-UAT-O-28）：
    1. errno 优先（仅从可信驱动异常链提取，见 _errno_of）；
    2. 文本兜底仅用于可信驱动异常——access denied / unknown database 等短语
       不得用于泛化 OSError（PermissionError/FileNotFoundError 会继承 OSError，
       文件/密钥/权限类程序错误携带这些短语时不得伪装成数据库错误）；
    3. 内建网络异常只认精确类型（ConnectionRefused/Reset/Aborted/TimeoutError）
       或 OSError.errno 属于明确网络错误码集合；
    4. RuntimeError/AttributeError/TypeError 等程序异常一律返回 None。
    """
    errno = _errno_of(exc)
    if errno in _ERRNO_CONNECTION:
        return ConnectionRefusedError_(str(exc), cause=exc)
    if errno in _ERRNO_AUTH:
        return AuthenticationFailedError(str(exc), cause=exc)
    if errno in _ERRNO_DB_NOT_FOUND:
        return DatabaseNotFoundError(str(exc), cause=exc)

    # 文本兜底：仅可信驱动异常（驱动层错误码缺失环境的兜底）
    if _DRIVER_ERROR_TYPES and isinstance(exc, _DRIVER_ERROR_TYPES):
        text = str(exc).lower()
        if "access denied" in text:
            return AuthenticationFailedError(str(exc), cause=exc)
        if "unknown database" in text:
            return DatabaseNotFoundError(str(exc), cause=exc)
        if "can't connect" in text or "connection refused" in text or "timed out" in text:
            return ConnectionRefusedError_(str(exc), cause=exc)
        return None

    # 内建网络异常：精确类型本身就是语义，无需文本匹配
    if isinstance(exc, _NET_EXACT_TYPES):
        return ConnectionRefusedError_(str(exc), cause=exc)

    # 其余 OSError：仅当 errno 属于明确网络错误码集合；
    # PermissionError / FileNotFoundError 及其他泛化 OSError 一律 None。
    if isinstance(exc, OSError):
        if exc.errno in _NETWORK_ERRNOS:
            return ConnectionRefusedError_(str(exc), cause=exc)
        return None

    return None
