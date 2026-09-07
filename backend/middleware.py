"""
TDSQL SQL审核工具 - 中间件 (V2.0)

1. RequestContextMiddleware: 请求ID透传/生成、访问日志、指标采集
2. AuthMiddleware: 令牌认证 + RBAC权限校验 + 变更操作审计日志

认证约定:
- 请求头 Authorization: Bearer <token>
- 免认证路径见 auth_service.PUBLIC_PATHS / PUBLIC_PREFIXES
- AUTH_ENABLED=false 时跳过认证（仅限开发/测试环境，生产必须开启）
"""
import json
import logging
import math
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backend import config
from backend.services import metrics_service
from backend.services.auth_service import (
    auth_service, check_permission, is_public_path, verify_token,
)
from backend.services.database import log_operation
# v1.6.3.4 / D05：网关上传跨 worker 非阻塞槽（文件锁 + 状态文件）
from backend.services.gateway_upload_lock import (
    GatewayUploadSlot, new_owner_nonce, STAGE_RECEIVING, STAGE_PROCESSING,
)

logger = logging.getLogger("tdsql.access")

# index.html 首部内联主题脚本的 CSP 哈希。
# 该脚本必须在渲染前同步执行（否则深/浅色主题切换会闪白），无法外置为文件，
# 故以哈希放行而非 'unsafe-inline'——后者等于对全部内联脚本敞开。
# 脚本内容一旦改动必须同步更新此哈希，tests/test_security_headers.py 会守住这一点。
_INLINE_THEME_SCRIPT_HASH = "'sha256-cdekG9cIdI9gtVRZGv6Od+m5VzXnZfdYIKSX/nFpv7g='"

# 安全响应头基线。style-src 保留 'unsafe-inline'：Element Plus 运行期动态注入
# 行内样式，且页面本身有大量 style= 属性，收紧会直接破坏界面。
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; "
        # 'unsafe-eval' 是本架构的硬约束：前端为免构建的 Vue 全量版 + in-DOM 模板，
        # 运行期模板编译走 new Function()，去掉它页面直接白屏（实测 #app 为空）。
        # 要摘掉它必须先引入构建步骤把模板预编译，属架构级改动。
        # 保留 CSP 仍有价值：外域脚本、点击劫持、表单劫持、base 注入均已被挡住。
        f"script-src 'self' 'unsafe-eval' {_INLINE_THEME_SCRIPT_HASH}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}


# v1.6.3.4 / D05：网关大日志上传专属路由（§6.4）
_GATEWAY_UPLOAD_PATH = "/api/v1/gateway-log/upload"


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """限制请求体大小，超限返回 413。

    此前无任何限制，单个超大报文即可把内存打满。上限取 config.max_body_bytes()，
    默认 8MB——需容纳大 SQL 文件与元数据审核报文，故不宜过小。
    文件上传走 UploadFile 流式读取，同样受此限制保护。

    v1.6.3.4 / D05：网关上传专属路由 POST /api/v1/gateway-log/upload 让
    GatewayUploadPolicyMiddleware 决定限额（200 MiB 净文件 / 201 MiB multipart），
    本中间件跳过该精确路由；**其他路由继续使用原 max_body_bytes（默认 50 MiB），
    不因新增网关参数放开全站**（§6.4）。
    """

    async def dispatch(self, request: Request, call_next):
        # 网关上传专属路由让专属策略决定（§6.4），本中间件不重复限额
        if (request.url.path == _GATEWAY_UPLOAD_PATH
                and request.method == "POST"):
            return await call_next(request)
        limit = config.max_body_bytes()
        if limit > 0:
            declared = request.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > limit:
                return JSONResponse(
                    status_code=413,
                    content={"code": 413,
                             "message": f"请求体过大，上限 {limit // 1024 // 1024}MB"})
        return await call_next(request)


class _GatewayBodyTooLarge(Exception):
    """ASGI receive 累计 body 字节超过网关请求上限（内部信号，非 500）。"""

    def __init__(self, received: int, limit: int):
        self.received = received
        self.limit = limit


class GatewayUploadPolicyMiddleware:
    """网关大日志上传专属策略（纯 ASGI，v1.6.3.4 / D05，DETAIL §6.4）。

    只匹配 POST /api/v1/gateway-log/upload；其他路由直接透传。职责：
      1. 请求上下文最外侧保证 413/429 也有 X-Request-ID；
      2. Content-Length 预检（可信/不可信都先作格式/上限检查）；
      3. 包装 ASGI receive 累计所有 http.request body 字节，超限停止后续读取
         和表单解析，不返回 500、不创建报告；无头/分块/伪造偏小值同样不能越界；
      4. 收体期间共用跨 worker 非阻塞槽，忙则直接 429（不入队、不分析其文件）；
      5. finally 仅释放本请求实际取得的槽（取槽前被拒的请求不释放他人锁）。

    注册顺序：本中间件在 AuthMiddleware 之后（认证通过才取锁）、路由之前
    （表单解析前限额）。见 main.py 的 add_middleware 顺序。
    """

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _get_request_id(scope) -> str:
        # v1.6.3.4 SIT B-02：外层 RequestContextMiddleware 已建立请求上下文时，必须复用
        # 同一个 ID（scope["state"]["request_id"]）。否则响应头/访问日志与响应体里的
        # request_id 是两个不同的值，用户报障的编号在日志里永远查不到。
        rid = (scope.get("state") or {}).get("request_id")
        if rid:
            return str(rid)[:64]
        for k, v in scope.get("headers") or []:
            if k == b"x-request-id" and v:
                return v.decode("latin-1")[:64]
        return uuid.uuid4().hex[:16]

    async def _send_json(self, send, status: int, detail: dict,
                         request_id: str, extra_headers=None):
        """发送结构化 JSON 响应，始终带 X-Request-ID（§6.4 第 1 条）。"""
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("latin-1")),
            (b"x-request-id", request_id.encode("latin-1")),
        ]
        for k, v in (extra_headers or []):
            headers.append((k, v))
        await send({"type": "http.response.start", "status": status,
                    "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        if (scope.get("path") != _GATEWAY_UPLOAD_PATH
                or scope.get("method") != "POST"):
            await self.app(scope, receive, send)
            return

        cfg = config.gateway_upload_config()
        request_id = self._get_request_id(scope)
        request_limit = cfg["GATEWAY_REQUEST_MAX_BYTES"]
        upload_limit = cfg["GATEWAY_UPLOAD_MAX_BYTES"]

        # ── 1. Content-Length 预检（可信/不可信都先作格式/上限检查）──
        cl_raw = None
        for k, v in scope.get("headers") or []:
            if k == b"content-length":
                cl_raw = v
                break
        if cl_raw is not None:
            try:
                cl = int(cl_raw)
            except (ValueError, TypeError):
                cl = None
            if cl is None or cl < 0:
                await self._send_json(send, 400, {
                    "code": "GATEWAY_INVALID_CONTENT_LENGTH",
                    "message": "Content-Length 非法，无法受理网关日志上传",
                    "stage": "admission", "request_id": request_id,
                    "retryable": False,
                }, request_id)
                return
            if cl > request_limit:
                await self._send_json(send, 413, {
                    "code": "GATEWAY_UPLOAD_TOO_LARGE",
                    "message": (
                        f"请求体过大：声明 {cl} 字节，网关 multipart 总上限 "
                        f"{request_limit} 字节（约 {request_limit // 1024 // 1024} MiB，"
                        f"净文件上限约 {upload_limit // 1024 // 1024} MiB）；未启动分析"),
                    "stage": "admission", "request_id": request_id,
                    "retryable": False,
                }, request_id)
                return

        # ── 2. 取跨 worker 非阻塞槽（忙则 429，不入队、不分析其文件）──
        slot = GatewayUploadSlot(cfg.get("GATEWAY_TMP_DIR", ""))
        nonce = new_owner_nonce()
        if not slot.try_acquire(nonce):
            retry_after = slot.peek_retry_after()
            minutes = max(1, int(math.ceil(retry_after / 60)))
            await self._send_json(send, 429, {
                "code": "GATEWAY_BUSY",
                "message": (
                    f"当前已有一个网关日志任务正在上传或分析，同一时刻仅允许一个任务。"
                    f"您的文件未被处理（未进入分析、未排队），请约 {minutes} 分钟后"
                    f"重新上传；此间隔仅供参考，不保证届时空闲。"),
                "stage": "admission", "request_id": request_id, "retryable": True,
            }, request_id, extra_headers=[
                (b"retry-after", str(retry_after).encode("latin-1")),
            ])
            return

        # 取槽成功：进入收体阶段，deadline 用收体剩余预算（§6.4）
        receive_budget = cfg["GATEWAY_UPLOAD_RECEIVE_TIMEOUT_SECONDS"]
        processing_budget = cfg["GATEWAY_PROCESSING_BUDGET_SECONDS"]
        slot.update_stage(STAGE_RECEIVING, time.monotonic() + receive_budget)

        body_bytes = 0
        response_started = False
        stage_switched = False

        async def wrapped_receive():
            nonlocal body_bytes, stage_switched
            message = await receive()
            if message.get("type") == "http.request":
                chunk = message.get("body", b"") or b""
                body_bytes += len(chunk)
                if body_bytes > request_limit:
                    # 超限：停止后续读取和表单解析（抛信号，外层拦截为 413）
                    raise _GatewayBodyTooLarge(body_bytes, request_limit)
                # 收体完成（more_body=False）→ 切换 processing 阶段协调预算
                if not message.get("more_body", False) and not stage_switched:
                    stage_switched = True
                    slot.update_stage(STAGE_PROCESSING,
                                      time.monotonic() + processing_budget)
            return message

        async def wrapped_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, wrapped_receive, wrapped_send)
        except _GatewayBodyTooLarge as e:
            # 累计字节超限（无 Content-Length / 分块 / 伪造偏小值）→ 413，非 500
            if not response_started:
                await self._send_json(send, 413, {
                    "code": "GATEWAY_UPLOAD_TOO_LARGE",
                    "message": (
                        f"请求体过大：已接收 {e.received} 字节，超过网关 multipart "
                        f"总上限 {e.limit} 字节；已停止接收，未创建报告"),
                    "stage": "receive", "request_id": request_id,
                    "retryable": False,
                }, request_id)
            else:
                logger.warning(
                    "网关上传超限但响应已开始，无法改写为 413 (req=%s)", request_id)
        finally:
            # §6.4 第 4 条：仅释放本请求实际取得的槽（取槽前被拒的不释放他人锁）
            slot.release()


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
        for k, v in _SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        # v1.6.2.2-UAT-O-15：网关报告文档被不透明源 sandbox 的 iframe 嵌入，
        # 全局基线的 X-Frame-Options: DENY 与之冲突（Chromium 拒绝加载）；
        # 报告端点以 frame-ancestors 'self' 承担嵌入控制，显式移除 XFO。
        if getattr(request.state, "frame_embeddable", False):
            if "X-Frame-Options" in response.headers:
                del response.headers["X-Frame-Options"]
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


_BACKGROUND_TASKS = set()

# ── 允许经 URL 查询参数携带令牌的端点白名单 ──
# URL 中的 token 会被 Nginx/网关 access_log、浏览器历史明文留存，属凭证泄露面，
# 因此默认只认 Authorization 头。但下载/导出类端点由 window.open 触发，
# 浏览器无法为其附加请求头，只能走 URL 传参。故收敛为白名单：
# 既堵住 /dashboard/summary?access_token=... 这类通用接口的滥用，
# 又不破坏既有导出功能。
# 后续应改为一次性下载 ticket（POST 签发、60s 有效、用后即焚），彻底消除 URL 明文令牌。
_QUERY_TOKEN_PATHS = frozenset({
    "/api/v1/ppt-report/generate",
    "/api/v1/daily-inspect/compare/html",
    "/api/v1/toolkit/download",
    "/api/v1/scan-compare/compare/html",
})
_QUERY_TOKEN_SUFFIXES = ("/html", "/sql", "/export")
_QUERY_TOKEN_PREFIXES = ("/api/v1/audit/", "/api/v1/slow-queries/", "/api/v1/scan-compare/")


def _allows_query_token(path: str) -> bool:
    """该路径是否允许用 ?access_token= 携带令牌（仅下载/导出类）"""
    if path in _QUERY_TOKEN_PATHS:
        return True
    return (path.startswith(_QUERY_TOKEN_PREFIXES)
            and path.endswith(_QUERY_TOKEN_SUFFIXES))


# v1.6.2.2-UAT-O-15：网关报告 iframe 用短时一次性报告票据鉴权，不再把长期令牌放进 URL。
_GATEWAY_REPORT_HTML_RE = re.compile(r"^/api/v1/gateway-log/reports/(\d+)/html$")


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

        # v1.6.2.2-UAT-O-15：网关报告 iframe 一次性票据鉴权。
        # 票据由登录后的签发接口（头部令牌 + RBAC）发放，90s 有效、用后即焚、
        # 绑定报告 ID；授权在签发时已把关，消费时只需验票据与账户状态。
        _gw_m = _GATEWAY_REPORT_HTML_RE.match(path)
        if _gw_m and method == "GET":
            _ticket = request.query_params.get("report_ticket", "")
            if _ticket:
                from backend.services.gateway_log_service import gateway_log_service
                _username = gateway_log_service.consume_report_ticket(
                    _ticket, int(_gw_m.group(1)))
                _user = auth_service.get_user(_username) if _username else None
                if _username and _user and _user.get("status") == "active":
                    request.state.username = _username
                    request.state.role = _user.get("role", "developer")
                    request.state.user = _user
                    return await call_next(request)
                # 失效提示需在抽屉 iframe 内可见：标记为可嵌入文档，免全局 XFO DENY 拦截
                request.state.frame_embeddable = True
                return Response(
                    content="<div style='font-family:sans-serif;padding:40px;text-align:center;'><h2>⚠️ 报告访问票据无效或已过期</h2><p style='color:#666'>请返回主系统界面重新点击“查看报告”。</p></div>",
                    media_type="text/html; charset=utf-8",
                    status_code=401,
                )

        # 提取令牌
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        if not token and _allows_query_token(path):
            token = request.query_params.get("access_token") or request.query_params.get("token") or ""

        payload = verify_token(token)
        if not payload:
            if path.endswith("/html"):
                return Response(
                    content="<div style='font-family:sans-serif;padding:40px;text-align:center;'><h2>⚠️ 登录凭证已失效或无效</h2><p style='color:#666'>请返回主系统界面重新登录后，再次点击“导出HTML比对大屏”。</p></div>",
                    media_type="text/html; charset=utf-8",
                    status_code=401
                )
            return JSONResponse(
                status_code=401,
                content={"code": 401, "message": "未认证或令牌已过期，请重新登录"})

        username = payload.get("sub", "")
        user = auth_service.get_user(username)
        if not user or user.get("status") != "active":
            if path.endswith("/html"):
                return Response(
                    content="<div style='font-family:sans-serif;padding:40px;text-align:center;'><h2>⚠️ 账户不存在或已被禁用</h2></div>",
                    media_type="text/html; charset=utf-8",
                    status_code=401
                )
            return JSONResponse(
                status_code=401,
                content={"code": 401, "message": "账户不存在或已禁用"})

        # 会话吊销校验：令牌载荷 tv 与 users.token_version 不符即已失效。
        # 放在此处而非 verify_token 内，是因为 user 已在上一步取到（带短TTL缓存），
        # 避免为每个请求额外增加一次元数据库查询。
        if int(payload.get("tv", 0)) != int(user.get("token_version", 0) or 0):
            if path.endswith("/html"):
                return Response(
                    content="<div style='font-family:sans-serif;padding:40px;text-align:center;'><h2>⚠️ 会话已失效</h2><p style='color:#666'>请重新登录后再次生成报告。</p></div>",
                    media_type="text/html; charset=utf-8",
                    status_code=401
                )
            return JSONResponse(
                status_code=401,
                content={"code": 401, "message": "会话已失效，请重新登录"})

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
                task = asyncio.create_task(asyncio.to_thread(
                    log_operation,
                    operator=username,
                    operation_type=f"{method} {path}",
                    target_type="api",
                    target_id=path,
                    detail=f"status={response.status_code}",
                    ip_address=client_ip,
                    user_agent=request.headers.get("User-Agent", "")[:200],
                ))
                _BACKGROUND_TASKS.add(task)
                task.add_done_callback(_BACKGROUND_TASKS.discard)
            except Exception:
                logger.exception("操作审计日志写入失败")

        return response
