# -*- coding: utf-8 -*-
"""发布依赖边界门禁（UAT4-O-REL-01）。

第三轮 UAT 整改曾把 playwright==1.62.0 误加入根 requirements.txt——
该文件是生产依赖清单：Dockerfile / deploy/install.sh / 发布包都安装它，
而离线 wheel 仓（dist/wheels_tmp）没有 playwright，发布校验会确定性失败。

本门禁钉住两件事：
  1. 生产 requirements.txt 不得包含任何测试框架（pytest / playwright 等）；
  2. 测试依赖清单（pyproject.toml 的 dev extra）必须精确包含 playwright==1.62.0
     ——删掉它，行为级浏览器测试（tests/test_g14_request_ownership_browser.py）
     将在干净 CI 环境失败关闭。
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# 测试框架/工具包名（小写精确比对），不得出现在生产依赖清单
_TEST_PACKAGES = {"pytest", "pytest-asyncio", "playwright", "pyee", "greenlet"}


def _production_packages() -> list[str]:
    """解析根 requirements.txt 的包名清单（忽略注释/空行/选项）。"""
    pkgs = []
    for line in (_REPO / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)", line)
        if m:
            pkgs.append(m.group(1).lower().replace("_", "-"))
    return pkgs


def _dev_extra_lines() -> list[str]:
    text = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r"\[project\.optional-dependencies\]\s*\n\s*dev\s*=\s*\[(.*?)\]",
                  text, re.S)
    assert m, "pyproject.toml 缺少 [project.optional-dependencies].dev"
    return [l.strip() for l in m.group(1).splitlines()
            if l.strip() and not l.strip().startswith("#")]


def test_production_requirements_have_no_test_frameworks():
    """生产 requirements.txt 不得包含测试框架（发布链路会安装它）。"""
    pkgs = _production_packages()
    leaked = sorted(set(pkgs) & _TEST_PACKAGES)
    assert not leaked, (
        f"生产 requirements.txt 混入测试依赖 {leaked}——"
        "离线 wheel 仓无这些包，发布校验会确定性失败；"
        "测试依赖只能进 pyproject.toml 的 [project.optional-dependencies].dev")


def test_dev_extra_pins_playwright_exactly():
    """dev extra 必须精确固定 playwright==1.62.0（行为级测试的依赖来源）。"""
    lines = _dev_extra_lines()
    assert 'playwright==1.62.0' in ",".join(lines).replace('"', '"'), (
        "dev extra 未精确包含 playwright==1.62.0："
        "删掉它，行为级浏览器测试将在干净 CI 环境失败关闭")


def test_dev_extra_has_core_test_packages():
    """dev extra 至少包含 pytest / pytest-asyncio / httpx（既有测试栈）。"""
    text = ",".join(_dev_extra_lines()).lower().replace('"', "").replace("'", "")
    for pkg in ("pytest", "pytest-asyncio", "httpx"):
        assert pkg in text, f"dev extra 缺少 {pkg}"
