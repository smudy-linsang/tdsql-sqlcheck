# -*- coding: utf-8 -*-
"""UAT-O-15 回归测试：网关报告脚本上下文注入消除 + nonce 制 CSP + 一次性票据

覆盖 O 第四轮 UAT 报告 O-15（BLOCK）的整改验收点：
1. 恶意日志（</script>/<img onerror>/引号/反斜杠/U+2028/U+2029）经分析器后
   不得在脚本上下文形成提前闭合断点；`<`/`>`/`&` 必须以 Unicode 转义存在。
2. 报告模板不得输出内联事件处理器属性（nonce 制 CSP 下它们会被拦截）。
3. 服务时响应：CSP 不含 unsafe-inline、所有裸 <script> nonce 化、
   不再发送与不透明源 sandbox 冲突的 X-Frame-Options、frame-ancestors 'self'。
4. 一次性报告票据：90s 有效、用后即焚、绑定报告 ID、拒绝伪造与重放。
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from backend.api.gateway_log import (
    _BARE_SCRIPT_RE, _report_doc_headers, _strip_inline_handlers,
)
from backend.services.gateway_log_service import gateway_log_service

ROOT = Path(__file__).resolve().parents[1]
ANALYZER = ROOT / "backend" / "services" / "gateway_log_analysis" / "analyze_gateway_log.py"

MALICIOUS_SQLS = [
    "SELECT 1 </script><script>window.__pwned=1</script>",
    "SELECT '<img src=x onerror=alert(1)>' FROM t",
    'SELECT \'quote" back\\slash\' ',
    "SELECT '\u2028\u2029 linesep'",
]


def _real_inline_handlers(html: str) -> list:
    """真实（非转义文本上下文）的内联事件处理器属性"""
    found = []
    for m in re.finditer(r"\son[a-zA-Z]+\s*=", html):
        prefix = html[max(0, m.start() - 500):m.start()]
        lt = prefix.rfind("&lt;")
        if lt == -1:
            lt = prefix.rfind("\\u003c")
        gt = max(prefix.rfind("&gt;"), prefix.rfind("\\u003e"))
        if lt == -1 or gt > lt:
            found.append(m.group(0))
    return found


@pytest.fixture(scope="module")
def malicious_report_html():
    """用恶意合成日志跑一次分析器（与服务同一命令形态），返回报告 HTML"""
    lines = []
    for i, sql in enumerate(MALICIOUS_SQLS):
        lines.append(
            f"[2026-08-29 10:0{i}:00 000001] INFO topic=interf&timecost={100 * (i + 1)}"
            f"&sql={sql}&db=proddb&instance=127.0.0.1:50000&ret=0"
        )
    for i in range(20):
        lines.append(
            f"[2026-08-29 10:1{i % 6}:00 000001] INFO topic=interf&timecost={i + 1}"
            f"&sql=SELECT id FROM t WHERE id={i}&db=proddb&instance=127.0.0.1:50000&ret=0"
        )
    tmpdir = Path(tempfile.mkdtemp(prefix="test_o15_"))
    log_file = tmpdir / "interf_instance_50000.2026-08-29.0"
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    html_out = tmpdir / "report.html"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    res = subprocess.run(
        [sys.executable, str(ANALYZER), "--files", str(log_file),
         "-o", str(html_out), "--log-types", "interf", "-f", "html"],
        capture_output=True, text=True, timeout=180, env=env,
    )
    assert html_out.exists(), f"分析器未生成报告: {res.stderr[-500:]}"
    return html_out.read_text(encoding="utf-8", errors="replace")


class TestScriptContextEncoding:
    """数据→脚本上下文编码：`</script>` 断点不得存在"""

    def test_no_premature_script_close_in_data(self, malicious_report_html):
        scripts = re.findall(r"<script>(.*?)</script>", malicious_report_html, flags=re.S)
        assert scripts, "报告必须包含交互脚本块"
        bad = [s for s in scripts if "</script" in s or "<script" in s]
        assert not bad, "数据块中存在提前闭合的 </script> 断点"

    def test_malicious_markers_unicode_escaped(self, malicious_report_html):
        joined = "".join(re.findall(r"<script>(.*?)</script>",
                                    malicious_report_html, flags=re.S))
        assert "\\u003c/script\\u003e" in joined, "恶意 </script> 未被 Unicode 转义"
        assert "\\u003cimg" in joined, "恶意 <img 未被 Unicode 转义"

    def test_no_bare_img_or_svg_tags(self, malicious_report_html):
        assert "<img" not in malicious_report_html
        assert "<svg" not in malicious_report_html

    def test_no_real_inline_event_handlers(self, malicious_report_html):
        assert not _real_inline_handlers(malicious_report_html), \
            "模板不得输出真实内联事件处理器"


class TestServeTimeHardening:
    """服务时加固：nonce 制 CSP、无 unsafe-inline、无 XFO"""

    def test_csp_nonce_no_unsafe_inline(self):
        headers = _report_doc_headers("TESTNONCE")
        csp = headers["Content-Security-Policy"]
        assert "script-src 'nonce-TESTNONCE'" in csp
        assert "unsafe-inline" not in csp.split("style-src")[0], \
            "script-src 不得含 unsafe-inline"
        assert "frame-ancestors 'self'" in csp
        assert "X-Frame-Options" not in headers, \
            "不得发送与不透明源 sandbox 冲突的 X-Frame-Options"

    def test_bare_scripts_get_nonce(self, malicious_report_html):
        served = _strip_inline_handlers(malicious_report_html)
        served = _BARE_SCRIPT_RE.sub('<script nonce="N1">', served)
        bare_left = re.findall(r"<script>", served)
        assert not bare_left, "裸 <script> 未全部 nonce 化"
        assert served.count('<script nonce="N1">') >= 1

    def test_strips_handlers_outside_scripts_only(self):
        html = ('<button onclick="evil()">x</button>'
                '<script>var d=" onerror=x ";</script>')
        out = _strip_inline_handlers(html)
        assert "onclick" not in out.split("<script>")[0], \
            "脚本块之外的内联处理器必须剥离"
        assert 'var d=" onerror=x ";' in out, "脚本块内已转义数据不得被误伤"

    def test_served_doc_has_no_real_handlers(self, malicious_report_html):
        served = _strip_inline_handlers(malicious_report_html)
        assert not _real_inline_handlers(served)


class TestOneTimeReportTicket:
    """一次性报告票据：用后即焚、绑定报告、拒绝伪造"""

    def test_issue_consume_once(self):
        tk = gateway_log_service.create_report_ticket(9001, "uat_admin")
        assert gateway_log_service.consume_report_ticket(tk, 9001) == "uat_admin"
        assert gateway_log_service.consume_report_ticket(tk, 9001) is None, \
            "票据必须一次性消费，重放不得命中"

    def test_bound_to_report_id(self):
        tk = gateway_log_service.create_report_ticket(9002, "uat_admin")
        assert gateway_log_service.consume_report_ticket(tk, 9999) is None, \
            "票据必须绑定报告 ID"

    def test_forged_ticket_rejected(self):
        assert gateway_log_service.consume_report_ticket("forged-ticket", 1) is None

    def test_empty_ticket_rejected(self):
        assert gateway_log_service.consume_report_ticket("", 1) is None
