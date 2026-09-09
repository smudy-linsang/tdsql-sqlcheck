# -*- coding: utf-8 -*-
"""v1.6.3.5 / SIT B-01 整改 — 交付门禁：应用与全部新模块必须能被 import。

A 第一轮 SIT B-01：metadata_artifacts.py 用了 Optional 却没 import，在 Python≤3.13
（生产目标 3.11）上模块级注解即时求值 → NameError → `from backend.main import app` 崩。
本机 Python 3.14 因 PEP 649 惰性注解掩盖了它——所以这里显式 `get_type_hints` 强制解析
各新模块的模块级函数注解，即便在 3.14 也能抓出"未导入的类型名"这类缺陷。
"""
import importlib
import inspect
import typing

import pytest

# v1.6.3.5 新增的全部模块（任一 import 失败即交付门禁不通过）
NEW_MODULES = [
    "backend.engine.r035_context",
    "backend.services.metadata_audit_repository",
    "backend.services.metadata_audit_pipeline",
    "backend.services.metadata_artifacts",
    "backend.api.metadata_audit",
    "backend.workers.metadata_runner",
    "backend.workers.metadata_audit_worker",
]


@pytest.mark.parametrize("modname", NEW_MODULES)
def test_module_importable(modname):
    mod = importlib.import_module(modname)
    assert mod is not None


@pytest.mark.parametrize("modname", NEW_MODULES)
def test_module_annotations_resolve(modname):
    """强制解析模块级函数/方法的类型注解，抓出"用了但未导入的类型名"（PEP649 也拦得住）。"""
    mod = importlib.import_module(modname)
    problems = []
    for name, obj in vars(mod).items():
        funcs = []
        if inspect.isfunction(obj):
            funcs.append(obj)
        elif inspect.isclass(obj) and obj.__module__ == modname:
            funcs.extend(m for _, m in inspect.getmembers(obj, inspect.isfunction))
        for fn in funcs:
            try:
                typing.get_type_hints(fn)
            except NameError as e:   # 用了未导入的类型名
                problems.append(f"{modname}.{fn.__qualname__}: {e}")
            except Exception:
                pass                  # 其他解析失败（前向引用等）不作为门禁
    assert not problems, "存在未导入的类型名注解:\n" + "\n".join(problems)


def test_main_app_importable():
    """交付门禁底线：FastAPI 应用必须能被 import（B-01 传导链 main→api→services）。"""
    from backend.main import app
    assert app is not None
