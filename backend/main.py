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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
from backend.api.schema_diff import router as schema_diff_router
from backend.api.emergency import router as emergency_router
from backend.api.daily_inspect import router as daily_inspect_router
from backend.api.sql_stats import router as sql_stats_router
# V2.0 新增路由
from backend.api.auth import router as auth_router
from backend.api.rulesets import router as rulesets_router
from backend.api.admin import router as admin_router
from backend.middleware import AuthMiddleware, RequestContextMiddleware

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
        from backend.services.database import ensure_db, init_rule_configs
        ensure_db()
        init_rule_configs()
        logger.info("数据库初始化完成 (V2.0, 27张表)")
    except Exception as e:
        logger.warning(f"数据库初始化失败（非致命）: {e}")
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

# ── 中间件（注册顺序与执行顺序相反：请求先过RequestContext再过Auth） ──
app.add_middleware(AuthMiddleware)
app.add_middleware(RequestContextMiddleware)

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

# 前端静态资源（V2.0: 本地化vendor资产，纯内网可用）
STATIC_DIR = FRONTEND_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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
async def serve_frontend():
    """服务前端页面"""
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
