# -*- coding: utf-8 -*-
"""v1.6.3.4 / D01：报告来源上下文（REQ-01）

设计出处：docs/DETAIL-v1.6.3.4-报告实例标识与分区统计及审核网关修复.md §3.2—§3.4

本模块只处理来源值和安全渲染，不承担审核、采集或鉴权。

核心契约（§3.2）：
  · 新的绑定检查：在扫描/任务受理时按**连接 ID 精确查找** registry.get_saved(id)，
    一次取值并传递；名称不是 host:port，也不是数据库名。查不到已指定 ID 时拒绝
    新任务，不能自动切默认连接。
  · origin = bound | offline | manual | legacy
  · name_source = snapshot | manual | legacy_stored | current_lookup | missing
  · 名称用注册值原文，按现有连接名称最大长度校验；不额外截断中文名称。
  · 新记录一经完成，重命名/删除连接不改扫描快照。扫描途中改名，仍用开始时
    冻结的名称。
  · 历史无上下文：先读有明确来源的历史名称；旧字段只是 endpoint 的，作为
    "历史端点"展示。仅能关联当前配置时，显示"扫描时名称未记录；当前连接
    名称：X（非扫描时快照）"；连接已删则显示"历史未记录名称（连接 ID：…）"。

HTML 与安全约束（§3.4）：
  · 所有新增名称、库名、角色说明按 HTML 文本上下文转义（包括引号）。
  · JS 中需要的数据使用 JSON 安全序列化，转义 < > & U+2028 U+2029，
    禁止直接拼接到 <script>、onclick 或 URL。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from html import escape as _html_escape
from typing import Optional

logger = logging.getLogger("tdsql.report_context")

# 上下文协议版本（§3.2 冻结结构 version 字段）
CONTEXT_VERSION = 1

# 单任务上下文 UTF-8 JSON 限 8 KiB（§3.3）
MAX_CONTEXT_JSON_BYTES = 8 * 1024

# 连接名称最大长度（tdsql_connections.name VARCHAR(255)）
MAX_CONNECTION_NAME_LEN = 255

# 北京时区（+08:00），用于 captured_at 时间戳
_TZ_CST = timezone(timedelta(hours=8))

# origin 枚举（§3.2）
ORIGIN_BOUND = "bound"        # 报告生成时已指定实例连接
ORIGIN_OFFLINE = "offline"    # 离线文件审核，未传 connection_id
ORIGIN_MANUAL = "manual"      # CLI 传入的人工名称
ORIGIN_LEGACY = "legacy"      # 历史记录重建

_VALID_ORIGINS = frozenset({ORIGIN_BOUND, ORIGIN_OFFLINE, ORIGIN_MANUAL, ORIGIN_LEGACY})

# name_source 枚举（§3.2）
NAME_SOURCE_SNAPSHOT = "snapshot"          # 扫描/任务受理时冻结的注册名称
NAME_SOURCE_MANUAL = "manual"              # CLI 人工指定
NAME_SOURCE_LEGACY_STORED = "legacy_stored"  # 历史记录中已存的名称
NAME_SOURCE_CURRENT_LOOKUP = "current_lookup"  # 仅能关联当前配置（非扫描时快照）
NAME_SOURCE_MISSING = "missing"            # 连接已删/无法取得名称

_VALID_NAME_SOURCES = frozenset({
    NAME_SOURCE_SNAPSHOT, NAME_SOURCE_MANUAL, NAME_SOURCE_LEGACY_STORED,
    NAME_SOURCE_CURRENT_LOOKUP, NAME_SOURCE_MISSING,
})


# ══════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════

@dataclass
class ConnectionContext:
    """单个连接的来源上下文（§3.2 冻结结构 connections[] 元素）。"""
    connection_id: str = ""
    connection_name: str = ""
    name_source: str = NAME_SOURCE_MISSING
    db_name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ConnectionContext":
        if not isinstance(d, dict):
            return cls()
        return cls(
            connection_id=str(d.get("connection_id") or ""),
            connection_name=str(d.get("connection_name") or ""),
            name_source=str(d.get("name_source") or NAME_SOURCE_MISSING),
            db_name=str(d.get("db_name") or ""),
        )


@dataclass
class ReportContext:
    """报告来源上下文（§3.2 冻结结构）。"""
    version: int = CONTEXT_VERSION
    captured_at: str = ""
    origin: str = ORIGIN_LEGACY
    connections: list = field(default_factory=list)  # list[ConnectionContext]

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "captured_at": self.captured_at,
            "origin": self.origin,
            "connections": [c.to_dict() for c in self.connections],
        }

    def to_json(self) -> str:
        """序列化为 UTF-8 JSON 字符串（持久化用）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "ReportContext":
        if not isinstance(d, dict):
            return cls()
        conns = d.get("connections") or []
        if not isinstance(conns, list):
            conns = []
        return cls(
            version=int(d.get("version") or CONTEXT_VERSION),
            captured_at=str(d.get("captured_at") or ""),
            origin=str(d.get("origin") or ORIGIN_LEGACY),
            connections=[ConnectionContext.from_dict(c) for c in conns],
        )

    @classmethod
    def from_json(cls, s: Optional[str]) -> Optional["ReportContext"]:
        """从 JSON 字符串反序列化；无效/空返回 None。"""
        if not s or not str(s).strip():
            return None
        try:
            d = json.loads(str(s))
        except (json.JSONDecodeError, ValueError):
            logger.warning("report_context_json 解析失败，按无上下文处理")
            return None
        if not isinstance(d, dict):
            return None
        return cls.from_dict(d)

    def is_empty(self) -> bool:
        """是否为空上下文（无连接信息）。"""
        return not self.connections


@dataclass
class RoleContext:
    """多实例合并时的角色上下文（§3.2 merge_report_contexts 输出）。

    role 例如 "基准扫描" / "目标扫描" / "上传日志关联实例"。
    """
    role: str = ""
    context: Optional[ReportContext] = None

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "context": self.context.to_dict() if self.context else None,
        }


# ══════════════════════════════════════════════════════════════════
# 核心函数契约（§3.2）
# ══════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    """当前时间的 ISO 8601 字符串（北京时区，精确到秒）。"""
    return datetime.now(_TZ_CST).replace(microsecond=0).isoformat()


def _validate_name(name: str) -> str:
    """校验并归一化连接名称：原文返回，超长截断（不额外截断中文）。

    §3.2：名称用注册值原文，按现有连接名称最大长度校验；不额外截断中文名称。
    """
    if not name:
        return ""
    s = str(name)
    if len(s) > MAX_CONNECTION_NAME_LEN:
        # 按字符截断（中文不额外截断），保留最大长度
        s = s[:MAX_CONNECTION_NAME_LEN]
    return s


def capture_report_context(
    connection_id: Optional[str],
    db_name: str = "",
    origin: str = ORIGIN_BOUND,
    connection_name: Optional[str] = None,
) -> ReportContext:
    """冻结报告来源上下文（§3.2 capture_report_context）。

    在扫描/任务受理时调用，按**连接 ID 精确查找** registry.get_saved(id)，
    一次取值并传递。名称不是 host:port，也不是数据库名。

    Args:
        connection_id: 连接 ID；None/空 表示未绑定（离线文件审核）
        db_name: 数据库名（辅助字段，不代替名称）
        origin: bound | offline | manual | legacy
        connection_name: 人工指定的连接名称（origin=manual 时使用；
                         origin=bound 时忽略，以注册值为准）

    Returns:
        ReportContext 冻结结构

    规则：
      · origin=bound 且 connection_id 非空：查 registry.get_saved(id)，
        取 name 字段作为 connection_name，name_source=snapshot。
        查不到已指定 ID 时**不自动切默认连接**，name_source=missing。
      · origin=offline 或 connection_id 为空：显示"未关联实例（场景）"，
        name_source=missing。
      · origin=manual：使用传入的 connection_name，name_source=manual。
      · 即席路径没有保存名称时明确"未命名即席连接"。
    """
    if origin not in _VALID_ORIGINS:
        logger.warning("capture_report_context: 非法 origin=%r，按 legacy 处理", origin)
        origin = ORIGIN_LEGACY

    ctx = ReportContext(
        version=CONTEXT_VERSION,
        captured_at=_now_iso(),
        origin=origin,
        connections=[],
    )

    # origin=manual：使用人工指定的名称
    if origin == ORIGIN_MANUAL:
        name = _validate_name(connection_name or "")
        ctx.connections.append(ConnectionContext(
            connection_id=str(connection_id or ""),
            connection_name=name or "未命名即席连接",
            name_source=NAME_SOURCE_MANUAL if name else NAME_SOURCE_MISSING,
            db_name=str(db_name or ""),
        ))
        return ctx

    # origin=offline 或 connection_id 为空：未关联实例
    if origin == ORIGIN_OFFLINE or not connection_id:
        ctx.connections.append(ConnectionContext(
            connection_id="",
            connection_name="",
            name_source=NAME_SOURCE_MISSING,
            db_name=str(db_name or ""),
        ))
        return ctx

    # origin=bound：按连接 ID 精确查找注册值
    conn_id = str(connection_id).strip()
    saved = None
    try:
        from backend.services.connection_registry import registry
        saved = registry.get_saved(conn_id)
    except Exception as e:
        logger.warning("capture_report_context: registry.get_saved(%r) 失败: %s", conn_id, e)
        saved = None

    if saved and isinstance(saved, dict):
        # 取注册值原文作为名称（不是 host:port，也不是数据库名）
        raw_name = saved.get("name") or ""
        name = _validate_name(raw_name)
        # 即席路径没有保存名称时明确"未命名即席连接"
        if not name:
            name = "未命名即席连接"
            name_source = NAME_SOURCE_MISSING
        else:
            name_source = NAME_SOURCE_SNAPSHOT
        # db_name 优先用传入值，回退到注册值的 database 字段
        db = str(db_name or saved.get("database") or "")
        ctx.connections.append(ConnectionContext(
            connection_id=conn_id,
            connection_name=name,
            name_source=name_source,
            db_name=db,
        ))
    else:
        # 查不到已指定 ID：不自动切默认连接，name_source=missing
        logger.info("capture_report_context: 连接 ID %r 未找到注册记录，名称缺失", conn_id)
        ctx.connections.append(ConnectionContext(
            connection_id=conn_id,
            connection_name="",
            name_source=NAME_SOURCE_MISSING,
            db_name=str(db_name or ""),
        ))

    return ctx


def resolve_legacy_context(
    record: dict,
    related_source: Optional[dict] = None,
) -> ReportContext:
    """历史记录的上下文降级解析（§3.2 resolve_legacy_context）。

    历史无上下文：先读有明确来源的历史名称；旧字段只是 endpoint 的，作为
    "历史端点"展示。仅能关联当前配置时，显示"扫描时名称未记录；当前连接
    名称：X（非扫描时快照）"；连接已删则显示"历史未记录名称（连接 ID：…）"。
    不能把现名伪造为历史名称，不批量补写历史列。

    Args:
        record: 历史记录行（dict），可能含 connection_id / connection_name /
                report_context_json 等字段
        related_source: 关联的来源配置（如 raw_slowlog 的 source），可选

    Returns:
        ReportContext（origin=legacy）
    """
    ctx = ReportContext(
        version=CONTEXT_VERSION,
        captured_at=_now_iso(),
        origin=ORIGIN_LEGACY,
        connections=[],
    )

    if not isinstance(record, dict):
        return ctx

    # 1. 优先读已持久化的 report_context_json（新记录）
    rc_json = record.get("report_context_json")
    if rc_json:
        parsed = ReportContext.from_json(rc_json)
        if parsed and not parsed.is_empty():
            # 历史重建、快照 upsert 不得用重建时现名覆盖已存上下文
            parsed.origin = ORIGIN_LEGACY
            return parsed

    # 2. 读历史记录中已存的名称字段（legacy_stored）
    conn_id = str(record.get("connection_id") or "").strip()
    legacy_name = ""
    # 不同表的名称字段名可能不同，逐个尝试
    for key in ("connection_name", "conn_name", "instance_name", "name"):
        v = record.get(key)
        if v and str(v).strip():
            legacy_name = _validate_name(str(v).strip())
            break

    # 3. 旧字段只是 endpoint 的（host:port），作为"历史端点"展示
    endpoint = ""
    host = str(record.get("host") or "").strip()
    port = record.get("port")
    if host and port:
        endpoint = f"{host}:{port}"
    elif host:
        endpoint = host

    db_name = str(record.get("db_name") or record.get("database") or "").strip()

    if legacy_name:
        # 有明确来源的历史名称
        ctx.connections.append(ConnectionContext(
            connection_id=conn_id,
            connection_name=legacy_name,
            name_source=NAME_SOURCE_LEGACY_STORED,
            db_name=db_name,
        ))
        return ctx

    # 4. 仅能关联当前配置时：current_lookup（非扫描时快照）
    if conn_id and related_source is None:
        try:
            from backend.services.connection_registry import registry
            saved = registry.get_saved(conn_id)
            if saved and isinstance(saved, dict):
                cur_name = _validate_name(saved.get("name") or "")
                if cur_name:
                    ctx.connections.append(ConnectionContext(
                        connection_id=conn_id,
                        connection_name=cur_name,
                        name_source=NAME_SOURCE_CURRENT_LOOKUP,
                        db_name=db_name or str(saved.get("database") or ""),
                    ))
                    return ctx
        except Exception as e:
            logger.debug("resolve_legacy_context: 当前配置查找失败: %s", e)

    # 5. 连接已删/无法取得名称：missing
    # 若有 endpoint，作为"历史端点"展示（不伪造为名称）
    display_name = ""
    if endpoint:
        display_name = f"历史端点 {endpoint}"
    ctx.connections.append(ConnectionContext(
        connection_id=conn_id,
        connection_name=display_name,
        name_source=NAME_SOURCE_MISSING,
        db_name=db_name,
    ))
    return ctx


def merge_report_contexts(
    contexts_with_roles: list,
) -> list:
    """多实例合并（§3.2 merge_report_contexts）。

    对比报告必须标"基准扫描""目标扫描"，相同 ID 改过名仍各显各名。
    多实例合并使用输入上下文集合，不塞入单实例的 8 KiB 槽位。

    Args:
        contexts_with_roles: [(role, ReportContext), ...] 列表

    Returns:
        list[RoleContext]
    """
    result = []
    if not isinstance(contexts_with_roles, (list, tuple)):
        return result
    for item in contexts_with_roles:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            role, ctx = item[0], item[1]
        elif isinstance(item, dict):
            role = item.get("role", "")
            ctx = item.get("context")
        else:
            continue
        if ctx is None:
            ctx = ReportContext(origin=ORIGIN_LEGACY)
        if not isinstance(ctx, ReportContext):
            # 尝试从 dict/JSON 解析
            if isinstance(ctx, dict):
                ctx = ReportContext.from_dict(ctx)
            elif isinstance(ctx, str):
                ctx = ReportContext.from_json(ctx) or ReportContext(origin=ORIGIN_LEGACY)
            else:
                ctx = ReportContext(origin=ORIGIN_LEGACY)
        result.append(RoleContext(role=str(role or ""), context=ctx))
    return result


# ══════════════════════════════════════════════════════════════════
# HTML 安全渲染（§3.4）
# ══════════════════════════════════════════════════════════════════

def _js_safe_json(obj) -> str:
    """JSON 安全序列化：转义 < > & U+2028 U+2029（§3.4）。

    禁止直接拼接到 <script>、onclick 或 URL。
    """
    s = json.dumps(obj, ensure_ascii=False)
    # 转义 HTML/JS 敏感字符
    s = s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    # U+2028 LINE SEPARATOR / U+2029 PARAGRAPH SEPARATOR 在 JS 中是合法换行，
    # 会破坏字符串字面量，必须转义
    s = s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return s


def _esc(text) -> str:
    """HTML 文本上下文转义（包括引号）。"""
    return _html_escape(str(text or ""), quote=True)


# 未关联实例的场景化提示文案（§3.1 通则）
_OFFLINE_HINTS = {
    "offline": "未关联实例（离线文件审核）",
    "manual": "未关联实例（人工指定）",
    "legacy": "未关联实例（历史记录）",
    "": "未关联实例",
}


def _badge_offline(escaped_text: str) -> str:
    """对“未关联实例/历史未记录名称”等占位名加浅黄徽标（v1.6.3.4 / TKT-M02）。

    用**内联样式**而非新增 CSS 类：导出的独立 HTML 报告不加载 app.css，内联样式
    保证离线/降级徽标在脱离平台单独打开时也生效；display-only，不破坏结构与
    CSP/nonce 安全链。已关联的真实名称仍用 <strong>（不走本函数）。
    """
    return ('<span style="background:#fff3cd;color:#856404;padding:1px 6px;'
            'border-radius:3px;font-weight:600;">' + escaped_text + '</span>')


def render_report_context(
    context: Optional[ReportContext],
    role: Optional[str] = None,
    scene: str = "",
) -> str:
    """渲染报告来源上下文为 escaped HTML fragment（§3.2 render_report_context）。

    统一页眉标签用"实例连接名称"，在标题下、首个指标区之前；长名自动换行，
    打印可见。多实例用名称＋ID 的来源表及角色列，明细能对应。

    Args:
        context: ReportContext；None 或空上下文显示"未关联实例（场景）"
        role: 角色标签（如"基准扫描"/"目标扫描"），多实例合并时使用
        scene: 场景提示（如"离线文件审核"/"主机磁盘测试"），用于未关联时的文案

    Returns:
        escaped HTML fragment（<div class="report-context">...</div>）
    """
    parts = []
    parts.append('<div class="report-context" data-report-context-version="1" '
                 'style="margin:8px 0;padding:8px 12px;background:#f8f9fa;'
                 'border-left:3px solid #0d6efd;font-size:0.9em;">')

    if role:
        parts.append(f'<span style="font-weight:600;color:#0d6efd;">{_esc(role)}</span> ')

    if context is None or context.is_empty():
        # 未关联实例（TKT-M02：加浅黄徽标，便于一眼区分未绑定/降级报告）
        hint = _OFFLINE_HINTS.get(scene, _OFFLINE_HINTS.get("", "未关联实例"))
        if scene and scene not in _OFFLINE_HINTS:
            hint = f"未关联实例（{_esc(scene)}）"
        parts.append(f'<span>实例连接名称：{_badge_offline(hint)}</span>')
        parts.append('</div>')
        return "".join(parts)

    conns = context.connections
    if len(conns) == 1:
        # 单实例：直接显示名称
        c = conns[0]
        name = c.connection_name
        is_placeholder = not name          # 未冻结到名称 → 占位（未关联/历史未记录）
        if not name:
            # name_source=missing 时的降级文案
            if c.connection_id:
                name = f"历史未记录名称（连接 ID：{_esc(c.connection_id)}）"
            elif scene:
                # 无连接 ID 且无名称：优先用调用方 scene 场景提示（如“离线文件审核”/
                # “主机磁盘测试”），比笼统的 origin 降级更准确（name 稍后统一 _esc）
                name = _OFFLINE_HINTS.get(scene) or f"未关联实例（{scene}）"
            else:
                name = _OFFLINE_HINTS.get(context.origin, "未关联实例")
        name_display = _esc(name)
        # TKT-M02：占位名（未关联/历史未记录）加浅黄徽标，真实绑定名称保持 <strong>
        name_html = _badge_offline(name_display) if is_placeholder else f"<strong>{name_display}</strong>"
        # current_lookup 需标注"非扫描时快照"
        suffix = ""
        if c.name_source == NAME_SOURCE_CURRENT_LOOKUP:
            suffix = ' <span style="color:#6c757d;">（扫描时名称未记录；当前连接名称，非扫描时快照）</span>'
        elif c.name_source == NAME_SOURCE_MISSING and c.connection_id:
            suffix = ' <span style="color:#6c757d;">（历史未记录名称）</span>'
        parts.append(f'<span>实例连接名称：{name_html}{suffix}</span>')
        if c.db_name:
            parts.append(f' <span style="color:#6c757d;">库：{_esc(c.db_name)}</span>')
    else:
        # 多实例：来源表
        parts.append('<div>实例连接名称（多来源）：</div>')
        parts.append('<table style="border-collapse:collapse;margin-top:4px;font-size:0.95em;">')
        parts.append('<thead><tr style="background:#e9ecef;">'
                     '<th style="border:1px solid #dee2e6;padding:4px 8px;text-align:left;">角色</th>'
                     '<th style="border:1px solid #dee2e6;padding:4px 8px;text-align:left;">连接名称</th>'
                     '<th style="border:1px solid #dee2e6;padding:4px 8px;text-align:left;">连接 ID</th>'
                     '<th style="border:1px solid #dee2e6;padding:4px 8px;text-align:left;">库</th>'
                     '</tr></thead><tbody>')
        for c in conns:
            name = c.connection_name or (
                f"历史未记录名称（连接 ID：{_esc(c.connection_id)}）"
                if c.connection_id else "未关联实例")
            c_role = _esc(role or "")
            parts.append('<tr>'
                         f'<td style="border:1px solid #dee2e6;padding:4px 8px;">{c_role}</td>'
                         f'<td style="border:1px solid #dee2e6;padding:4px 8px;">{_esc(name)}</td>'
                         f'<td style="border:1px solid #dee2e6;padding:4px 8px;">{_esc(c.connection_id)}</td>'
                         f'<td style="border:1px solid #dee2e6;padding:4px 8px;">{_esc(c.db_name)}</td>'
                         '</tr>')
        parts.append('</tbody></table>')

    parts.append('</div>')
    return "".join(parts)


def render_report_context_js(context: Optional[ReportContext]) -> str:
    """渲染为 JS 安全的 JSON 字符串（供 <script> 内使用，§3.4）。

    转义 < > & U+2028 U+2029，禁止直接拼接到 onclick 或 URL。
    """
    if context is None:
        return _js_safe_json(None)
    return _js_safe_json(context.to_dict())


# ══════════════════════════════════════════════════════════════════
# 持久化辅助
# ══════════════════════════════════════════════════════════════════

def context_to_json_column(context: Optional[ReportContext]) -> Optional[str]:
    """把 ReportContext 序列化为可存入 report_context_json 列的字符串。

    持久化前校验结构，不允许客户端塞任意对象。超过 8 KiB 限制时记录告警
    并返回 None（不阻塞主流程，但报告将显示"未关联实例"）。
    """
    if context is None:
        return None
    try:
        s = context.to_json()
    except Exception as e:
        logger.warning("context_to_json_column: 序列化失败: %s", e)
        return None
    # UTF-8 字节数校验（§3.3：单任务上下文 UTF-8 JSON 限 8 KiB）
    byte_len = len(s.encode("utf-8"))
    if byte_len > MAX_CONTEXT_JSON_BYTES:
        logger.warning(
            "context_to_json_column: 上下文 JSON 超过 %d 字节限制（实际 %d），"
            "不持久化（报告将显示未关联实例）",
            MAX_CONTEXT_JSON_BYTES, byte_len)
        return None
    return s


def context_from_json_column(s: Optional[str]) -> Optional[ReportContext]:
    """从 report_context_json 列值反序列化 ReportContext。"""
    return ReportContext.from_json(s)


def render_for_record(record: dict, scene: str = "",
                      role: Optional[str] = None) -> str:
    """从一条历史记录渲染"实例连接名称"来源块 HTML（v1.6.3.4 / D02 统一入口）。

    优先读已持久化的 report_context_json（新记录，扫描时冻结的真实名称）；
    无则按 §3.2 历史降级链解析（legacy_stored → current_lookup → missing）。
    14 个 HTML 生成入口（H01—H14）共用本函数，避免各处重复降级逻辑漂移。

    Args:
        record: 记录 dict（含 report_context_json 及可能的 connection_id/name/host/port）
        scene: 未关联时的场景提示（如"离线文件审核"/"在线元数据审核"）
        role: 角色标签（对比报告的"基准扫描"/"目标扫描"等）

    Returns:
        escaped HTML fragment（<div class="report-context" ...>）
    """
    ctx = None
    if isinstance(record, dict):
        ctx = ReportContext.from_json(record.get("report_context_json"))
    if ctx is None:
        ctx = resolve_legacy_context(record if isinstance(record, dict) else {})
    return render_report_context(ctx, role=role, scene=scene)


import re as _re

# <body ...> 开始标签（H08 注入锚点，§3.4）
_BODY_OPEN_RE = _re.compile(r"<body\b[^>]*>", _re.IGNORECASE)

# 已有“实例连接名称”来源块（UAT-M01：去重锚点）。网关来源块为单实例、内容无嵌套
# <div>（render_report_context 单实例分支 / 分析器 _inject_conn_name_block 均是单层），
# 故 .*?</div> 非贪婪能准确匹配整块。
_CONTEXT_BLOCK_RE = _re.compile(
    r'<div\s+class="report-context"[^>]*>.*?</div>', _re.IGNORECASE | _re.DOTALL)


def inject_context_into_html(html: str, context_html: str) -> str:
    """把来源块注入**已生成**的报告 HTML（v1.6.3.4 / D02 H08，§3.4）。

    网关报告的 report_html 由分析器生成后存库，服务时补来源块：
      · **UAT-M01 去重**：若报告已含 `report-context` 来源块（分析器生成时嵌入的
        占位/冻结名），用本次平台冻结上下文**替换**它，保证“实例连接名称”是唯一
        权威展示位，不再头部并存两块；
      · 无既有来源块（旧 report_html）→ 在 <body...> 开始处补块一次（锚点明确，
        不全局 replace 任意文本）；
      · 无 body 的历史片段用安全外层文档容纳；
      · 注入/替换的是已转义静态 HTML（无 <script>/on* 属性），不影响既有
        _strip_inline_handlers 与 nonce/CSP/iframe 安全链。
    """
    if not context_html:
        return html or ""
    if not html:
        return context_html
    # 已存在来源块 → 替换（UAT-M01：去重，冻结上下文为唯一权威）
    if _CONTEXT_BLOCK_RE.search(html):
        return _CONTEXT_BLOCK_RE.sub(lambda _m: context_html, html, count=1)
    # 无既有来源块 → 在 <body> 后注入一次
    m = _BODY_OPEN_RE.search(html)
    if m:
        idx = m.end()
        return html[:idx] + context_html + html[idx:]
    # 无 body 的历史片段：用安全外层文档容纳（不全局替换）
    return ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            '</head><body>' + context_html + html + '</body></html>')
