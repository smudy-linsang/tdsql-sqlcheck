"""
TDSQL SQL审核平台 - FastAPI 入口 (V2.0)

启动方式: python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
访问前端: http://localhost:8000
访问API文档: http://localhost:8000/docs

V2.0 变更:
- 认证与RBAC中间件（AUTH_ENABLED，默认开启）
- 请求ID/访问日志/Prometheus指标中间件
- 前端静态资源本地化（/static，纯内网环境可用）
- 初始管理员账户引导
- CORS默认收敛（同源部署），可通过 CORS_ALLOW_ORIGINS 配置
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.api.sql_audit import router as audit_router
from backend.api.slow_query import router as slow_query_router
from backend.api.dashboard import router as dashboard_router
from backend.api.gitlab_hook import router as gitlab_router
from backend.api.tdsql_manage import router as tdsql_router
from backend.api.rules import router as rules_router
from backend.api.project import router as project_router
from backend.api.bigtable import router as bigtable_router
from backend.api.quality_gate import router as gate_router
from backend.api.monitor import router as monitor_router
from backend.api.inspection import router as inspection_router
from backend.api.cluster_inspect import router as cluster_inspect_router
from backend.api.index_audit import router as index_audit_router
from backend.api.table_type_stats import router as table_type_stats_router
from backend.api.schema_diff import router as schema_diff_router
from backend.api.emergency import router as emergency_router
from backend.api.daily_inspect import router as daily_inspect_router
from backend.api.sql_stats import router as sql_stats_router
# V2.0 新增路由
from backend.api.auth import router as auth_router
from backend.api.rulesets import router as rulesets_router
from backend.api.system_config import router as system_config_router
from backend.api.admin import router as admin_router
from backend.api.scan_compare import router as scan_compare_router
from backend.api.raw_slowlog import router as raw_slowlog_router
from backend.api.metadata_audit import router as metadata_audit_router  # v1.6.3.5 在线元数据任务
from backend.services.metadata_audit_repository import MetadataJobError  # v1.6.3.5 DU-2 异常处理器
from backend.middleware import (AuthMiddleware, BodySizeLimitMiddleware,
                                GatewayUploadPolicyMiddleware,
                                RequestContextMiddleware)

# G10-G13 新增路由
from backend.api.zk_discovery import router as zk_discovery_router
from backend.api.gateway_log import router as gateway_log_router
from backend.api.ppt_report import router as ppt_report_router
from backend.api.toolkit import router as toolkit_router

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("tdsql")

# 前端目录
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理（启动/关闭）"""
    # ── 启动时 ──
    logger.info("TDSQL SQL审核平台启动中... (V%s)", config.APP_VERSION)
    try:
        import anyio
        anyio.to_thread.current_default_thread_limiter().total_tokens = 100
    except Exception:
        pass
    try:
        from backend.services.database import ensure_db, init_rule_configs
        ensure_db()
        init_rule_configs()
        logger.info("数据库初始化完成 (V2.0, 27张表)")
    except Exception as e:
        logger.warning(f"数据库初始化失败（非致命）: {e}")
    # v1.6.3.4 / D05：网关大日志上传配置启动校验（§6.3.1）——失败关闭，拒绝启动。
    # 不能只打印 warning：GATEWAY_MAX_CONCURRENT≠1、超时链余量不足等非法配置若放行，
    # 要到生产停服或大日志上传时才暴露。切生产前用拟发布配置跑本校验即可阻断。
    try:
        _gw_cfg = config.gateway_upload_config()
        _gw_problems = config.validate_gateway_config(_gw_cfg)
        if _gw_problems:
            for _p in _gw_problems:
                logger.error("网关上传配置校验失败: %s", _p)
            raise RuntimeError(
                "网关大日志上传配置校验失败，拒绝启动（详见上方 ERROR 日志）："
                + "；".join(_gw_problems))
        logger.info(
            "网关上传配置校验通过 (mode=%s, upload_max=%d MiB, request_max=%d MiB, "
            "concurrent=%d 固定不可调)",
            _gw_cfg["GATEWAY_DEPLOYMENT_MODE"],
            _gw_cfg["GATEWAY_UPLOAD_MAX_BYTES"] // 1024 // 1024,
            _gw_cfg["GATEWAY_REQUEST_MAX_BYTES"] // 1024 // 1024,
            _gw_cfg["GATEWAY_MAX_CONCURRENT"])
    except Exception as e:
        # 校验失败或异常一律拒绝启动（失败关闭），不降级为 warning
        logger.error("网关上传配置校验未通过，应用拒绝启动: %s", e)
        raise
    # V2.0: 初始管理员引导
    try:
        if config.auth_enabled():
            from backend.services.auth_service import auth_service
            auth_service.ensure_bootstrap_admin()
        else:
            logger.warning(
                "⚠️ 认证已关闭 (AUTH_ENABLED=false)，仅限开发/测试环境使用！"
                "生产环境必须开启认证。")
    except Exception as e:
        logger.warning(f"管理员账户引导失败（非致命）: {e}")
    try:
        from backend.services.scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        logger.warning(f"定时任务调度器启动失败（非致命）: {e}")
    # v1.6.3.5 / UAT-M1：启动后探测元数据执行器 runner 心跳，缺失则打印明显提示
    # （dev/UAT 单机若只起 Web 未起 runner，新提交的元数据任务会被拒绝受理/503）。
    try:
        from backend.services.metadata_audit_repository import repository as _meta_repo
        from backend.services import metadata_job_process as _jp
        from backend.services.database import ensure_db as _ensure_meta
        _ensure_meta()
        _slot = _meta_repo.slot_state()
        _alive = bool(_slot and _slot.get("accepting") and
                      _jp.is_fresh_heartbeat(_slot.get("runner_heartbeat_at"),
                                             window_seconds=15))
        if not _alive:
            logger.warning(
                "⚠️ 未检测到元数据审核执行器 metadata-runner 心跳：在线元数据审核新任务将被"
                "拒绝受理（503 EXECUTOR_UNAVAILABLE）。dev/单机请另行启动："
                "python -m backend.workers.metadata_runner（生产由 systemd tdsql-metadata-runner 提供）")
    except Exception as e:
        logger.warning(f"runner 心跳探测失败（非致命）: {e}")
    logger.info("TDSQL SQL审核平台已就绪 (V%s)", config.APP_VERSION)
    yield
    # ── 关闭时 ──
    logger.info("TDSQL SQL审核平台关闭中...")
    try:
        from backend.services.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass
    try:
        from backend.services.connection_registry import registry
        registry.disconnect()
    except Exception:
        pass


app = FastAPI(
    title=config.APP_TITLE,
    version=config.APP_VERSION,
    description=config.APP_DESCRIPTION,
    lifespan=lifespan,
)


# v1.6.3.5 / DU-2 / SIT-B-03：MetadataJobError → 稳定 HTTP 错误（code+中文 message+request_id）
# 业务错误不再退化为裸 500；http_status 字段生效（如 runner 未就绪 503）。
@app.exception_handler(MetadataJobError)
async def _metadata_job_error_handler(request, exc):  # noqa: ANN001
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "detail": exc.message, "message": exc.message,
                 "request_id": getattr(request.state, "request_id", "") or ""},
        headers={"Cache-Control": "no-store"},
    )

# ── 中间件（注册顺序与执行顺序相反：请求先过RequestContext再过Auth） ──
# v1.6.3.4 / D05：GatewayUploadPolicyMiddleware 最先 add → 位于最内层，在 Auth
# 之后、路由之前执行（认证通过才取跨 worker 槽；表单解析前做网关专属字节限额）。
# 请求实际执行顺序：GZip → BodySizeLimit → RequestContext → Auth →
#                   GatewayUploadPolicy → 路由。
# BodySizeLimitMiddleware 对网关上传精确路由让专属策略决定，其他路由仍用原 50 MiB。
app.add_middleware(GatewayUploadPolicyMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(RequestContextMiddleware)

# 请求体大小上限：此前无限制，超大报文可直接打满内存
app.add_middleware(BodySizeLimitMiddleware)

# 响应压缩：首页与报告类响应均在 100KB 以上，内网带宽也不该白白浪费
app.add_middleware(GZipMiddleware, minimum_size=1024)

# CORS 配置（V2.0: 默认同源不下发跨域头；跨域部署时配置 CORS_ALLOW_ORIGINS）
_cors_origins = config.cors_allow_origins()
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# 注册API路由
app.include_router(auth_router)             # V2.0 认证与用户管理
app.include_router(audit_router)            # SQL审核
app.include_router(slow_query_router)       # 慢SQL分析
app.include_router(dashboard_router)        # Dashboard
app.include_router(gitlab_router)           # GitLab集成
app.include_router(tdsql_router)            # TDSQL管理
app.include_router(rules_router)            # 规则管理
app.include_router(rulesets_router)         # V2.0 规则集管理
app.include_router(system_config_router)    # V1.5 系统配置（全局默认实例类型）
app.include_router(project_router)          # 项目管理
app.include_router(bigtable_router)         # 大表治理
app.include_router(gate_router)             # 质量门禁
app.include_router(monitor_router)          # 监控告警
app.include_router(inspection_router)       # 巡检管理
app.include_router(cluster_inspect_router)  # G3 集群深度巡检
app.include_router(index_audit_router)      # G5 索引健康审计
app.include_router(schema_diff_router)      # G6 表结构比对
app.include_router(emergency_router)        # G7 应急诊断
app.include_router(daily_inspect_router)    # G4 每日巡检与趋势
app.include_router(sql_stats_router)        # G8 SQL调用量分析 + G9 大表趋势
app.include_router(admin_router)            # V2.0 系统管理
app.include_router(zk_discovery_router)     # G10 ZK 发现
app.include_router(gateway_log_router)      # G11 网关日志
app.include_router(ppt_report_router)       # G12 PPT 生成与大屏
app.include_router(toolkit_router)          # G13 运维工具箱
app.include_router(table_type_stats_router)  # G14 表类型统计
app.include_router(scan_compare_router)     # V1.3 扫描结果纵向对比
app.include_router(raw_slowlog_router)      # V1.5.3 原始慢日志采集（独立模块）
app.include_router(metadata_audit_router)   # v1.6.3.5 在线元数据审核任务

# 前端静态资源（V2.0: 本地化vendor资产，纯内网可用）
STATIC_DIR = FRONTEND_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """网站图标路由（避免浏览器请求 404）"""
    custom_logo = STATIC_DIR / "img" / "custom-logo.png"
    if custom_logo.exists():
        return FileResponse(str(custom_logo), media_type="image/png")
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/health", tags=["健康检查"])
async def health():
    """健康检查端点（存活探针）"""
    return {"status": "ok", "version": config.APP_VERSION}


@app.get("/metrics", tags=["可观测性"], response_class=PlainTextResponse,
         include_in_schema=False)
async def metrics():
    """Prometheus 指标端点"""
    if not config.metrics_enabled():
        return PlainTextResponse("metrics disabled", status_code=404)
    from backend.services.metrics_service import render_prometheus
    return PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4")


# 前端页面路由
@app.get("/", include_in_schema=False)
async def serve_frontend(ui: Optional[str] = None):
    """服务前端页面（双 UI 渐进式智能路由与离线降级）"""
    v2_dist_index = FRONTEND_DIR / "dist" / "index.html"
    if ui != "v1" and v2_dist_index.exists():
        return FileResponse(str(v2_dist_index))
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {
        "service": config.APP_TITLE,
        "version": config.APP_VERSION,
        "status": "running",
        "api_docs": "/docs",
        "modules": {
            "auth": "认证与用户管理 - POST /api/v1/auth/login",
            "sql_audit": "SQL审核（77条规则） - POST /api/v1/audit/sql",
            "slow_query": "慢SQL分析 - POST /api/v1/slow-queries",
            "dashboard": "统计概览 - GET /api/v1/dashboard/summary",
            "gitlab": "GitLab集成 - POST /api/v1/gitlab/webhook/merge-request",
            "tdsql": "TDSQL管理（多实例） - POST /api/v1/tdsql/connect",
            "rules": "规则管理 - GET /api/v1/rules",
            "rulesets": "规则集管理 - GET /api/v1/rulesets",
            "projects": "项目管理 - GET /api/v1/projects",
            "bigtable": "大表治理 - GET /api/v1/bigtable/report/{connection_id}",
            "gate": "质量门禁 - GET /api/v1/gate/rules/{project_id}",
            "monitor": "监控告警 - GET /api/v1/monitor/alerts",
            "inspection": "巡检管理 - GET /api/v1/inspection/tasks",
            "admin": "系统管理 - GET /api/v1/admin/info",
            "metrics": "Prometheus指标 - GET /metrics",
        },
    }
