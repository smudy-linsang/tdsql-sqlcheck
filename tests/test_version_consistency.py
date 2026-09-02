# -*- coding: utf-8 -*-
"""发布版本一致性门禁（v1.6.3.0-UAT-O-G14-02）。

第一轮 UAT 发现：G14 落盘后发布标识仍是 1.6.2.2——登录页、页面标题、
静态资源缓存参数、VERSION、APP_VERSION、/health 各说各话，
UAT 证据无法与目标发布准确对应。

本用例把"同一版本的七个来源"钉成自动化断言：任何一处漏改即红灯。
历史注释中的 `v1.6.2.2-UAT-*` 是缺陷追踪标识，不属于发布标识、不校验。
"""
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


def _version_file() -> str:
    return (_REPO / "VERSION").read_text(encoding="utf-8").strip()


def test_version_file_matches_app_version():
    from backend import config
    assert _version_file() == config.APP_VERSION, (
        f"VERSION 文件（{_version_file()}）与 config.APP_VERSION"
        f"（{config.APP_VERSION}）不一致")


def test_health_and_openapi_use_app_version():
    """/health 与 OpenAPI 元数据的 version 必须等于 config.APP_VERSION。"""
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend import config
    client = TestClient(app)
    h = client.get("/health")
    assert h.status_code == 200
    assert h.json()["version"] == config.APP_VERSION, \
        f"/health 返回 {h.json()['version']}，期望 {config.APP_VERSION}"
    assert app.version == config.APP_VERSION, \
        f"FastAPI app.version（{app.version}）与 config.APP_VERSION 不一致"


def test_frontend_version_marks_match():
    """HTML 标题、登录页、CSS/JS 缓存参数必须携带同一版本号。"""
    from backend import config
    v = config.APP_VERSION
    html = _read("frontend/index.html")
    checks = {
        "页面 <title>": f" V{v}" in html,
        "登录页版本行": f"V{v} ·" in html,
        "app.css 缓存参数": f"app.css?v={v}" in html,
        "theme-dark-blue.css 缓存参数": f"theme-dark-blue.css?v={v}" in html,
        "app.js 缓存参数": f"app.js?v={v}" in html,
    }
    missing = [k for k, okk in checks.items() if not okk]
    assert not missing, f"前端以下发布标识未更新为 {v}：{missing}"

    # 反向护栏：发布标识位不得残留旧版本号（历史注释 v1.6.*-UAT-* 不受此约束）
    stale = [m for m in re.findall(r'[?]v=(\d+\.\d+\.\d+\.\d+)', html) if m != v]
    assert not stale, f"静态资源缓存参数残留旧版本号：{sorted(set(stale))}"
    m = re.search(r'<title>[^<]*V(\d+\.\d+\.\d+\.\d+)[^<]*</title>', html)
    assert m and m.group(1) == v, f"页面标题版本号 {m and m.group(1)} ≠ {v}"


def test_no_version_drift_between_spots():
    """汇总断言：七个来源全部同源。"""
    from backend import config
    from fastapi.testclient import TestClient
    from backend.main import app
    v = _version_file()
    html = _read("frontend/index.html")
    client = TestClient(app)
    health_v = client.get("/health").json()["version"]
    all_v = {
        "VERSION": v,
        "config.APP_VERSION": config.APP_VERSION,
        "/health": health_v,
        "FastAPI app.version": app.version,
        "HTML title": (re.search(r'<title>[^<]*V(\d+\.\d+\.\d+\.\d+)', html) or [None, None])[1],
        "登录页": (re.search(r'class="version">V(\d+\.\d+\.\d+\.\d+)', html) or [None, None])[1],
        "app.js?v=": (re.search(r'app\.js\?v=(\d+\.\d+\.\d+\.\d+)', html) or [None, None])[1],
    }
    bad = {k: vv for k, vv in all_v.items() if vv != v}
    assert not bad, f"发布标识不一致（基准 VERSION={v}）：{bad}"
