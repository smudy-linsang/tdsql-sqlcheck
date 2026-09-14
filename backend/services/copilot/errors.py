# -*- coding: utf-8 -*-
"""v1.6.4.0 AI Copilot — 错误码闭集与固定错误字典（CP-W01，DETAIL §12.7）。

错误形状固定：
    {"detail": {"code": ..., "message": <本地字典>, "request_id": ..., "retryable": ...}}

message 仅取本地错误字典，禁止透传数据库、httpx 或模型供应商原始错误体。
新增错误码必须同步更新本字典、§12.7 合同与测试。
"""
from __future__ import annotations

from typing import Optional


class CopilotError(Exception):
    """Copilot 业务错误（稳定 code + 本地中文 message + HTTP 状态）。

    http_status 不传时按 ERROR_TABLE 闭集表取值（如 404/409/422/503）。
    """

    def __init__(self, code: str, http_status: Optional[int] = None,
                 retryable: Optional[bool] = None, message: Optional[str] = None):
        self.code = code
        table_status, table_msg, table_retry = ERROR_TABLE.get(
            code, (500, "请求无法处理", False))
        self.http_status = http_status if http_status is not None else table_status
        self.retryable = retryable if retryable is not None else table_retry
        self.message = message or table_msg
        super().__init__(f"{code}: {self.message}")


# ══════════════════════════════════════════════════════════════════
# §12.7 错误码闭集 → (HTTP状态, 本地消息, retryable)
# ══════════════════════════════════════════════════════════════════

ERROR_TABLE: dict[str, tuple[int, str, bool]] = {
    # 401
    "AUTH_REQUIRED": (401, "需要重新登录后才能继续使用助手", False),
    # 403
    "FORBIDDEN": (403, "当前账号无权使用该功能", False),
    "INSTANCE_NOT_GRANTED": (403, "该实例未授予 Copilot 资料使用权", False),
    "SOURCE_FORBIDDEN": (403, "无权读取所选来源资料", False),
    # 404
    "NOT_FOUND": (404, "对象不存在或无权访问", False),
    # 409
    "SESSION_BUSY": (409, "该会话有正在处理的提问，请等待完成或先停止", False),
    "SESSION_ARCHIVED": (409, "会话已归档，不再接受新提问", False),
    "SESSION_REVISION_CHANGED": (409, "会话状态已变化，请刷新后重试", False),
    "IDEMPOTENCY_CONFLICT": (409, "相同提交标识对应不同请求内容，请核对后重新发起", False),
    "PREVIEW_CONSUMED": (409, "该资料预览已被使用，请重新预览", False),
    "PREVIEW_EXPIRED": (409, "资料预览已过期，请重新预览", False),
    "CONTEXT_CHANGED": (409, "资料或授权已变化，请重新预览", False),
    "CONFIG_CHANGED": (409, "配置已被他人修改，请刷新获取最新版本后重试", False),
    "RESULT_NOT_READY": (409, "结果尚未生成，请继续等待", True),
    # 413
    "REQUEST_TOO_LARGE": (413, "请求内容超过大小限制", False),
    # 422
    "INVALID_REQUEST": (422, "请求参数不合法", False),
    "SOURCE_REQUIRED": (422, "本场景需要选择对应的来源资料", False),
    "INPUT_SENSITIVE": (422, "输入内容疑似包含凭据等敏感信息，请删除后再试", False),
    "CONTEXT_TOO_LARGE": (422, "所选资料超出单次处理上限，请缩小范围", False),
    "PROVIDER_CONFIG_INVALID": (422, "模型提供方配置不合法", False),
    # 429
    "RATE_LIMITED": (429, "请求过于频繁，请稍后重试", True),
    "CAPACITY_EXHAUSTED": (429, "当前并发任务已满，请稍后重试", True),
    "QUOTA_EXHAUSTED": (429, "当日调用额度已用尽", False),
    "STORAGE_QUOTA_EXHAUSTED": (429, "助手存储空间已满，请联系管理员", False),
    # 503
    "COPILOT_SCHEMA_UNAVAILABLE": (503, "助手模块结构验收未通过或正在恢复，请稍后重试", True),
    "COPILOT_DISABLED": (503, "助手功能未启用", False),
    "RUNNER_UNAVAILABLE": (503, "助手执行器不可用，请稍后重试", True),
    "COPILOT_CRYPTO_UNAVAILABLE": (503, "助手加密组件不可用", False),
    "STORAGE_UNAVAILABLE": (503, "助手存储不可用", False),
    "POLICY_UNAVAILABLE": (503, "助手出站策略配置不可用", False),
    # 终态错误码（turn.state=FAILED 的 reason）
    "QUEUE_TIMEOUT": (200, "排队超时，未能及时执行", False),
    "TURN_TIMEOUT": (200, "处理超时", False),
    "EXECUTOR_INTERRUPTED": (200, "执行中断（执行器重启或故障恢复）", False),
    "AUTH_REVOKED": (200, "处理过程中授权被撤销", False),
    # 模型原因码
    "PROVIDER_CONNECT_FAILED": (200, "模型端点连接失败", False),
    "PROVIDER_TIMEOUT": (200, "模型响应超时，未自动重新发送", False),
    "PROVIDER_RATE_LIMITED": (200, "模型端点限流", False),
    "PROVIDER_UNAVAILABLE": (200, "模型端点暂不可用", False),
    "PROVIDER_AUTH_FAILED": (200, "模型端点认证失败", False),
    "PROVIDER_REQUEST_REJECTED": (200, "模型端点拒绝请求", False),
    "PROVIDER_TLS_FAILED": (200, "模型端点 TLS 校验失败", False),
    "EGRESS_DENIED": (200, "出站策略不允许本次发送", False),
    # 资料/输出原因码
    "EVIDENCE_UNAVAILABLE": (200, "所选资料不可用或已删除", False),
    "KNOWLEDGE_UNAVAILABLE": (200, "本地知识库不可用", False),
    "KNOWLEDGE_BUNDLE_STALE": (200, "知识包版本不兼容或已过期", False),
    "OUTPUT_INVALID": (200, "模型输出未通过校验", False),
    "OUTPUT_TRUNCATED": (200, "模型输出被截断", False),
    "OUTPUT_SENSITIVE": (200, "模型输出包含不允许的内容", False),
    "TEXT_VALIDATION_TIMEOUT": (200, "候选 SQL 文本复核超时", False),
    # 500
    "INTERNAL_ERROR": (500, "内部错误，请凭追踪号联系管理员", False),
}

#: code → 本地消息（CopilotError.message 缺省来源）
ERROR_MESSAGES: dict[str, str] = {k: v[1] for k, v in ERROR_TABLE.items()}


def http_status_of(code: str) -> int:
    return ERROR_TABLE.get(code, (500, "", False))[0]


def retryable_of(code: str) -> bool:
    return ERROR_TABLE.get(code, (500, "", False))[2]


def error_payload(code: str, request_id: str, message: Optional[str] = None) -> dict:
    """构造 §12.1 固定错误形状。"""
    return {
        "detail": {
            "code": code,
            "message": message or ERROR_MESSAGES.get(code, "请求无法处理"),
            "request_id": request_id,
            "retryable": retryable_of(code),
        }
    }
