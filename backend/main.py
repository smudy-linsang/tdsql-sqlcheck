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
from backend.api.rules import router as rules_router
# V1.0 新增路由
from backend.api.project import router as project_router
from backend.api.bigtable import router as bigtable_router
from backend.api.quality_gate import router as gate_router
from backend.api.monitor import router as monitor_router
from backend.api.inspection import router as inspection_router

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
        from backend.services.database import ensure_db, init_rule_configs
        ensure_db()
        init_rule_configs()
        logger.info("数据库初始化完成 (V1.0, 20张表)")
    except Exception as e:
        logger.warning(f"数据库初始化失败（非致命）: {e}")
    try:
        from backend.services.scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        logger.warning(f"定时任务调度器启动失败（非致命）: {e}")
    logger.info("TDSQL SQL审核工具已就绪 (V1.0)")
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
    version="1.0.0",
    description="覆盖开发、测试、生产全生命周期的SQL质量管控与慢SQL分析工具（V1.0 - 76条规则+慢SQL六维分析+大表治理+质量门禁）",
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
app.include_router(slow_query_router)       # 慢SQL分析
app.include_router(dashboard_router)       # Dashboard
app.include_router(gitlab_router)          # GitLab集成
app.include_router(tdsql_router)           # TDSQL管理
app.include_router(rules_router)            # 规则管理
# V1.0 新增路由
app.include_router(project_router)         # 项目管理
app.include_router(bigtable_router)        # 大表治理
app.include_router(gate_router)            # 质量门禁
app.include_router(monitor_router)         # 监控告警
app.include_router(inspection_router)      # 巡检管理


@app.get("/health", tags=["健康检查"])
async def health():
    """健康检查端点"""
    return {"status": "ok", "version": "1.0.0"}


# 前端页面路由
@app.get("/", include_in_schema=False)
async def serve_frontend():
    """服务前端页面"""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {
        "service": "TDSQL SQL审核工具",
        "version": "1.0.0",
        "status": "running",
        "api_docs": "http://localhost:8000/docs",
        "modules": {
            "sql_audit": "SQL审核（76条规则） - POST /api/v1/audit/sql",
            "slow_query": "慢SQL分析 - POST /api/v1/slow-queries",
            "dashboard": "统计概览 - GET /api/v1/dashboard/summary",
            "gitlab": "GitLab集成 - POST /api/v1/gitlab/webhook/merge-request",
            "tdsql": "TDSQL管理 - POST /api/v1/tdsql/connect",
            "rules": "规则管理 - GET /api/v1/rules",
            "projects": "项目管理 - GET /api/v1/projects",
            "bigtable": "大表治理 - GET /api/v1/bigtable/report/{connection_id}",
            "gate": "质量门禁 - GET /api/v1/gate/rules/{project_id}",
            "monitor": "监控告警 - GET /api/v1/monitor/alerts",
            "inspection": "巡检管理 - GET /api/v1/inspection/tasks",
        },
    }
