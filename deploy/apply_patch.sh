#!/usr/bin/env bash
set -e

# ==============================================================================
# TDSQL-SQLCheck V1.6.1.8 增量热补丁应用脚本
# ==============================================================================

TARGET_DIR="${1:-.}"

if [ ! -f "$TARGET_DIR/VERSION" ] && [ -f "./VERSION" ]; then
    TARGET_DIR="."
fi

if [ ! -d "$TARGET_DIR/backend" ] || [ ! -d "$TARGET_DIR/frontend" ]; then
    echo "❌ 错误: 目标目录 $TARGET_DIR 不是有效的 TDSQL-SQLCheck 安装根目录！"
    echo "用法: sudo ./apply_patch.sh [/path/to/tdsql-sqlcheck]"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_VER=$(cat "$TARGET_DIR/VERSION" 2>/dev/null || echo "unknown")
BACKUP_DIR="$TARGET_DIR/backup_pre_v1.6.1.8_$(date +%Y%m%d_%H%M%S)"

echo "══════════════════════════════════════════════════════════════════"
echo "  TDSQL-SQLCheck V1.6.1.8 增量热补丁部署"
echo "  目标路径: $(cd "$TARGET_DIR" && pwd)"
echo "  当前版本: $CURRENT_VER"
echo "  升级目标: 1.6.1.8"
echo "══════════════════════════════════════════════════════════════════"

# 1. 备份原文件
echo "[1/4] 备份变动前的文件至 $BACKUP_DIR ..."
mkdir -p "$BACKUP_DIR/backend/services" "$BACKUP_DIR/backend/api" "$BACKUP_DIR/frontend/static/js" "$BACKUP_DIR/frontend/static/css" "$BACKUP_DIR/docs"
[ -f "$TARGET_DIR/VERSION" ] && cp "$TARGET_DIR/VERSION" "$BACKUP_DIR/"
[ -f "$TARGET_DIR/backend/config.py" ] && cp "$TARGET_DIR/backend/config.py" "$BACKUP_DIR/backend/"
[ -f "$TARGET_DIR/backend/services/connection_registry.py" ] && cp "$TARGET_DIR/backend/services/connection_registry.py" "$BACKUP_DIR/backend/services/"
[ -f "$TARGET_DIR/backend/services/auth_service.py" ] && cp "$TARGET_DIR/backend/services/auth_service.py" "$BACKUP_DIR/backend/services/"
[ -f "$TARGET_DIR/backend/api/tdsql_manage.py" ] && cp "$TARGET_DIR/backend/api/tdsql_manage.py" "$BACKUP_DIR/backend/api/"
[ -f "$TARGET_DIR/backend/api/zk_discovery.py" ] && cp "$TARGET_DIR/backend/api/zk_discovery.py" "$BACKUP_DIR/backend/api/"
[ -f "$TARGET_DIR/frontend/index.html" ] && cp "$TARGET_DIR/frontend/index.html" "$BACKUP_DIR/frontend/"
[ -f "$TARGET_DIR/frontend/static/js/app.js" ] && cp "$TARGET_DIR/frontend/static/js/app.js" "$BACKUP_DIR/frontend/static/js/"
[ -f "$TARGET_DIR/frontend/static/css/theme-dark-blue.css" ] && cp "$TARGET_DIR/frontend/static/css/theme-dark-blue.css" "$BACKUP_DIR/frontend/static/css/"

# 2. 覆盖增量更新文件
echo "[2/4] 写入 V1.6.1.8 增量补丁文件..."
cp "$SCRIPT_DIR/VERSION" "$TARGET_DIR/VERSION"
cp "$SCRIPT_DIR/backend/config.py" "$TARGET_DIR/backend/config.py"
cp "$SCRIPT_DIR/backend/services/connection_registry.py" "$TARGET_DIR/backend/services/connection_registry.py"
cp "$SCRIPT_DIR/backend/services/auth_service.py" "$TARGET_DIR/backend/services/auth_service.py"
cp "$SCRIPT_DIR/backend/api/tdsql_manage.py" "$TARGET_DIR/backend/api/tdsql_manage.py"
cp "$SCRIPT_DIR/backend/api/zk_discovery.py" "$TARGET_DIR/backend/api/zk_discovery.py"
cp "$SCRIPT_DIR/frontend/index.html" "$TARGET_DIR/frontend/index.html"
cp "$SCRIPT_DIR/frontend/static/js/app.js" "$TARGET_DIR/frontend/static/js/app.js"
[ -f "$SCRIPT_DIR/frontend/static/css/theme-dark-blue.css" ] && cp "$SCRIPT_DIR/frontend/static/css/theme-dark-blue.css" "$TARGET_DIR/frontend/static/css/"
mkdir -p "$TARGET_DIR/docs"
cp -r "$SCRIPT_DIR/docs/"* "$TARGET_DIR/docs/" 2>/dev/null || true

# 3. 重启服务
echo "[3/4] 重启 TDSQL-SQLCheck 后台服务..."
if systemctl is-active --quiet tdsql-sqlcheck 2>/dev/null; then
    echo "检测到 systemd 托管服务 tdsql-sqlcheck，执行 systemctl restart..."
    systemctl restart tdsql-sqlcheck
elif command -v supervisorctl &>/dev/null && supervisorctl status tdsql-sqlcheck &>/dev/null; then
    echo "检测到 supervisord 托管服务 tdsql-sqlcheck，执行 restart..."
    supervisorctl restart tdsql-sqlcheck
else
    echo "尝试重启 Python 后台进程..."
    pkill -f "uvicorn backend.main:app" || true
    sleep 1
    if [ -f "$TARGET_DIR/venv/bin/python" ]; then
        nohup "$TARGET_DIR/venv/bin/python" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 > "$TARGET_DIR/sqlcheck.log" 2>&1 &
    else
        nohup python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 > "$TARGET_DIR/sqlcheck.log" 2>&1 &
    fi
fi

# 4. 健康检查
echo "[4/4] 检查服务状态与版本号..."
sleep 2
for i in {1..10}; do
    HEALTH_OUT=$(curl -s http://127.0.0.1:8000/health 2>/dev/null || true)
    if [[ "$HEALTH_OUT" == *"1.6.1.8"* ]]; then
        echo "✅ 升级成功！服务已就绪，当前版本: 1.6.1.8"
        echo "══════════════════════════════════════════════════════════════════"
        exit 0
    fi
    sleep 1
done

echo "⚠️ 提示: 服务正在拉起或请手动确认 curl http://127.0.0.1:8000/health"
