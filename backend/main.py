"""
TDSQL SQL审核工具 - FastAPI 入口

启动方式: python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
访问前端: http://localhost:8000
访问API文档: http://localhost:8000/docs
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.api.sql_audit import router as audit_router
from backend.api.slow_query import router as slow_query_router
from backend.api.dashboard import router as dashboard_router
from backend.api.gitlab_hook import router as gitlab_router
from backend.api.tdsql_manage import router as tdsql_router

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
    logger.info("TDSQL SQL审核工具启动中...")
    try:
        from backend.services.scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        logger.warning(f"定时任务调度器启动失败（非致命）: {e}")
    logger.info("TDSQL SQL审核工具已就绪")
    yield
    # ── 关闭时 ──
    logger.info("TDSQL SQL审核工具关闭中...")
    try:
        from backend.services.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass


app = FastAPI(
    title="TDSQL SQL审核工具",
    version="0.4.0",
    description="覆盖开发、测试、生产全生命周期的SQL质量管控与慢SQL分析工具",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册API路由
app.include_router(audit_router)          # SQL审核
app.include_router(slow_query_router)     # 慢SQL分析
app.include_router(dashboard_router)      # Dashboard
app.include_router(gitlab_router)         # GitLab集成
app.include_router(tdsql_router)          # TDSQL管理


@app.get("/health", tags=["健康检查"])
async def health():
    """健康检查端点"""
    return {"status": "ok", "version": "0.4.0"}


# 前端页面路由
@app.get("/", include_in_schema=False)
async def serve_frontend():
    """服务前端页面"""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {
        "service": "TDSQL SQL审核工具",
        "version": "0.4.0",
        "status": "running",
        "api_docs": "http://localhost:8000/docs",
        "modules": {
            "sql_audit": "SQL审核（22条规则） - POST /api/v1/audit/sql",
            "slow_query": "慢SQL分析 - POST /api/v1/slow-queries",
            "dashboard": "统计概览 - GET /api/v1/dashboard/summary",
            "gitlab": "GitLab集成 - POST /api/v1/gitlab/webhook/merge-request",
            "tdsql": "TDSQL管理 - POST /api/v1/tdsql/connect",
            "scheduler": "定时任务 - GET /api/v1/tdsql/scheduler/status",
            "report": "审核报告导出 - GET /api/v1/audit/report/{id}/export",
        },
    }
