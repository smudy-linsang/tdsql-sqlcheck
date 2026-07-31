# -*- coding: utf-8 -*-
"""路由完整性守卫（规约 R-17）

背景：v1.5.2.4 P0-01，a1fdc53 误删 StatusUpdateRequest 请求模型。
- Python 3.11（生产 wheels 目标）：backend.main 导入期 NameError，进程起不来；
- Python 3.14（开发机，PEP 649 惰性注解）：导入成功，但 FastAPI 把该请求体
  参数静默降级为必填查询参数，接口返回 422。
两种形态都必须在提交前被本文件拦截（部署侧另有 preflight_check.sh 导入检查）。

注意：FastAPI 0.139 的 include_router 产生惰性 _IncludedRouter 节点，
app.routes 顶层不直接暴露 APIRoute，需经 original_router 递归展开。
"""
from fastapi.routing import APIRoute

from backend.main import app


def _walk_api_routes(routes):
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        orig = getattr(r, "original_router", None)
        if orig is not None:
            yield from _walk_api_routes(orig.routes)
        elif not isinstance(r, APIRoute):
            sub = getattr(r, "routes", None)
            if sub:
                yield from _walk_api_routes(sub)


def _routes():
    return list(_walk_api_routes(app.routes))


def test_app_has_routes():
    """路由遍历本身必须有效（防止 FastAPI 升级改变内部结构后守卫静默失效）"""
    assert len(_routes()) > 100, "APIRoute 遍历结果异常，检查 _walk_api_routes 兼容性"


def test_slow_query_status_route_consumes_json_body():
    """P0-01 定点回归：慢SQL状态更新接口必须以 JSON 请求体接收参数"""
    route = next(r for r in _routes()
                 if r.path == "/api/v1/slow-queries/{slow_id}/status")
    assert route.body_field is not None, (
        "StatusUpdateRequest 缺失时该接口会退化为查询参数，"
        "见 docs/v1.5.2.4_缺陷修复方案.md FIX-1")


def test_no_route_query_param_named_request():
    """通用守卫：请求模型类被误删时，FastAPI 会把形参降级为查询参数。
    正常代码里不存在名为 request/req/body/payload 的查询参数
    （fastapi.Request 类型的形参不进入 query_params），出现即说明某个
    BaseModel 引用失效。"""
    offenders = []
    for r in _routes():
        for p in r.dependant.query_params:
            if p.name in ("request", "req", "body", "payload"):
                offenders.append(f"{r.path} -> {p.name}")
    assert not offenders, f"疑似请求模型缺失导致的参数退化: {offenders}"
