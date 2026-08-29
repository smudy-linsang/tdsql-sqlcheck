"""G11 网关日志分析 API 路由"""
import re
import secrets

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from typing import List, Optional
from pydantic import BaseModel
from backend.services.gateway_log_service import gateway_log_service

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
    connection_id: str = Form(...),
    log_type: str = Form("interf"),
    file: UploadFile = File(...)
):
    """上传网关日志并进行深度分析"""
    try:
        content = await file.read()
        res = gateway_log_service.analyze_log(
            connection_id=connection_id,
            file_name=file.filename,
            file_content=content,
            log_type=log_type
        )
        return {
            "status": res.get("status", "success"),
            "report_id": res["id"],
            "total_queries": res["total_queries"],
            "slow_queries": res["slow_queries"],
            "max_time_ms": res["max_time_ms"],
            "avg_time_ms": res["avg_time_ms"],
            # v1.6.2.2-UAT-O-17：混合输入不得静默丢行——响应携带解析覆盖率、
            # 跳过数与样例，前端据此提示用户报告仅覆盖部分输入。
            "parse_quality": res.get("parse_quality"),
        }
    except ValueError as e:
        # v1.6.2.2-UAT-O-11：零有效记录等业务输入错误返回 422（可读的失败语义），
        # 不得落入 500 让人误以为是系统故障，也不得返回 200 冒充成功
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


@router.get("/reports/{report_id}/ticket")
def create_report_ticket(report_id: int, http_request: Request):
    """签发短时一次性报告票据（v1.6.2.2-UAT-O-15）。

    iframe 无法携带 Authorization 头，此前把长期登录令牌放进可见 URL 属凭证泄露面；
    改为登录后经本接口（头部令牌鉴权 + RBAC）签发 90s 一次性票据，
    iframe 仅携带该票据访问 /html。
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
