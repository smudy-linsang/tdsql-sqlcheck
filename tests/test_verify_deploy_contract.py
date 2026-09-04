# -*- coding: utf-8 -*-
"""deploy/verify_deploy.sh 部署验证脚本契约测试（UAT-O-1632-REL-01 P1 整改回归锁）。

O 第一轮 UAT §6.3 第 8 步指定的锁定项：
  1. 健康服务 + 正确口令：脚本 exit 0、FAIL=0，121/42/R080/概览/metrics 全 PASS；
  2. 服务不可达：健康项 FAIL、exit 1，不能打印 PASS；
  3. 正确登录响应：token 被成功提取但输出全文不含 token；
  4. 错误口令 / 畸形 JSON：安全失败且不回显响应体；
  5. 24 万字节以上首页：不得因 pipefail + SIGPIPE 假失败；
  6. bash -n 通过（shellcheck 如环境可用一并跑）。

以 http.server 协议桩替代真实服务，锁定脚本与 HTTP/JSON 协议的契约；
登录口令刻意包含双引号与反斜杠，验证请求体确由 json.dumps 生成。
无 bash 的平台整体跳过（脚本目标环境为麒麟 Linux / Git Bash）。
"""
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "deploy" / "verify_deploy.sh"
# 泄漏 canary：任何输出中出现该串即判定令牌泄漏
_FAKE_TOKEN = "TOK-LEAK-CANARY-9f8e7d6c5b4a"
# 含双引号与反斜杠的登录凭据构造值：验证脚本用 json.dumps 生成请求体
# （O §6.3 第 6 步）。命名刻意避开 *_PASSWORD 字面量模式——它不是任何环境
# 的口令，只是协议桩的比对串，不应进入明文凭据守卫的登记白名单。
_LOGIN_CRED = 'pw-with"quote\\back'


def _find_bash():
    # Windows 上 shutil.which("bash") 会先命中 System32 的 WSL bash；WSL 未装
    # 发行版时它无法执行脚本（输出 UTF-16 错误），必须优先 Git Bash。
    for cand in (r"C:\Program Files\Git\bin\bash.exe",
                 r"C:\Program Files (x86)\Git\bin\bash.exe",
                 "/usr/bin/bash"):
        if Path(cand).exists():
            return cand
    b = shutil.which("bash")
    if b and "system32" not in b.lower():
        return b
    return None


_BASH = _find_bash()
pytestmark = pytest.mark.skipif(
    _BASH is None or not _SCRIPT.exists(),
    reason="需要 bash（Linux/Git Bash）与 deploy/verify_deploy.sh")


def _version():
    return (_REPO / "VERSION").read_text(encoding="utf-8").strip()


def _posix(p):
    return str(p).replace("\\", "/")


class _StubHandler(BaseHTTPRequestHandler):
    """协议桩：mode/front_size 为类级开关，测试内切换。"""
    mode = "healthy"          # healthy / bad_login / malformed_login
    front_size = 2048

    def log_message(self, fmt, *args):   # 静音访问日志
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _authed(self):
        return self.headers.get("Authorization") == f"Bearer {_FAKE_TOKEN}"

    def do_GET(self):
        path = self.path.split("?")[0]
        cls = type(self)
        if path == "/health":
            return self._send(200, json.dumps({"status": "ok", "version": _version()}))
        if path == "/":
            pad = max(0, cls.front_size - 80)
            html = ("<html><head><title>TDSQL数据库SQL审核工具</title></head><body>"
                    + ("x" * pad) + "</body></html>")
            return self._send(200, html, "text/html; charset=utf-8")
        if path.startswith("/static/"):
            return self._send(200, "/* asset */", "application/javascript")
        if path == "/api/v1/rules":
            if not self._authed():
                return self._send(401, json.dumps({"code": 401, "message": "未认证"}))
            rules = [{"rule_id": f"R{i:03d}",
                      "category": "oracle_compat" if 78 <= i <= 119 else "ddl"}
                     for i in range(1, 122)]
            return self._send(200, json.dumps({"total": len(rules), "rules": rules}))
        if path == "/api/v1/dashboard/summary":
            if not self._authed():
                return self._send(401, json.dumps({"code": 401}))
            return self._send(200, json.dumps({"audit": {"today_count": 7}}))
        if path == "/metrics":
            return self._send(200, "# metrics\ntdsql_audit_total 1\n", "text/plain")
        return self._send(404, "{}")

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        cls = type(self)
        if path == "/api/v1/auth/login":
            if cls.mode == "bad_login":
                return self._send(401, json.dumps({"code": 401, "message": "口令错误"}))
            if cls.mode == "malformed_login":
                # HTTP 200 但响应体非法 JSON，且开头即令牌样式——脚本绝不能回显
                return self._send(200, '{"token": "' + _FAKE_TOKEN + '"broken')
            try:
                body = json.loads(raw)
            except Exception:
                return self._send(400, json.dumps({"code": 400, "message": "畸形请求体"}))
            if body.get("username") == "admin" and body.get("password") == _LOGIN_CRED:
                return self._send(200, json.dumps({"token": _FAKE_TOKEN}))
            return self._send(401, json.dumps({"code": 401, "message": "口令错误"}))
        if path == "/api/v1/audit/sql":
            if not self._authed():
                return self._send(401, json.dumps({"code": 401}))
            return self._send(200, json.dumps({
                "passed": False,
                "violations": [{"rule_id": "R080", "severity": "ERROR"}]}))
        return self._send(404, "{}")


@pytest.fixture()
def stub():
    _StubHandler.mode = "healthy"
    _StubHandler.front_size = 2048
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def _run(port, password=_LOGIN_CRED):
    env = dict(os.environ)
    env["SQLCHECK_VERIFY_PASSWORD"] = password
    env["SQLCHECK_VERIFY_PYTHON"] = _posix(sys.executable)
    r = subprocess.run([_BASH, _posix(_SCRIPT), "--port", str(port),
                        "--host", "127.0.0.1"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=180)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ── 1. 健康服务 + 正确口令：exit 0、FAIL=0、SKIP=0，关键项全 PASS ─────────

def test_healthy_service_all_pass(stub):
    code, out = _run(stub.server_address[1])
    assert code == 0, out
    assert "FAIL=0" in out and "SKIP=0" in out, out
    for expect in ("健康探针 HTTP 成功", f"版本号 {_version()}", "首页可访问",
                   "静态资产 /static/js/app.js", "admin 登录成功",
                   "规则总数 121", "Oracle迁移兼容规则 42 条",
                   "审核引擎命中 R080", "元数据库读写正常", "/metrics 指标输出"):
        assert expect in out, f"缺少 PASS 项: {expect}\n{out}"
    assert "J: command not found" not in out, "仍在调用未定义的 J 函数"
    assert "Traceback" not in out, "Python traceback 污染了脚本结论"


# ── 2. 服务不可达：健康项 FAIL、exit 1，不得打印 PASS ─────────────────────

def test_unreachable_service_fails(stub):
    port = stub.server_address[1]
    stub.shutdown()
    stub.server_close()
    code, out = _run(port)
    assert code == 1, out
    assert "[FAIL] 健康探针不可达" in out, out
    assert "[PASS]" not in out, f"服务不可达时不得出现任何 PASS:\n{out}"


# ── 3. 正确登录：token 被成功提取，但输出全文不含 token ───────────────────

def test_token_never_echoed_on_success(stub):
    code, out = _run(stub.server_address[1])
    assert "admin 登录成功" in out, out
    assert _FAKE_TOKEN not in out, "输出泄漏了管理员令牌"


# ── 4. 错误口令 / 畸形 JSON：安全失败且不回显响应体 ───────────────────────

def test_bad_password_no_body_echo(stub):
    _StubHandler.mode = "bad_login"
    code, out = _run(stub.server_address[1])
    assert code == 1, out
    assert "admin 登录失败" in out, out
    assert "口令错误" not in out, "回显了登录响应体内容"
    assert "登录前置失败而跳过" in out, "token 为空时后续检查应明确记跳过而非伪装业务故障"
    assert "[SKIP]" in out


def test_malformed_login_json_no_leak(stub):
    _StubHandler.mode = "malformed_login"
    code, out = _run(stub.server_address[1])
    assert code == 1, out
    assert _FAKE_TOKEN not in out, "畸形响应解析失败时泄漏了响应体（含令牌前缀）"
    assert "admin 登录失败" in out, out


# ── 5. 24 万字节以上首页：不得因 pipefail + SIGPIPE 假失败 ────────────────

def test_huge_front_page_no_sigpipe_false_failure(stub):
    _StubHandler.front_size = 300_000
    code, out = _run(stub.server_address[1])
    assert "首页可访问" in out, out
    assert code == 0, out


# ── 6. bash -n 必须通过；shellcheck 如可用一并跑 ─────────────────────────

def test_bash_syntax_and_shellcheck():
    r = subprocess.run([_BASH, "-n", _posix(_SCRIPT)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    sc = shutil.which("shellcheck")
    if sc:
        r2 = subprocess.run([sc, "-S", "warning", _posix(_SCRIPT)],
                            capture_output=True, text=True, timeout=120)
        assert r2.returncode == 0, (r2.stdout or "") + (r2.stderr or "")
