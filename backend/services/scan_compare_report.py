"""
扫描结果对比报告 HTML 渲染

设计依据：docs/DETAIL-v1.3-扫描结果对比.md §7

要求：
  - 自包含：内联 CSS，不引用任何外部资源（内网部署 + 离线可打开 + 可邮件转发）
  - 打印友好：@media print 去背景、表格不跨页断行
  - 明细行数保护：单类超过 MAX_ROWS 只渲染前 N 行并注明
  - XSS 防护：所有外部文本一律 html.escape
"""
import html
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

MAX_ROWS = 500

_MODULE_LABELS = {
    "schema_audit": "在线元数据审核",
    "slow_scan": "慢SQL扫描任务",
    "bigtable": "大表治理",
    "launch_check": "上线检查",
}

_CSS = """
* { box-sizing: border-box; }
body { font-family: "Microsoft YaHei","PingFang SC","Segoe UI",Roboto,Arial,sans-serif;
       background:#f5f7fa; color:#1e293b; margin:0; padding:24px; line-height:1.6; }
.wrap { max-width:1180px; margin:0 auto; background:#fff; padding:32px;
        border-radius:8px; box-shadow:0 2px 12px rgba(15,23,42,.06); }
h1 { font-size:22px; margin:0 0 6px; color:#0f1e34; }
.meta { font-size:13px; color:#64748b; }
.meta b { color:#1e293b; }
.arrow { color:#2563eb; font-weight:700; }
.banner { padding:10px 14px; border-radius:6px; margin:14px 0; font-size:13px; }
.banner.danger { background:#fef2f2; border-left:4px solid #dc2626; color:#991b1b; }
.banner.warn { background:#fffbeb; border-left:4px solid #d97706; color:#92400e; }
.kpis { display:flex; flex-wrap:wrap; gap:12px; margin:22px 0; }
.kpi { flex:1; min-width:150px; background:#f8fafc; border:1px solid #e2e8f0;
       border-radius:6px; padding:14px; text-align:center; }
.kpi .num { font-size:26px; font-weight:700; line-height:1.2;
            font-family:"JetBrains Mono",Consolas,monospace; }
.kpi .lbl { font-size:12px; color:#64748b; margin-top:4px; }
.t-green { color:#16a34a; } .t-red { color:#dc2626; }
.t-amber { color:#d97706; } .t-blue { color:#2563eb; }
h2 { font-size:16px; margin:26px 0 10px; padding-left:10px;
     border-left:3px solid #2563eb; color:#0f1e34; }
h2 .cnt { font-size:13px; color:#64748b; font-weight:400; }
table { width:100%; border-collapse:collapse; font-size:12.5px; }
th { background:#f1f5f9; color:#475569; text-align:left; padding:7px 9px;
     border-bottom:2px solid #e2e8f0; font-weight:600; }
td { padding:6px 9px; border-bottom:1px solid #f1f5f9; vertical-align:top;
     word-break:break-word; }
tr:nth-child(even) td { background:#fafbfc; }
.sev { display:inline-block; padding:1px 7px; border-radius:10px;
       font-size:11px; font-weight:600; }
.sev.ERROR { background:#fef2f2; color:#dc2626; }
.sev.WARNING { background:#fffbeb; color:#d97706; }
.sev.INFO { background:#f8fafc; color:#64748b; }
.dist { margin:6px 0 18px; }
.dist-row { display:flex; align-items:center; gap:10px; margin:5px 0; font-size:12.5px; }
.dist-row .name { width:120px; color:#475569; }
.dist-row .bar { height:14px; border-radius:3px; }
.dist-row .val { color:#64748b; font-family:"JetBrains Mono",Consolas,monospace; }
.empty { color:#94a3b8; font-size:13px; padding:10px 0; }
.note { font-size:12px; color:#94a3b8; margin-top:6px; }
footer { margin-top:30px; padding-top:14px; border-top:1px solid #e2e8f0;
         font-size:12px; color:#94a3b8; display:flex; justify-content:space-between; }
@media print {
  body { background:#fff; padding:0; }
  .wrap { box-shadow:none; padding:0; }
  tr, .kpi { page-break-inside: avoid; }
  table { page-break-inside: auto; }
}
"""


def _e(v) -> str:
    """转义任意值为安全 HTML 文本"""
    return html.escape("" if v is None else str(v))


def _fmt_time(v) -> str:
    s = str(v or "")
    return s.replace("T", " ")[:19] if s else "-"


def _sev_badge(sev: str) -> str:
    s = (sev or "").upper()
    cls = s if s in ("ERROR", "WARNING", "INFO") else "INFO"
    return f'<span class="sev {cls}">{_e(s or "-")}</span>'


def _rows_table(items, headers, row_fn, empty_text="无") -> str:
    if not items:
        return f'<div class="empty">{_e(empty_text)}</div>'
    shown = items[:MAX_ROWS]
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join(f"<tr>{row_fn(it)}</tr>" for it in shown)
    extra = ""
    if len(items) > MAX_ROWS:
        extra = (f'<div class="note">仅展示前 {MAX_ROWS} 条，'
                 f'其余 {len(items) - MAX_ROWS} 条请在页面查看</div>')
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{extra}"


def _issue_row(it) -> str:
    return (f"<td>{_e(it.get('object_name'))}</td>"
            f"<td>{_e(it.get('issue_type'))}</td>"
            f"<td>{_sev_badge(it.get('severity'))}</td>"
            f"<td>{_e(it.get('title'))}</td>")


def _remain_row(it) -> str:
    attrs = it.get("attrs") or {}
    last = attrs.get("last_seen") or ""
    return (f"<td>{_e(it.get('object_name'))}</td>"
            f"<td>{_e(it.get('issue_type'))}</td>"
            f"<td>{_sev_badge(it.get('severity'))}</td>"
            f"<td>{_e(it.get('title'))}</td>"
            f"<td>{_e(_fmt_time(last) if last else '-')}</td>")


def _changed_row(it) -> str:
    ch = it.get("change") or {}
    pct = ch.get("pct")
    pct_str = f"（{pct:+.1f}%）" if isinstance(pct, (int, float)) else ""
    return (f"<td>{_e(it.get('object_name'))}</td>"
            f"<td>{_e(ch.get('type'))} / {_e(ch.get('field'))}</td>"
            f"<td>{_e(ch.get('old'))} → <b>{_e(ch.get('new'))}</b>{_e(pct_str)}</td>"
            f"<td>{_e(it.get('title'))}</td>")


def _severity_dist(summary) -> str:
    """两次扫描的严重级别分布对比（纯 CSS 条形，不引图表库）"""
    base = (summary.get("by_severity") or {}).get("base") or {}
    target = (summary.get("by_severity") or {}).get("target") or {}
    keys = ["ERROR", "WARNING", "INFO"]
    peak = max([base.get(k, 0) for k in keys] + [target.get(k, 0) for k in keys] + [1])
    colors = {"ERROR": "#dc2626", "WARNING": "#d97706", "INFO": "#94a3b8"}
    rows = []
    for k in keys:
        b, t = int(base.get(k, 0) or 0), int(target.get(k, 0) or 0)
        if not b and not t:
            continue
        for tag, val in (("变更前", b), ("变更后", t)):
            width = max(2, int(val / peak * 320))
            rows.append(
                f'<div class="dist-row"><span class="name">{_e(k)} · {tag}</span>'
                f'<span class="bar" style="width:{width}px;background:{colors[k]}"></span>'
                f'<span class="val">{val}</span></div>')
    return f'<div class="dist">{"".join(rows)}</div>' if rows else ""


def render_compare_html(result: dict) -> str:
    """把 run_compare 的返回渲染成自包含 HTML 报告"""
    summary = result.get("summary") or {}
    labels = result.get("labels") or {}
    base = result.get("base") or {}
    target = result.get("target") or {}
    module = result.get("module") or ""
    module_label = _MODULE_LABELS.get(module, module)

    banners = []
    if summary.get("degraded"):
        banners.append('<div class="banner danger">⚠ 部分快照明细缺失或损坏，'
                       '本报告已退化为全量差集，结果仅供参考。</div>')
    for w in (result.get("warnings") or []):
        cls = "danger" if "截断" in str(w) else "warn"
        banners.append(f'<div class="banner {cls}">⚠ {_e(w)}</div>')

    conn_line = " · ".join(filter(None, [
        _e(result.get("connection_name") or result.get("connection_id") or "未知实例"),
        _e(result.get("db_name") or ""),
    ]))

    # V1.4：报告自解释——脱离尺度的问题数没有意义，页眉必须标注用的哪把尺
    rs_id = result.get("rule_set_id") or ""
    rs_name = result.get("rule_set_name") or ""
    if rs_id:
        scale_line = f"{_e(rs_name or rs_id)}（{_e(rs_id)}）"
    else:
        scale_line = "V1.4 前记录，尺度未知"

    kpis = [
        ("之前问题数", summary.get("base_total", 0), ""),
        ("现在问题数", summary.get("target_total", 0), ""),
        (labels.get("fixed", "已修复"), summary.get("fixed_count", 0), "t-green"),
        (labels.get("new", "新增问题"), summary.get("new_count", 0), "t-red"),
        (labels.get("remain", "遗留未整改"), summary.get("remain_count", 0), "t-amber"),
        ("整改率", f'{summary.get("fix_rate", 0)}%', "t-blue"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="num {cls}">{_e(val)}</div>'
        f'<div class="lbl">{_e(lbl)}</div></div>'
        for lbl, val, cls in kpis)

    fixed_lbl = labels.get("fixed", "已修复")
    new_lbl = labels.get("new", "新增问题")
    remain_lbl = labels.get("remain", "遗留未整改")

    sections = [
        f'<h2>{_e(fixed_lbl)} <span class="cnt">共 {summary.get("fixed_count", 0)} 项</span></h2>'
        + _rows_table(result.get("fixed") or [],
                      ["对象", "问题类型", "级别", "描述"], _issue_row,
                      f"本次没有{fixed_lbl}的问题"),

        f'<h2>{_e(new_lbl)} <span class="cnt">共 {summary.get("new_count", 0)} 项</span></h2>'
        + _rows_table(result.get("new") or [],
                      ["对象", "问题类型", "级别", "描述"], _issue_row,
                      "没有新增问题"),

        f'<h2>{_e(remain_lbl)} <span class="cnt">共 {summary.get("remain_count", 0)} 项</span></h2>'
        + _rows_table(result.get("remain") or [],
                      ["对象", "问题类型", "级别", "描述", "末次出现"], _remain_row,
                      "没有遗留问题"),

        f'<h2>级别 / 指标变化 <span class="cnt">共 {summary.get("changed_count", 0)} 项</span></h2>'
        + _rows_table(result.get("changed") or [],
                      ["对象", "变化项", "变化前 → 变化后", "描述"], _changed_row,
                      "无显著变化"),
    ]

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TDSQL 扫描结果对比报告 - {_e(module_label)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>TDSQL 扫描结果对比报告 · {_e(module_label)}</h1>
  <div class="meta">
    实例：<b>{conn_line}</b><br>
    基准：<b>{_e(_fmt_time(base.get('scan_finished_at')))}</b>
    <span class="arrow">&nbsp;→&nbsp;</span>
    目标：<b>{_e(_fmt_time(target.get('scan_finished_at')))}</b>
  </div>
  {''.join(banners)}
  <div class="kpis">{kpi_html}</div>
  <h2>严重级别分布对比</h2>
  {_severity_dist(summary)}
  {''.join(sections)}
  <footer>
    <span>生成时间：{_e(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</span>
    <span>TDSQL SQL审核平台 · 扫描结果纵向对比</span>
  </footer>
</div>
</body>
</html>"""


def render_single_snapshot_html(snap: dict) -> str:
    """渲染单个扫描/体检快照的独立 HTML 报告"""
    snap = snap or {}
    meta_module = snap.get("module", "")
    module_label = _MODULE_LABELS.get(meta_module, "上线检查" if meta_module == "launch_check" else meta_module)
    conn_name = snap.get("connection_name") or snap.get("connection_id") or "未知实例"
    db_name = snap.get("db_name") or "全部数据库"
    created_by = snap.get("created_by") or "system"
    finished_at = _fmt_time(snap.get("scan_finished_at") or snap.get("created_at"))
    
    stats = snap.get("stats") or {}
    object_total = snap.get("object_total", 0)
    issue_total = snap.get("issue_total", 0)
    error_count = snap.get("error_count", 0)
    warning_count = snap.get("warning_count", 0)
    info_count = issue_total - error_count - warning_count
    if info_count < 0:
        info_count = 0

    issues = snap.get("issues") or []

    def _issue_row(it):
        it = it or {}
        sev = it.get("severity") or "INFO"
        title = it.get("title") or f"[{it.get('issue_type','')}] {it.get('object_name','')}"
        return (
            f"<td>{_sev_badge(sev)}</td>"
            f"<td><b>{_e(it.get('issue_type', '-'))}</b></td>"
            f"<td>{_e(it.get('object_name', '-'))}</td>"
            f"<td>{_e(it.get('detail', title))}</td>"
            f"<td>{_e(it.get('suggestion', '-'))}</td>"
        )

    kpi_html = (
        f'<div class="kpi"><div class="num">{object_total}</div><div class="lbl">检查对象数</div></div>'
        f'<div class="kpi"><div class="num">{issue_total}</div><div class="lbl">问题总数</div></div>'
        f'<div class="kpi"><div class="num t-red">{error_count}</div><div class="lbl">ERROR (错误)</div></div>'
        f'<div class="kpi"><div class="num t-amber">{warning_count}</div><div class="lbl">WARNING (警告)</div></div>'
        f'<div class="kpi"><div class="num t-blue">{info_count}</div><div class="lbl">INFO (提示)</div></div>'
    )

    issues_table = _rows_table(
        issues,
        ["级别", "检查项", "目标对象", "详细说明", "处置建议"],
        _issue_row,
        empty_text="未查出任何不合规问题项"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TDSQL 扫描快照报告 - {_e(module_label)} (#{_e(snap.get('id', ''))})</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>TDSQL 扫描快照报告 · {_e(module_label)}</h1>
  <div class="meta">
    实例：<b>{_e(conn_name)}</b> &nbsp;|&nbsp; 
    检查范围：<b>{_e(db_name)}</b> &nbsp;|&nbsp; 
    执行人：<b>{_e(created_by)}</b> &nbsp;|&nbsp; 
    完成时间：<b>{_e(finished_at)}</b>
  </div>
  <div class="kpis">{kpi_html}</div>
  <h2>问题明细列表 <span class="cnt">（共 {len(issues)} 项）</span></h2>
  {issues_table}
  <footer>
    <span>生成时间：{_e(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</span>
    <span>TDSQL SQL审核平台 · 扫描快照报告</span>
  </footer>
</div>
</body>
</html>"""

