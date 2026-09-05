#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# TDSQL-SQLCheck 增量更新补丁包构建脚本 (无 wheels 依赖包，秒级轻量交付)
# ==============================================================================

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=$(cat "$ROOT_DIR/VERSION" | tr -d ' \r\n')
PATCH_NAME="tdsql-sqlcheck-v${VERSION}-patch"
DIST_DIR="$ROOT_DIR/dist"
STAGE_DIR="$DIST_DIR/$PATCH_NAME"

echo "════════ 打包 TDSQL-SQLCheck 增量更新补丁 v${VERSION} ════════"
rm -rf "$STAGE_DIR" "$DIST_DIR/${PATCH_NAME}.tar.gz" "$DIST_DIR/${PATCH_NAME}.tar.gz.sha256"
mkdir -p "$STAGE_DIR" "$DIST_DIR"

# 1. 复制全量应用代码与运维资产（排除临时文件和缓存）
echo "[1/3] 收集应用代码、前端资源、部署脚本与操作手册..."
cp "$ROOT_DIR/VERSION" "$STAGE_DIR/"
cp "$ROOT_DIR/requirements.txt" "$STAGE_DIR/"
cp -a "$ROOT_DIR/backend" "$STAGE_DIR/"
cp -a "$ROOT_DIR/frontend" "$STAGE_DIR/"
cp -a "$ROOT_DIR/deploy" "$STAGE_DIR/"

mkdir -p "$STAGE_DIR/docs"
cp "$ROOT_DIR/docs/DEPLOY-v${VERSION}-内网生产环境增量更新部署手册.md" "$STAGE_DIR/docs/" 2>/dev/null || true
cp "$ROOT_DIR/docs/DEPLOY-v${VERSION}-内网测试环境增量更新部署手册.md" "$STAGE_DIR/docs/" 2>/dev/null || true
cp "$ROOT_DIR/docs/PRODUCTION-DEPLOY-ISSUES-v1.6.3.0.md" "$STAGE_DIR/docs/" 2>/dev/null || true
cp "$ROOT_DIR/docs/GATE-DECISION-v${VERSION}-生产发布门禁签署决议与整改任务书.md" "$STAGE_DIR/docs/" 2>/dev/null || true
cp "$ROOT_DIR/docs/GATE-v${VERSION}-生产发布三项书面门禁发起.md" "$STAGE_DIR/docs/" 2>/dev/null || true

# 清理 __pycache__ 及临时文件
find "$STAGE_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$STAGE_DIR" -name "*.pyc" -delete 2>/dev/null || true
find "$STAGE_DIR" -name "*.pyo" -delete 2>/dev/null || true

# 2. 打包与校验
echo "[2/3] 生成 tar.gz 压缩归档..."
cd "$DIST_DIR"
tar -czf "${PATCH_NAME}.tar.gz" "$PATCH_NAME"

echo "[3/3] 计算 SHA256 哈希校验和..."
if command -v sha256sum &>/dev/null; then
    sha256sum "${PATCH_NAME}.tar.gz" > "${PATCH_NAME}.tar.gz.sha256"
elif command -v shasum &>/dev/null; then
    shasum -a 256 "${PATCH_NAME}.tar.gz" > "${PATCH_NAME}.tar.gz.sha256"
fi

rm -rf "$STAGE_DIR"

echo "══════════════════════════════════════════════════════════════════"
echo " 增量更新补丁包: dist/${PATCH_NAME}.tar.gz"
echo " 校验和文件:     dist/${PATCH_NAME}.tar.gz.sha256"
echo " 包内已包含生产与测试增量部署手册: docs/DEPLOY-v${VERSION}-内网生产环境增量更新部署手册.md"
echo "══════════════════════════════════════════════════════════════════"
