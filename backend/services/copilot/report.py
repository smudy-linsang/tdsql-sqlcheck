# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：单轮独立 HTML 建议报告（CP-W09，DETAIL §13.3）。

只渲染已保存且当前有权的单 turn 结果；实例连接名称取会话快照，缺失明示
“历史来源未记录”，不可伪造连接名称。HTML 仅内联 CSS/转义文本，无
JavaScript/表单/追踪/远程资源；CSP 禁止脚本/connect/frame/object。
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone

from backend import config

_DISCLAIMER = "AI或本地助手建议不是正式审核、数据库执行或验收结论。"


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def render_turn_html(turn: dict, session: dict) -> str:
    resp = json.loads(turn.get("response_envelope") or "{}")
    answer = resp.get("answer") or {}
    sources = resp.get("sources") or []
    usage = resp.get("usage") or {}
    model = resp.get("model") or {}

    conn_name = session.get("name_snapshot")
    name_source = session.get("name_source") or "missing"
    if not conn_name:
        conn_name = "历史来源未记录" if session.get("connection_id") \
            else "未绑定实例"
        name_source = "missing"

    rows_findings = []
    for f in answer.get("findings", [])[:20]:
        rows_findings.append(
            f"<tr><td>{_e(f.get('kind'))}</td><td>{_e(f.get('text'))}</td></tr>")
    rows_steps = []
    for s in answer.get("steps", [])[:20]:
        rows_steps.append(
            f"<tr><td>{_e(s.get('text'))}</td><td>{_e(s.get('risk'))}</td></tr>")
    rows_sources = []
    for src in sources[:12]:
        rows_sources.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                _e(src.get("evidence_id") or src.get("source_id") or ""),
                _e(src.get("source_kind")),
                _e("可用" if src.get("available") else "缺失"),
                _e(src.get("completeness") or "")))
    rows_candidates = []
    for i, c in enumerate(answer.get("sql_candidates", [])[:2]):
        rows_candidates.append(
            f"<div class='cand'><div class='cand-h'>候选 {i + 1}：{_e(c.get('reason'))}</div>"
            f"<pre>{_e(c.get('sql'))}</pre>"
            f"<div class='warn'>仅纯文本复核，未在数据库执行；不证明语义等价</div></div>")

    missing = "".join(f"<li>{_e(x)}</li>" for x in answer.get("missing_evidence", [])[:10])
    limits = "".join(f"<li>{_e(x)}</li>" for x in answer.get("limitations", [])[:10])

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; style-src 'unsafe-inline'">
<title>Copilot 建议报告</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;margin:24px;color:#222;background:#fff}}
h1{{font-size:18px;border-bottom:2px solid #345;padding-bottom:8px}}
.meta{{font-size:12px;color:#555;margin:12px 0}}
.meta span{{margin-right:16px}}
.banner{{background:#fff8e1;border:1px solid #f0c36d;padding:8px 12px;font-size:12px;margin:12px 0}}
h2{{font-size:14px;margin-top:20px;color:#234}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left;vertical-align:top}}
.cand{{border:1px solid #ddd;margin:8px 0;padding:8px}}
.cand-h{{font-weight:bold;margin-bottom:6px}}
pre{{background:#f6f6f6;padding:8px;white-space:pre-wrap;word-break:break-all;font-size:12px}}
.warn{{color:#b45309;font-size:11px;margin-top:6px}}
ul{{font-size:12px}}
</style>
</head>
<body>
<h1>Copilot 专家建议报告（内部建议资料，请按项目权限传播）</h1>
<div class="meta">
<span>报告ID：{_e(turn.get('id'))}</span>
<span>应用版本：{_e(config.APP_VERSION)}</span>
<span>生成时间：{_e(generated_at)}</span>
<span>场景：{_e(turn.get('scene'))}</span>
<span>状态：{_e(turn.get('state'))}</span>
</div>
<div class="meta">
<span>实例连接名称：{_e(conn_name)}（来源：{_e(name_source)}）</span>
<span>数据库：{_e(session.get('database_name') or '—')}</span>
<span>实例类型：{_e(session.get('instance_type') or 'unknown')}</span>
<span>模型：{_e((model or {}).get('provider_id') or '本地模板')}</span>
<span>用量来源：{_e((usage or {}).get('accounting_source') or 'UNKNOWN')}</span>
</div>
<div class="banner">{_e(_DISCLAIMER)}</div>

<h2>结论摘要</h2>
<p>{_e(answer.get('summary') or '')}</p>

<h2>事实与依据</h2>
<table><tr><th>类型</th><th>内容</th></tr>{''.join(rows_findings) or '<tr><td colspan="2">无</td></tr>'}</table>

<h2>建议步骤</h2>
<table><tr><th>步骤</th><th>风险</th></tr>{''.join(rows_steps) or '<tr><td colspan="2">无</td></tr>'}</table>

<h2>候选 SQL（仅文本复核）</h2>
{''.join(rows_candidates) or '<p>无</p>'}

<h2>需要补充的资料</h2>
<ul>{missing or '<li>无</li>'}</ul>

<h2>限制与边界</h2>
<ul>{limits or '<li>无</li>'}</ul>

<h2>来源摘要</h2>
<table><tr><th>编号</th><th>类型</th><th>可用性</th><th>完整性</th></tr>{''.join(rows_sources) or '<tr><td colspan="4">无</td></tr>'}</table>
</body>
</html>"""
    # 导出最大 1MiB
    if len(doc.encode("utf-8")) > 1024 * 1024:
        raise ValueError("导出内容超过 1MiB 上限")
    return doc
