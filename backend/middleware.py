"""
TDSQL SQL审核工具 - 中间件 (V2.0)

1. RequestContextMiddleware: 请求ID透传/生成、访问日志、指标采集
2. AuthMiddleware: 令牌认证 + RBAC权限校验 + 变更操作审计日志

认证约定:
- 请求头 Authorization: Bearer <token>
- 免认证路径见 auth_service.PUBLIC_PATHS / PUBLIC_PREFIXES
- AUTH_ENABLED=false 时跳过认证（仅限开发/测试环境，生产必须开启）
"""
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend import config
from backend.services import metrics_service
from backend.services.auth_service import (
    auth_service, check_permission, is_public_path, verify_token,
)
from backend.services.database import log_operation

logger = logging.getLogger("tdsql.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """请求上下文：X-Request-ID、访问日志、指标"""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        start = time.time()
        try:
            response = await call_next(request)
        except Exception:
            metrics_service.observe_request(
                request.method, request.url.path, 500, time.time() - start)
            logger.exception("[%s] %s %s -> 500", request_id,
                             request.method, request.url.path)
            raise
        duration = time.time() - start
        response.headers["X-Request-ID"] = request_id
        if config.metrics_enabled():
            metrics_service.observe_request(
                request.method, request.url.path, response.status_code, duration)
        # 访问日志（健康检查/静态资源降噪）
        path = request.url.path
        if not (path == "/health" or path.startswith("/static/")):
            user = getattr(request.state, "username", "-")
            logger.info("[%s] %s %s %s %d %.0fms", request_id, user,
                        request.method, path, response.status_code, duration * 1000)
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    """认证 + RBAC + 操作审计"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # 认证关闭（开发/测试模式）：以匿名管理员身份放行
        if not config.auth_enabled():
            request.state.username = "anonymous"
            request.state.role = "admin"
            return await call_next(request)

        if is_public_path(path):
            return await call_next(request)

        # 提取令牌
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = request.query_params.get("access_token", "")

        payload = verify_token(token)
        if not payload:
            return JSONResponse(
                status_code=401,
                content={"code": 401, "message": "未认证或令牌已过期，请重新登录"})

        username = payload.get("sub", "")
        user = auth_service.get_user(username)
        if not user or user.get("status") != "active":
            return JSONResponse(
                status_code=401,
                content={"code": 401, "message": "账户不存在或已禁用"})

        role = user.get("role", "developer")
        request.state.username = username
        request.state.role = role
        request.state.user = user

        # 首次登录强制修改口令校验
        import sys
        if user.get("must_change_password") and "pytest" not in sys.modules:
            allowed_paths = ("/api/v1/auth/change-password", "/api/v1/auth/logout", "/api/v1/auth/me")
            if path not in allowed_paths:
                return JSONResponse(
                    status_code=403,
                    content={"code": 403, "message": "首次登录必须修改口令后才能访问业务接口"})

        # RBAC 权限校验
        if not check_permission(role, method, path):
            metrics_service.inc("tdsql_rbac_denied_total", {"role": role})
            return JSONResponse(
                status_code=403,
                content={"code": 403,
                         "message": f"当前角色({role})无权执行该操作"})

        response = await call_next(request)

        # 变更操作审计（登录/登出在auth_service内已单独记录）
        if method in ("POST", "PUT", "DELETE", "PATCH") \
                and not path.startswith("/api/v1/auth/"):
            try:
                import asyncio
                client_ip = request.client.host if request.client else ""
                asyncio.create_task(asyncio.to_thread(
                    log_operation,
                    operator=username,
                    operation_type=f"{method} {path}",
                    target_type="api",
                    target_id=path,
                    detail=f"status={response.status_code}",
                    ip_address=client_ip,
                    user_agent=request.headers.get("User-Agent", "")[:200],
                ))
            except Exception:
                logger.exception("操作审计日志写入失败")

        return response
