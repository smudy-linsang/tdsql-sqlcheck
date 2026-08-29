"""连接领域异常（v1.6.2.2-UAT-O-19）

把 pymysql 底层异常归一为稳定的领域语义，API 层据此映射为可读的 4xx，
而不是让原始异常穿透成裸 500（离线实例跑巡检曾返回 Internal Server Error，
前端只能显示 HTTP 500，无法给出连接失败的真实原因）。

约定：
- 领域异常只描述"连接/认证/库/monitordb 不可用"这类可预期的环境问题；
- 未知程序错误仍应抛原始异常并由 API 层返回 500 + request id，不得掩盖缺陷。
"""


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


def translate_db_error(exc: BaseException) -> InstanceConnectionError:
    """把 pymysql/底层异常翻译为领域异常；不可识别时返回基类（仍属可预期环境问题）。"""
    text = f"{type(exc).__name__}: {exc}".lower()
    if ("can't connect" in text or "connection refused" in text
            or "timed out" in text or "timeout" in text
            or "unreachable" in text or "10061" in text
            or "newconnectionerror" in text):
        return ConnectionRefusedError_(str(exc), cause=exc)
    if "access denied" in text or "1045" in text or "authentication" in text:
        return AuthenticationFailedError(str(exc), cause=exc)
    if "unknown database" in text or "1049" in text:
        return DatabaseNotFoundError(str(exc), cause=exc)
    return InstanceConnectionError(str(exc), cause=exc)
