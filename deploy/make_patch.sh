#!/usr/bin/env bash
set -e

# ==============================================================================
# TDSQL-SQLCheck V1.6.2.1 最小增量补丁包构建脚本
# ==============================================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=$(cat "$ROOT_DIR/VERSION" | tr -d ' \r\n')
PATCH_NAME="tdsql-sqlcheck-v${VERSION}-patch"
DIST_DIR="$ROOT_DIR/dist"
STAGE_DIR="$DIST_DIR/$PATCH_NAME"

echo "════════ 打包 TDSQL-SQLCheck 最小增量补丁 v${VERSION} ════════"
rm -rf "$STAGE_DIR" "$DIST_DIR/${PATCH_NAME}.tar.gz" "$DIST_DIR/${PATCH_NAME}.tar.gz.sha256"
mkdir -p "$STAGE_DIR/backend/services" "$STAGE_DIR/backend/api" "$STAGE_DIR/backend/engine/parser" "$STAGE_DIR/backend/engine/rules" "$STAGE_DIR/frontend/static/js" "$STAGE_DIR/frontend/static/css" "$STAGE_DIR/docs" "$DIST_DIR"

# 1. 复制最小必要变动文件
cp "$ROOT_DIR/VERSION" "$STAGE_DIR/"
cp "$ROOT_DIR/backend/config.py" "$STAGE_DIR/backend/"
cp "$ROOT_DIR/backend/main.py" "$STAGE_DIR/backend/"
cp "$ROOT_DIR/backend/services/connection_registry.py" "$STAGE_DIR/backend/services/"
cp "$ROOT_DIR/backend/services/auth_service.py" "$STAGE_DIR/backend/services/"
cp "$ROOT_DIR/backend/services/report_service.py" "$STAGE_DIR/backend/services/"
cp "$ROOT_DIR/backend/api/tdsql_manage.py" "$STAGE_DIR/backend/api/"
cp "$ROOT_DIR/backend/api/zk_discovery.py" "$STAGE_DIR/backend/api/"
cp "$ROOT_DIR/backend/api/sql_audit.py" "$STAGE_DIR/backend/api/"
cp "$ROOT_DIR/backend/engine/parser/parser_legacy.py" "$STAGE_DIR/backend/engine/parser/"
cp "$ROOT_DIR/backend/engine/rules/index.py" "$STAGE_DIR/backend/engine/rules/"
cp "$ROOT_DIR/backend/engine/rules/distributed.py" "$STAGE_DIR/backend/engine/rules/"
cp "$ROOT_DIR/frontend/index.html" "$STAGE_DIR/frontend/"
cp "$ROOT_DIR/frontend/static/js/app.js" "$STAGE_DIR/frontend/static/js/"
cp "$ROOT_DIR/frontend/static/css/theme-dark-blue.css" "$STAGE_DIR/frontend/static/css/"
[ -f "$ROOT_DIR/docs/v1.6.2.1_upgrade_manual.md" ] && cp "$ROOT_DIR/docs/v1.6.2.1_upgrade_manual.md" "$STAGE_DIR/docs/"
[ -f "$ROOT_DIR/docs/V1.6.2.1增量更新部署说明.md" ] && cp "$ROOT_DIR/docs/V1.6.2.1增量更新部署说明.md" "$STAGE_DIR/docs/"
[ -f "$ROOT_DIR/docs/V1.6.2.1全量更新部署说明.md" ] && cp "$ROOT_DIR/docs/V1.6.2.1全量更新部署说明.md" "$STAGE_DIR/docs/"
[ -f "$ROOT_DIR/docs/REPORT-v1.6.2.1-独立质检验收报告.md" ] && cp "$ROOT_DIR/docs/REPORT-v1.6.2.1-独立质检验收报告.md" "$STAGE_DIR/docs/"
[ -f "$ROOT_DIR/docs/REPORT-v1.6.2.0-独立质检验收报告.md" ] && cp "$ROOT_DIR/docs/REPORT-v1.6.2.0-独立质检验收报告.md" "$STAGE_DIR/docs/"
[ -f "$ROOT_DIR/docs/ANALYSIS-v1.6.2.0-上线后扫描结果分析_A.md" ] && cp "$ROOT_DIR/docs/ANALYSIS-v1.6.2.0-上线后扫描结果分析_A.md" "$STAGE_DIR/docs/"
cp "$ROOT_DIR/deploy/apply_patch.sh" "$STAGE_DIR/"
chmod +x "$STAGE_DIR/apply_patch.sh"

# 2. 打包与校验
cd "$DIST_DIR"
tar -czvf "${PATCH_NAME}.tar.gz" "$PATCH_NAME"
if command -v sha256sum &>/dev/null; then
    sha256sum "${PATCH_NAME}.tar.gz" > "${PATCH_NAME}.tar.gz.sha256"
elif command -v shasum &>/dev/null; then
    shasum -a 256 "${PATCH_NAME}.tar.gz" > "${PATCH_NAME}.tar.gz.sha256"
fi

rm -rf "$STAGE_DIR"

echo "══════════════════════════════════════════════════════════════════"
echo " 增量补丁包: dist/${PATCH_NAME}.tar.gz"
echo " 校验和:     dist/${PATCH_NAME}.tar.gz.sha256"
echo "══════════════════════════════════════════════════════════════════"
