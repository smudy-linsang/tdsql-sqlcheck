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
"""
from typing import Optional

# MySQL/TDSQL 稳定错误码白名单（errno-first，不做消息模糊包含）
_ERRNO_CONNECTION = {2003, 2004, 2005, 2006, 2013}   # 无法连接/拒绝/主机未知/连接丢失/超时
_ERRNO_AUTH = {1045}                                  # 认证失败
_ERRNO_DB_NOT_FOUND = {1049}                          # 库不存在


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
    """从驱动异常提取 MySQL/TDSQL errno（pymysql 异常的 args[0] 即 errno）。"""
    for arg in getattr(exc, "args", ()):
        if isinstance(arg, int) and not isinstance(arg, bool):
            return arg
    return None


def translate_db_error(exc: BaseException) -> Optional[InstanceConnectionError]:
    """严格白名单翻译：已知连接类错误 → 领域异常；未知异常 → None（不得伪装）。

    判定顺序：errno 优先（稳定契约），其次精确短语（驱动无关环境的兜底）；
    任何 RuntimeError/AttributeError/TypeError 等程序异常一律返回 None。
    """
    errno = _errno_of(exc)
    if errno in _ERRNO_CONNECTION:
        return ConnectionRefusedError_(str(exc), cause=exc)
    if errno in _ERRNO_AUTH:
        return AuthenticationFailedError(str(exc), cause=exc)
    if errno in _ERRNO_DB_NOT_FOUND:
        return DatabaseNotFoundError(str(exc), cause=exc)

    text = str(exc).lower()
    if "can't connect" in text or "connection refused" in text or "timed out" in text:
        return ConnectionRefusedError_(str(exc), cause=exc)
    if "access denied" in text:
        return AuthenticationFailedError(str(exc), cause=exc)
    if "unknown database" in text:
        return DatabaseNotFoundError(str(exc), cause=exc)
    return None
