# -*- coding: utf-8 -*-
"""G14 异步请求所有权【行为级】浏览器测试（UAT3-O-G14-01）。

第三轮 UAT 指出：静态源码断言只能防误删标记，不能证明"迟到请求的提示/数据/
loading 副作用在真实页面上被作废"。本文件用真实后端服务 + 真实
frontend/index.html + Playwright 请求拦截（可控 Promise）补齐行为级证据：

  · A 延迟返回 422：切换到 B 后不得出现 A 的错误 toast/结果/告警，按钮可用
  · A 未返回时发起 B：A 的迟到完成不得关闭 B 的 loading；B 完成后才关闭
  · A 延迟返回 200：不得显示 A 的成功 toast 或 A 数据；B 完成后只显示 B
  · 不切 scope 的当前 400/422/500：必须正常展示服务端可读错误，按钮恢复

环境约定（失败关闭，不静默 skip）：
  · 需要 playwright Python 包（requirements.txt 已固定版本）与任一 Chromium 系
    浏览器：优先系统 Chrome（channel="chrome"），回退 playwright 自带 Chromium；
    两者都不可用即测试【失败】而非 skip（CI 镜像必须内置浏览器）。
  · 元数据库使用隔离测试库 tdsql_sqlcheck_test；后端以子进程真实启动，
    /run 接口由浏览器侧拦截精确控制时序，不依赖真实目标库。
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # 失败关闭：不得静默 skip
    pytest.fail(
        "缺少 playwright Python 包：pip install playwright==1.62.0 并安装浏览器"
        "（CI 镜像必须内置 Chromium/Chrome）",
        pytrace=False)

_REPO = Path(__file__).resolve().parent.parent
_PORT = 18977
_BASE = f"http://127.0.0.1:{_PORT}"

# 现有接口契约的最小成功响应体（不依赖真实数据库）
def _ok_body(total: int, db: str = "", conn: str = "") -> dict:
    items = []
    if db:
        items = [{"db_name": db, "total_tables": total, "shard_tables": 0,
                  "broadcast_tables": 0, "single_tables": total,
                  "baseline_tables": total, "subpartition_tables": 0,
                  "status": "OK", "detail": ""}]
    return {
        "stat_id": 9000 + total, "instance_type": "centralized",
        "type_source": "declared", "type_conflict": False,
        "database_count": 1 if db else 0, "total_tables": total,
        "shard_tables": 0, "broadcast_tables": 0, "single_tables": total,
        "baseline_tables": total, "subpartition_tables": 0,
        "failed_databases": 0, "skipped_databases": 0, "overlap_count": 0,
        "items": items, "warnings": [], "shape": {},
        "created_at": "2026-09-02 19:00:00",
    }


class _RunGate:
    """/run 请求拦截闸：按序号挂起/放行，精确控制两个 Promise 的返回顺序。

    实现要点：route handler 运行在与主线程共享的驱动线程里，**不得阻塞等待**
    （否则主线程后续的 page.evaluate/click 会排队在 handler 后面形成死锁）。
    故 handler 只登记挂起的 route 立即返回，由主线程 release() 时 fulfill。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._count = 0
        self._pending = {}          # ordinal -> 挂起的 route
        self._plan = {}             # ordinal -> (status, body)

    def plan(self, ordinal: int, status: int, body: dict):
        with self._lock:
            self._plan[ordinal] = (status, body)

    def handler(self, route):
        with self._lock:
            self._count += 1
            n = self._count
            if n in self._plan:
                self._pending[n] = route     # 挂起：不 fulfill 不 fallback
                return
        route.fallback()

    def release(self, ordinal: int):
        deadline = time.time() + 10
        while time.time() < deadline:
            with self._lock:
                route = self._pending.pop(ordinal, None)
            if route is not None:
                status, body = self._plan[ordinal]
                route.fulfill(status=status, content_type="application/json",
                              body=json.dumps(body))
                return
            time.sleep(0.05)
        raise AssertionError(f"第 {ordinal} 个 /run 请求未到达拦截器")

    @property
    def arrived(self) -> int:
        with self._lock:
            return self._count


def _wait_http(url: str, timeout: float = 30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError(f"服务未在 {timeout}s 内就绪: {url}")


@pytest.fixture(scope="module")
def server():
    """启动真实后端服务（隔离测试库 + 免认证），并准备两条合成实例连接。"""
    env = os.environ.copy()
    env["AUTH_ENABLED"] = "false"
    env["SCHEDULER_ENABLED"] = "false"
    env["SQLCHECK_DB_NAME"] = "tdsql_sqlcheck_test"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(_PORT), "--log-level", "warning"],
        cwd=str(_REPO), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_http(f"{_BASE}/health")
        # 两条合成实例连接（/run 全被浏览器拦截，无需真实可连）
        for name in ("pw-own-a", "pw-own-b"):
            body = json.dumps({"name": name, "host": "127.0.0.1", "port": 13306,
                               "username": "root", "password": "x",
                               "database": f"{name}_db"}).encode()
            req = urllib.request.Request(
                f"{_BASE}/api/v1/tdsql/connections", data=body, method="POST",
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
        yield _BASE
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def browser():
    """系统 Chrome 优先，回退 playwright Chromium；都不可用即失败（不 skip）。"""
    pw = sync_playwright().start()
    browser = None
    err = None
    for kwargs in ({"channel": "chrome"}, {}):
        try:
            browser = pw.chromium.launch(headless=True, **kwargs)
            break
        except Exception as e:  # pragma: no cover - 环境相关
            err = e
    if browser is None:
        pw.stop()
        pytest.fail(f"无可用浏览器（Chrome/Chromium 均启动失败）：{err}；"
                    "CI 镜像必须内置浏览器，本门禁失败关闭",
                    pytrace=False)
    yield browser
    browser.close()
    pw.stop()


def _open_g14(page):
    """打开页面并进入 深度诊断 → 表类型统计 页签。"""
    page.goto(f"{_BASE}/", wait_until="domcontentloaded")
    page.wait_for_selector("text=深度诊断", timeout=15000)
    page.click("text=深度诊断")
    page.wait_for_selector(".el-tabs__item:has-text('表类型统计')", timeout=10000)
    page.click(".el-tabs__item:has-text('表类型统计')")
    page.wait_for_selector("button:has-text('统计表类型')", timeout=10000)


def _new_page(browser):
    ctx = browser.new_context()
    # 免认证模式下前端登录页显隐取决于 localStorage token（checkSession 不写 token）
    ctx.add_init_script("localStorage.setItem('tdsql_token','pw-test-token')")
    page = ctx.new_page()
    return ctx, page


def _select_instance(page, name_part: str):
    """在深度诊断页选择指定实例。"""
    page.click(".el-select")          # 深度诊断页的实例选择器（页首唯一 el-select）
    page.wait_for_selector(".el-select-dropdown__item", timeout=10000)
    page.click(f".el-select-dropdown__item:has-text('{name_part}')")


def _set_db_input(page, value: str):
    """用原生 setter 写库名输入框并触发 input 事件（保证 Vue v-model 同步）。"""
    page.evaluate(
        """(v) => {
            const el = document.querySelector("input[placeholder='库名(空=全部业务库)']");
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(el, v);
            el.dispatchEvent(new Event('input', {bubbles: true}));
        }""", value)


def _error_toasts(page) -> list:
    return [t.inner_text() for t in page.locator(".el-message--error").all()]


def _success_toasts(page) -> list:
    return [t.inner_text() for t in page.locator(".el-message--success").all()]


def _run_button(page):
    return page.locator("button:has-text('统计表类型')").first


# ══════════════════════════════════════════════════════════════════
# 用例 1：A 延迟返回 422——切到 B 后 A 的错误提示/结果/告警不得出现，按钮可用
# ══════════════════════════════════════════════════════════════════
def test_stale_422_error_toast_suppressed(server, browser):
    gate = _RunGate()
    gate.plan(1, 422, {"detail": "实例连接失败：请检查地址、端口、网络和账号；本次未产生统计结果"})
    ctx, page = _new_page(browser)
    try:
        page.route("**/api/v1/table-type-stats/run", gate.handler)
        _open_g14(page)
        _select_instance(page, "pw-own-a")

        _run_button(page).click()                    # A 发起（挂起中）
        page.wait_for_selector("button.is-loading", timeout=5000)
        _set_db_input(page, "db_shifted")            # 切查询条件 → A 作废
        gate.release(1)                              # A 迟到返回 422
        page.wait_for_timeout(1500)                  # 等迟到提示有机会出现

        assert _error_toasts(page) == [], \
            f"A 迟到的 422 错误提示不得出现在新上下文: {_error_toasts(page)}"
        assert "总表" not in page.inner_text("body"), "A 的结果不得显示"
        btn = _run_button(page)
        assert "is-loading" not in (btn.get_attribute("class") or ""), \
            "A 作废后按钮不得被旧请求锁住"
    finally:
        ctx.close()


# ══════════════════════════════════════════════════════════════════
# 用例 2：A 未返回时发起 B——A 的迟到完成不得关闭 B 的 loading
# ══════════════════════════════════════════════════════════════════
def test_stale_finally_must_not_release_new_loading(server, browser):
    gate = _RunGate()
    gate.plan(1, 422, {"detail": "实例连接失败：请检查地址、端口、网络和账号；本次未产生统计结果"})
    gate.plan(2, 200, _ok_body(222, "db_shifted"))
    ctx, page = _new_page(browser)
    try:
        page.route("**/api/v1/table-type-stats/run", gate.handler)
        _open_g14(page)
        _select_instance(page, "pw-own-a")

        _run_button(page).click()                    # A 发起（挂起）
        page.wait_for_selector("button.is-loading", timeout=5000)
        _set_db_input(page, "db_shifted")            # 切条件：A 作废 + watch 释放 loading
        page.wait_for_timeout(300)
        _run_button(page).click()                    # B 发起（挂起，loading 属于 B）
        page.wait_for_selector("button.is-loading", timeout=5000)

        gate.release(1)                              # A 迟到完成（finally 不得关 B 的 loading）
        page.wait_for_timeout(800)
        btn = _run_button(page)
        assert "is-loading" in (btn.get_attribute("class") or ""), \
            "B 在途时 A 的迟到 finally 不得关闭 loading"

        gate.release(2)                              # B 完成
        page.wait_for_selector("text=总表", timeout=10000)
        page.wait_for_timeout(500)
        btn = _run_button(page)
        assert "is-loading" not in (btn.get_attribute("class") or ""), \
            "B 完成后 loading 才允许关闭"
        assert "222" in page.inner_text("body"), "B 的数据必须显示"
    finally:
        ctx.close()


# ══════════════════════════════════════════════════════════════════
# 用例 3：A 延迟返回 200——不得显示 A 的成功 toast 或 A 数据；B 只显示 B
# ══════════════════════════════════════════════════════════════════
def test_stale_200_success_and_data_suppressed(server, browser):
    gate = _RunGate()
    gate.plan(1, 200, _ok_body(111))               # A 数据标记：111
    gate.plan(2, 200, _ok_body(222, "db_shifted")) # B 数据标记：222
    ctx, page = _new_page(browser)
    try:
        page.route("**/api/v1/table-type-stats/run", gate.handler)
        _open_g14(page)
        _select_instance(page, "pw-own-a")

        _run_button(page).click()                    # A 发起（挂起）
        page.wait_for_selector("button.is-loading", timeout=5000)
        _set_db_input(page, "db_shifted")            # A 作废
        gate.release(1)                              # A 迟到 200
        page.wait_for_timeout(1500)

        assert _success_toasts(page) == [], \
            f"A 迟到的成功提示不得出现: {_success_toasts(page)}"
        assert "111" not in page.inner_text("body"), "A 的数据不得显示"

        _run_button(page).click()                    # B 发起并放行
        gate.release(2)
        page.wait_for_selector("text=总表", timeout=10000)
        body = page.inner_text("body")
        assert "222" in body and "111" not in body, "只显示 B 的数据"
        assert "db_shifted" in body, "结果范围必须是 B 的库名"
    finally:
        ctx.close()


# ══════════════════════════════════════════════════════════════════
# 用例 4：不切 scope 的当前 400/422/500——错误必须正常展示，按钮恢复
# ══════════════════════════════════════════════════════════════════
def test_current_scope_errors_still_visible(server, browser):
    cases = [
        (400, {"detail": "数据库不存在或当前账号不可见: no_such（SHOW DATABASES 未返回该库）"},
         "数据库不存在或当前账号不可见"),
        (422, {"detail": "实例连接失败：请检查地址、端口、网络和账号；本次未产生统计结果"},
         "实例连接失败"),
        (500, {"detail": "表类型统计内部错误，请携带 X-Request-ID 联系管理员排查"},
         "表类型统计内部错误"),
    ]
    ctx, page = _new_page(browser)
    try:
        state = {"i": 0}
        def handler(route):
            status, body = cases[state["i"]][0], cases[state["i"]][1]
            route.fulfill(status=status,
                          content_type="application/json", body=json.dumps(body))
        page.route("**/api/v1/table-type-stats/run", handler)
        _open_g14(page)
        _select_instance(page, "pw-own-a")

        for idx, (status, _body, expect_text) in enumerate(cases):
            state["i"] = idx
            _run_button(page).click()
            page.wait_for_selector(".el-message--error", timeout=10000)
            toasts = _error_toasts(page)
            assert any(expect_text in t for t in toasts), \
                f"当前 {status} 的可读错误必须展示（实际 toast: {toasts}）"
            page.wait_for_timeout(3600)              # 等 toast 自动消失
            btn = _run_button(page)
            assert "is-loading" not in (btn.get_attribute("class") or ""), \
                f"{status} 完成后按钮必须恢复"
    finally:
        ctx.close()
