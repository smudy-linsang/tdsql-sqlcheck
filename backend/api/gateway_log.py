"""G11 网关日志分析 API 路由"""
import asyncio
import hashlib
import logging
import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from typing import List, Optional
from pydantic import BaseModel

from backend import config
from backend.services.gateway_log_service import (
    gateway_log_service, GatewayAnalysisError,
)

logger = logging.getLogger("tdsql.gateway_log.api")

router = APIRouter(prefix="/api/v1/gateway-log", tags=["Gateway Log"])

# v1.6.2.2-UAT-O-15：报告响应的安全闭环（按 O 要求的顺序重构）：
# 1) 数据→脚本上下文：分析器用 _js_json 转义 `<`/`>`/`&`/U+2028/U+2029，
#    日志里的 `</script>` 不再能提前结束脚本元素；
# 2) CSP 去掉 `script-src 'unsafe-inline'`：所有 <script> 改为响应级随机 nonce 放行，
#    模板内联事件处理器（onclick=）已改为 addEventListener，旧报告残留的
#    内联处理器在服务时统一剥离（_INLINE_HANDLER_RE）；
# 3) iframe 策略：保留不透明源 sandbox（前端），删除与之冲突的 X-Frame-Options，
#    嵌入控制只由 frame-ancestors 'self' 承担（同源可嵌、外站拒绝）。

# 剥离旧报告残留的内联事件处理器属性（nonce 制 CSP 下它们也不会生效，
# 主动移除避免误导与潜在绕过面）；模板数据均经 HTML 转义，属性值内不可能出现裸引号。
_INLINE_HANDLER_RE = re.compile(
    r'\s+on[a-zA-Z]+\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)', re.IGNORECASE)

# 脚本元素边界切分：带任意属性的 script 都作为整体保留（拿不到 nonce 即被 CSP 拦截，
# 失败关闭）；内联处理器剥离只作用于脚本块之外的标记文本，避免误伤脚本内已转义数据。
_SCRIPT_ELEMENT_RE = re.compile(r"(<script\b.*?</script>)", re.IGNORECASE | re.DOTALL)

# 脚本元素只匹配裸 `<script>`：带任何属性的 script 拿不到 nonce 即被 CSP 拦截。
_BARE_SCRIPT_RE = re.compile(r"<script>")


def _strip_inline_handlers(html_content: str) -> str:
    """只剥离脚本块之外的内联事件处理器属性（v1.6.2.2-UAT-O-15 服务时加固）。

    脚本块内的数据已由分析器 `_js_json` 转义，`on*=` 只是字符串内容，不是
    HTML 处理器；保留它们即保留了火焰图 SQL 明细的完整可复制性。
    """
    parts = _SCRIPT_ELEMENT_RE.split(html_content)
    for i in range(0, len(parts), 2):
        parts[i] = _INLINE_HANDLER_RE.sub("", parts[i])
    return "".join(parts)


def _report_doc_headers(nonce: str) -> dict:
    """报告文档专用响应头：nonce 制 CSP、不含 unsafe-inline、无 X-Frame-Options"""
    return {
        "Content-Security-Policy": (
            "default-src 'self'; "
            f"script-src 'nonce-{nonce}'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'self'; "
            "base-uri 'none'; "
            "form-action 'none'; "
            "object-src 'none'"
        ),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }

class ReportSummary(BaseModel):
    pass
class ReportItem(BaseModel):
    id: int
    connection_id: str
    log_file_name: str
    log_type: str
    total_queries: int
    slow_queries: int
    max_time_ms: float
    avg_time_ms: float
    created_at: str


@router.post("/upload")
async def upload_log(
    request: Request,
    connection_id: str = Form(...),
    log_type: str = Form("interf"),
    file: UploadFile = File(...),
):
    """上传网关日志并分析（v1.6.3.4 / D06 重构，§6.5—§6.6）。

    入口防护（multipart 总限额/跨 worker 锁/429/413）由 GatewayUploadPolicyMiddleware
    在路由之前完成；本路由负责：1 MiB 块转交受控目录（不全量 read 进内存）+ 净文件
    字节校验 + SHA-256 + 线程池分析（不阻塞事件循环）+ 结构化错误 + 受控目录清理。
    """
    cfg = config.gateway_upload_config()
    upload_max = cfg["GATEWAY_UPLOAD_MAX_BYTES"]
    tmp_root = cfg.get("GATEWAY_TMP_DIR") or None
    request_id = getattr(request.state, "request_id", "") or ""

    # report_context capture（D01）：受理时冻结连接名称（不是 host:port，不是库名）
    report_context = None
    try:
        from backend.services.report_context import capture_report_context, ORIGIN_BOUND
        report_context = capture_report_context(connection_id, "", ORIGIN_BOUND)
    except Exception:                                        # noqa: BLE001
        report_context = None

    # 受控临时目录（仅运行账号可访问；文件名由服务生成，非用户路径）
    try:
        work_dir = Path(tempfile.mkdtemp(prefix="gw_upload_", dir=tmp_root))
    except OSError as e:
        logger.error("网关上传临时目录创建失败: %s", e)
        raise HTTPException(status_code=507, detail={
            "code": "GATEWAY_TEMP_SPACE_LOW",
            "message": "无法创建上传暂存目录（磁盘不足或权限问题）",
            "stage": "admission", "request_id": request_id, "retryable": True})

    net_bytes = 0
    try:
        # 受控文件名：符合 analyze_gateway_log.py 的 <type>_instance_<port>.<date>.<seq>
        port = 0
        try:
            from backend.services.connection_registry import registry
            saved = registry.get_saved(connection_id) or {}
            port = int(saved.get("port") or 0)
        except Exception:                                    # noqa: BLE001
            port = 0
        safe_type = re.sub(r"[^a-z_]", "", (log_type or "interf").lower()) or "interf"
        date_str = datetime.now().strftime("%Y-%m-%d")
        dest = work_dir / f"{safe_type}_instance_{port}.{date_str}.0"

        # 1 MiB 块转交（不全量 read 进内存），累计净字节 + SHA-256
        sha = hashlib.sha256()
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                net_bytes += len(chunk)
                if net_bytes > upload_max:
                    # 净文件超限（与中间件的 multipart 总限额分别判断，§6.3）
                    raise HTTPException(status_code=413, detail={
                        "code": "GATEWAY_UPLOAD_TOO_LARGE",
                        "message": (f"文件净字节超过网关上限 {upload_max} 字节"
                                    f"（约 {upload_max // 1024 // 1024} MiB）；"
                                    f"已停止接收，未启动分析"),
                        "stage": "receive", "request_id": request_id,
                        "retryable": False})
                sha.update(chunk)
                out.write(chunk)
        try:
            await file.close()
        except Exception:                                    # noqa: BLE001
            pass

        # 线程池执行分析（同步文件/子进程/DB 移出事件循环，§6.1 问题 2）
        res = await asyncio.to_thread(
            gateway_log_service.analyze_log,
            connection_id=connection_id,
            file_path=str(dest),
            file_name=file.filename or dest.name,
            log_type=log_type,
            report_context=report_context,
            request_id=request_id,
        )
        return {
            "status": res.get("status", "success"),
            "report_id": res["id"],
            "total_queries": res["total_queries"],
            "slow_queries": res["slow_queries"],
            "max_time_ms": res["max_time_ms"],
            "avg_time_ms": res["avg_time_ms"],
            "request_id": request_id,
            # v1.6.2.2-UAT-O-17：混合输入不得静默丢行——响应携带解析覆盖率
            "parse_quality": res.get("parse_quality"),
        }
    except HTTPException:
        raise
    except GatewayAnalysisError as e:
        # 结构化错误（§6.6）：code/message/stage/request_id/retryable
        raise HTTPException(status_code=e.http_status, detail={
            "code": e.code, "message": e.message, "stage": e.stage,
            "request_id": request_id, "retryable": e.retryable})
    except ValueError as e:
        # v1.6.2.2-UAT-O-11：业务输入错误返回 422（可读失败语义），不落 500
        raise HTTPException(status_code=422, detail={
            "code": "GATEWAY_INVALID_LOG", "message": str(e), "stage": "analyze",
            "request_id": request_id, "retryable": False})
    except Exception:
        logger.exception("网关日志分析未预期异常 (req=%s)", request_id)
        raise HTTPException(status_code=500, detail={
            "code": "GATEWAY_ANALYZER_FAILED",
            "message": "网关日志分析内部错误，请携带请求编号联系管理员排查",
            "stage": "analyze", "request_id": request_id, "retryable": False})
    finally:
        # 清理本请求受控目录（输入/报告/summary）；不动其他任务目录
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:                                    # noqa: BLE001
            pass


@router.get("/capabilities")
def get_capabilities():
    """网关上传能力与限额（只读，v1.6.3.4 / D05，DETAIL §6.3/§6.4 第 5 条）。

    前端只作友好预检，后端（GatewayUploadPolicyMiddleware）仍是权威。
    鉴权/RBAC 与网关列表读取一致：本端点非 public，由 AuthMiddleware 处理。
    capabilities 返回 browser_wait/config_version，前端必须使用返回值；读取失败
    时禁大日志提交并提示获取配置失败，不能悄悄沿用较短超时（§6.3.1）。
    """
    from backend import config
    cfg = config.gateway_upload_config()
    return {
        "upload_max_bytes": cfg["GATEWAY_UPLOAD_MAX_BYTES"],
        "request_max_bytes": cfg["GATEWAY_REQUEST_MAX_BYTES"],
        "supported_log_types": ["interf", "sql"],
        # GATEWAY_MAX_CONCURRENT 是固定常量 1（非调优项，P3-04）：配置清单/
        # 运维说明/capabilities 三处同标"固定、不可调"。
        "max_concurrent": cfg["GATEWAY_MAX_CONCURRENT"],
        "concurrency_fixed": True,
        "concurrency_note": (
            "固定常量 1（非调优项）：同一时刻仅允许一个网关日志任务，忙时返回 429，"
            "您的文件未被处理（未进入分析、未排队），需约 N 分钟后重新上传"),
        "browser_wait_seconds": cfg["GATEWAY_BROWSER_WAIT_SECONDS"],
        "receive_timeout_seconds": cfg["GATEWAY_UPLOAD_RECEIVE_TIMEOUT_SECONDS"],
        "analysis_timeout_seconds": cfg["GATEWAY_ANALYSIS_TIMEOUT_SECONDS"],
        "processing_budget_seconds": cfg["GATEWAY_PROCESSING_BUDGET_SECONDS"],
        "min_free_bytes": cfg["GATEWAY_MIN_FREE_BYTES"],
        "max_line_bytes": cfg["GATEWAY_MAX_LINE_BYTES"],
        "report_max_bytes": cfg["GATEWAY_REPORT_MAX_BYTES"],
        "flame_points": cfg["GATEWAY_FLAME_POINTS"],
        "deployment_mode": cfg["GATEWAY_DEPLOYMENT_MODE"],
        "config_version": config.APP_VERSION,
    }


@router.get("/reports", response_model=List[ReportItem])
def get_reports(connection_id: Optional[str] = None):
    """获取历史网关日志分析列表"""
    try:
        return gateway_log_service.get_reports(connection_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports/{report_id}")
def get_report_detail(report_id: int):
    """获取特定报告的详细数据"""
    try:
        res = gateway_log_service.get_report_detail(report_id)
        if not res:
            raise HTTPException(status_code=404, detail="报告不存在")
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reports/{report_id}/ticket")
def create_report_ticket(report_id: int, http_request: Request):
    """签发短时一次性报告票据（v1.6.2.2-UAT-O-15/O-22）。

    iframe 无法携带 Authorization 头，此前把长期登录令牌放进可见 URL 属凭证泄露面；
    改为登录后经本接口（头部令牌鉴权 + RBAC）签发 90s 一次性票据，
    iframe 仅携带该票据访问 /html。
    v1.6.2.2-UAT-O-22：签发是产生状态的操作，必须用 POST；票据存共享元数据库，
    多 worker 下签发与消费可落在不同进程。
    """
    res = gateway_log_service.get_report_detail(report_id)
    if not res:
        raise HTTPException(status_code=404, detail="报告不存在")
    username = getattr(http_request.state, "username", "anonymous")
    ticket = gateway_log_service.create_report_ticket(report_id, username)
    return {"ticket": ticket, "expires_in": 90}


@router.get("/reports/{report_id}/html", response_class=HTMLResponse)
def get_report_html(report_id: int, http_request: Request):
    """获取特定报告的 HTML 内容进行页面渲染

    v1.6.2.2-UAT-O-15：鉴权由中间件处理（头部令牌或一次性报告票据）；
    响应携带随机 nonce 制 CSP（无 unsafe-inline），不再发送与不透明源
    sandbox 冲突的 X-Frame-Options；嵌入控制由 frame-ancestors 'self' 承担。
    """
    # 告知全局安全头中间件：本文档需被同源不透明源 iframe 嵌入，移除 XFO 基线。
    http_request.state.frame_embeddable = True
    try:
        res = gateway_log_service.get_report_detail(report_id)
        if not res or not res.get("report_html"):
            raise HTTPException(status_code=404, detail="报告或HTML内容不存在")
        html_content = res["report_html"]
        # 服务时加固：剥离脚本块之外的残留内联事件处理器（旧版报告），
        # 再给裸 <script> 注入本次响应的 nonce；
        # 带属性的 script 标签拿不到 nonce，会被 CSP 直接拦截（失败关闭）。
        html_content = _strip_inline_handlers(html_content)
        nonce = secrets.token_urlsafe(16)
        html_content = _BARE_SCRIPT_RE.sub(f'<script nonce="{nonce}">', html_content)
        return HTMLResponse(content=html_content, headers=_report_doc_headers(nonce))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
