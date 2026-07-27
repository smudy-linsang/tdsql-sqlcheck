"""
安全响应头基线（S08）与 CSP 哈希一致性守护

CSP 的 script-src 用哈希放行 index.html 首部那段内联主题脚本。
该脚本一旦被改动而哈希未同步，浏览器会拒绝执行它——页面首屏主题闪白，
且这类故障不会让任何接口测试变红。故用本用例把两者钉在一起。
"""
import base64
import hashlib
import re
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app
from backend.middleware import _INLINE_THEME_SCRIPT_HASH, _SECURITY_HEADERS

client = TestClient(app)
_INDEX = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


def test_inline_script_hash_matches_index_html():
    """index.html 内联脚本的实际哈希必须与 CSP 中声明的一致"""
    html = _INDEX.read_text(encoding="utf-8")
    m = re.search(r"<script>(.*?)</script>", html, re.S)
    assert m, "index.html 中未找到内联脚本；若已移除，请同步删除 CSP 中的哈希"
    digest = base64.b64encode(
        hashlib.sha256(m.group(1).encode()).digest()).decode()
    expected = f"'sha256-{digest}'"
    assert _INLINE_THEME_SCRIPT_HASH == expected, (
        "内联主题脚本已改动但 CSP 哈希未更新。\n"
        f"  middleware 中声明: {_INLINE_THEME_SCRIPT_HASH}\n"
        f"  index.html 实际值: {expected}")


def test_security_headers_present_on_response():
    r = client.get("/health")
    for key in ("X-Content-Type-Options", "X-Frame-Options",
                "Referrer-Policy", "Content-Security-Policy"):
        assert key in r.headers, f"缺少安全响应头 {key}"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_csp_keeps_unsafe_eval_for_indom_template():
    """前端为免构建的 in-DOM 模板，运行期编译依赖 eval。

    去掉 'unsafe-eval' 会导致整页白屏（#app 为空）且接口测试全绿，
    属高危静默故障，因此在此显式固化该约束。
    若将来引入构建步骤预编译模板，应同时删除本断言与该 CSP 来源。
    """
    csp = _SECURITY_HEADERS["Content-Security-Policy"]
    assert "'unsafe-eval'" in csp, (
        "移除 'unsafe-eval' 前必须先把 Vue 模板改为构建期预编译，"
        "否则前端将白屏")
    # 其余方向仍须收紧
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert "base-uri 'self'" in csp
