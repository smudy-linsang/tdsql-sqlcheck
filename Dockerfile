# ── TDSQL SQL审核工具 - Docker镜像 ──
# 基于 Python 3.11 slim 镜像，使用 uvicorn 运行 FastAPI 应用

FROM python:3.11-slim AS base

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# 安装系统依赖
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# 创建工作目录
WORKDIR /app

# 先复制依赖文件，利用 Docker 缓存层
COPY pyproject.toml requirements.txt ./

# 安装 Python 依赖
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir \
        pymysql>=1.1.0 \
        apscheduler>=3.10.0 \
        reportlab>=4.0.0

# 复制项目源码
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# 创建数据目录
RUN mkdir -p /app/data

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# 启动命令（单worker模式，避免调度器重复执行和SQLite并发写入问题）
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
