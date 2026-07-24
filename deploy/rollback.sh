#!/usr/bin/env bash
# ============================================================================
# TDSQL数据库SQL审核工具 一键回滚脚本
# 用法:
#   ./deploy/rollback.sh                        # 默认回滚到上一版本
#   ./deploy/rollback.sh --version v1.2.0.5     # 指定回滚到的版本
# ============================================================================
set -euo pipefail

INSTALL_DIR="/opt/tdsql-sqlcheck"
TARGET_VERSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) INSTALL_DIR="$2"; shift 2;;
    --version) TARGET_VERSION="$2"; shift 2;;
    *) echo "未知参数: $1"; exit 1;;
  esac
done

log()  { echo -e "\033[32m[ROLLBACK]\033[0m $*"; }
fail() { echo -e "\033[31m[FAILED]\033[0m $*"; exit 1; }

RELEASES_DIR="${INSTALL_DIR}/releases"

if [[ -z "${TARGET_VERSION}" ]]; then
  # 查找上一最新版本
  ALL_RELEASES=$(ls -dt "${RELEASES_DIR}"/v* 2>/dev/null || true)
  CURRENT_LINK=$(readlink -f "${INSTALL_DIR}/current" 2>/dev/null || true)
  for r in ${ALL_RELEASES}; do
    if [[ "${r}" != "${CURRENT_LINK}" ]]; then
      TARGET_VERSION="$(basename "${r}")"
      break
    fi
  done
fi

[[ -n "${TARGET_VERSION}" ]] || fail "未找到可回滚的历史版本目标"
TARGET_DIR="${RELEASES_DIR}/${TARGET_VERSION}"
[[ -d "${TARGET_DIR}" ]] || fail "目标版本目录不存在: ${TARGET_DIR}"

log "开始回滚至版本: ${TARGET_VERSION}"

# 1. 停止当前服务
systemctl stop tdsql-sqlcheck || true

# 2. 切换 current 软链接
ln -sfn "${TARGET_DIR}" "${INSTALL_DIR}/current"

# 3. 启动回滚后的服务
systemctl start tdsql-sqlcheck || fail "回滚服务启动失败"

log "✅ 已成功回滚至 ${TARGET_VERSION}！"
log "服务状态检查: systemctl status tdsql-sqlcheck"
