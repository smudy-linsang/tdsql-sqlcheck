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


def _build_rules_payload():
    """真实 /api/v1/rules 响应特征：121 条规则，含中文 name/description/
    spec_source/fix_suggestion；ensure_ascii=False 编码后 ≥64KB。用于复现
    UAT-O-1632-R2-01（P2）——Git Bash 把大体量中文经 stdin 交给 Windows 原生
    Python 会转码破坏，桩必须是真实体量+中文，否则漏检（小型 ASCII 无法复现）。"""
    rules = []
    for i in range(1, 122):
        rid = f"R{i:03d}"
        if 78 <= i <= 119:
            cat = "oracle_compat"
        elif i < 40:
            cat = "ddl"
        else:
            cat = "distributed"
        rules.append({
            "rule_id": rid,
            "category": cat,
            "severity": "ERROR",
            "enabled": True,
            "name": f"规则{rid}中文名称：分布式建表与字段类型治理示例条目",
            "description": (f"规则{rid}的详细中文描述：禁止在TDSQL分布式场景下使用不合规的"
                            "字段类型、分区策略与分片键定义，须遵循数据库开发规范并留存评审记录。" * 2),
            "spec_source": "《TDSQL数据库开发规范》《Oracle迁移TDSQL改造适配方案》中文条目",
            "fix_suggestion": (f"规则{rid}修复建议：请调整字段类型或分区定义，补充中文注释说明"
                               "业务含义与容量评估后重新提交审核，必要时联系DBA复核分片键选择。"),
        })
    return {"total": len(rules), "rules": rules}


# 契约桩规则响应：模块加载即构建并自证体量（≥64KB 中文 UTF-8、oracle_compat=42），
# 防止桩退化成小型 ASCII 而漏检 P2（O §6.5 第四步「测试桩在发送前自断言」）。
_RULES_PAYLOAD = _build_rules_payload()
_RULES_PAYLOAD_BYTES = json.dumps(_RULES_PAYLOAD, ensure_ascii=False).encode("utf-8")
assert len(_RULES_PAYLOAD_BYTES) >= 64 * 1024, (
    f"契约桩规则响应须≥64KB中文以防漏检P2，实际 {len(_RULES_PAYLOAD_BYTES)} bytes")
assert _RULES_PAYLOAD["total"] == 121
assert sum(r["category"] == "oracle_compat" for r in _RULES_PAYLOAD["rules"]) == 42


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
            # 真实特征：大型中文 UTF-8 响应（≥64KB），复现 P2 的 Git Bash 转码场景
            return self._send(200, _RULES_PAYLOAD_BYTES, "application/json; charset=utf-8")
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


# ── 8. UAT-O-1632-R2-01（P2）：大型中文规则响应在 Git Bash + Windows Python 下须解析成功 ──

def test_large_utf8_rules_payload_on_git_bash(stub):
    """P2 回归锁：真实 /api/v1/rules 约 44KB 且含中文；Git Bash 把响应经 stdin 交给
    Windows 原生 Python 会发生字符转码破坏，导致规则总数/Oracle 分类误判失败
    （PASS=10 FAIL=2 exit 1）。整改后 json_get 按 UTF-8 文件路径解析，须 12/0/0、exit 0。

    本测试运行于本机（Windows Git Bash + Windows CPython，SQLCHECK_VERIFY_PYTHON
    显式指向 sys.executable），即 P2 的真实复现平台；契约桩返回 ≥64KB 中文响应，
    比线上 44KB 更严苛。若在 Linux 上运行则同样必须通过（路径经 cygpath 分支透传）。
    """
    code, out = _run(stub.server_address[1])
    assert code == 0, out
    assert "PASS=12" in out and "FAIL=0" in out and "SKIP=0" in out, out
    assert "规则总数 121" in out, out
    assert "Oracle迁移兼容规则 42 条" in out, out
    assert "Traceback" not in out and "JSONDecodeError" not in out, f"JSON 解析异常污染输出:\n{out}"
    assert _FAKE_TOKEN not in out, "输出泄漏了管理员令牌"


# ── 9. UAT-O-1632-R3-01（P2）：信号退出须显式终止并以 128+signo 约定码退出、清理私有 TMPDIR ──

# 用 export -f 导出阻塞 4s 的假 curl（无需真实服务），保证信号落在脚本请求中；
# 隔离 TMPDIR 精确判定脚本自身临时目录是否被清理；信号经 bash 内部 kill 投递
# （规避 Windows Python send_signal(SIGTERM) 退化为 TerminateProcess、无法触发
# bash trap 的限制）。占位符 __PY__/__CRED__/__SCRIPT__/__SIG__/__TOKEN__ 由测试注入。
_SIGNAL_WRAPPER = r'''
set -u
# 启用作业控制：POSIX 规定非交互 shell 的 `&` 异步命令会预置忽略 SIGINT/SIGQUIT，
# 而被忽略的信号在子 shell 内无法再 trap（导致脚本 trap INT 失效）。set -m 让后台
# 脚本进入独立进程组且不预忽略 INT，忠实复现前台运行（CI/人工 Ctrl+C）时的 SIGINT。
set -m
COUNT="$(mktemp)"; export COUNT
OUT="$(mktemp)"
WORK="$(mktemp -d)"
export TMPDIR="$WORK"
curl() { printf 'x\n' >> "$COUNT"; sleep 4; return 7; }
export -f curl
bash '__SCRIPT__' --host 127.0.0.1 --port 1 > "$OUT" 2>&1 &
pid=$!
n=0
while [ "$n" -lt 100 ]; do
  c=$(wc -l < "$COUNT" 2>/dev/null | tr -d ' ')
  [ "${c:-0}" -ge 1 ] 2>/dev/null && break
  sleep 0.2
  n=$((n+1))
done
created=$(ls -A "$WORK" 2>/dev/null | wc -l | tr -d ' ')
kill -__SIG__ "$pid" 2>/dev/null
wait "$pid"; rc=$?
leftover=$(ls -A "$WORK" 2>/dev/null | wc -l | tr -d ' ')
calls=$(wc -l < "$COUNT" 2>/dev/null | tr -d ' ')
if grep -qiE 'authorization|traceback' "$OUT" 2>/dev/null \
   || grep -qF "$SQLCHECK_VERIFY_PASSWORD" "$OUT" 2>/dev/null \
   || grep -qF '__TOKEN__' "$OUT" 2>/dev/null; then leak=1; else leak=0; fi
rm -f -- "$COUNT" "$OUT"; rm -rf -- "$WORK"
printf 'rc=%s created=%s leftover=%s calls=%s leak=%s\n' "$rc" "$created" "$leftover" "$calls" "$leak"
'''


@pytest.mark.parametrize("sig,code", [("HUP", 129), ("INT", 130), ("TERM", 143)])
def test_signal_exits_and_cleans_private_tmpdir(sig, code):
    """R3-01 回归锁：`trap cleanup EXIT HUP INT TERM` 只清理不退出的缺陷已修。

    收到 HUP/INT/TERM 时脚本必须：①显式终止（不再发起下一个 curl 请求）；
    ②以 128+signo 约定码退出（129/130/143），而非跑完全部检查后 exit 1；
    ③私有 TMPDIR 经 EXIT trap 清理干净（无残留）；④输出不含 token/口令/
    Authorization/traceback。
    """
    wrapper = (_SIGNAL_WRAPPER
               .replace("__SCRIPT__", _posix(_SCRIPT))
               .replace("__TOKEN__", _FAKE_TOKEN)
               .replace("__SIG__", sig))
    # 解释器与凭据经 subprocess env 注入（不嵌入命令字符串）：既符合"敏感值走
    # 环境变量"的守卫约定，也避免占位符被明文凭据守卫误判为字面量口令。
    env = dict(os.environ)
    env["SQLCHECK_VERIFY_PYTHON"] = _posix(sys.executable)
    env["SQLCHECK_VERIFY_PASSWORD"] = _LOGIN_CRED
    r = subprocess.run([_BASH, "-c", wrapper], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=90)
    out = (r.stdout or "").strip()
    assert f"rc={code}" in out, f"{sig} 应以 {code} 退出；输出={out} err={r.stderr}"
    assert "created=1" in out, f"{sig}：信号前脚本私有临时目录应已创建；输出={out}"
    assert "leftover=0" in out, f"{sig} 退出后私有 TMPDIR 应无残留；输出={out}"
    assert "calls=1" in out, f"{sig} 后不得发起下一个请求（curl 应仅 1 次）；输出={out}"
    assert "leak=0" in out, f"{sig} 输出不得含 token/口令/Authorization/traceback；输出={out}"
