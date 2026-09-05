#!/usr/bin/env bash
# ============================================================================
# TDSQL-SQLCheck v1.6.3.2 增量更新部署脚本
# 适用目标: 内网测试环境 (10.243.16.252) / 生产环境 (10.243.16.238)
# 基线版本: v1.6.3.0 ➔ 目标版本: v1.6.3.2
#
# 特点与安全防呆:
#   1. 零依赖安装风险: 复用 v1.6.3.0 既有健全的 venv，避开麒麟系统 Python encodings 损坏问题；
#   2. 规范 Releases 隔离: 创建 releases/v1.6.3.2 目录，支持软链接原子切换与秒级回滚；
#   3. 密钥绝对延续: 自动从 v1.6.3.0 继承 data/encryption.key 与现网 .env，连接零中断；
#   4. 完整保留 deploy/: 将运维与验证脚本一同部署到 release 目录；
#   5. 自动化在轨验证: 升级完成后自动调用 verify_deploy.sh 执行 12 项合规检验。
# ============================================================================
set -euo pipefail

INSTALL_DIR="${1:-/opt/tdsql-sqlcheck}"
PORT="${2:-8000}"
RUN_USER="sqlcheck"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

log()  { echo -e "\033[32m[UPGRADE]\033[0m $*"; }
warn() { echo -e "\033[33m[WARN]\033[0m $*"; }
fail() { echo -e "\033[31m[FAILED]\033[0m $*"; exit 1; }

VERSION="$(tr -d ' \r\n' < "${PKG_ROOT}/VERSION" 2>/dev/null || echo "1.6.3.2")"
log "════════ TDSQL-SQLCheck v${VERSION} 增量升级 ════════"
log "目标安装根目录: ${INSTALL_DIR}"
log "发布介质根目录: ${PKG_ROOT}"

# 1. 检查既有环境
[[ -d "${INSTALL_DIR}" ]] || fail "目标安装目录 ${INSTALL_DIR} 不存在，请先执行全量安装！"
CURRENT_LINK="$(readlink "${INSTALL_DIR}/current" 2>/dev/null || true)"
[[ -n "${CURRENT_LINK}" ]] || fail "未找到 ${INSTALL_DIR}/current 软链接，请检查存量部署结构"

OLD_VERSION="unknown"
if [[ -f "${CURRENT_LINK}/VERSION" ]]; then
  OLD_VERSION="$(tr -d ' \r\n' < "${CURRENT_LINK}/VERSION")"
fi
log "当前运行版本: ${OLD_VERSION} (${CURRENT_LINK})"

# 2. 创建新 release 目录
TARGET_RELEASE="${INSTALL_DIR}/releases/v${VERSION}"
if [[ -d "${TARGET_RELEASE}" ]]; then
  BACKUP_DIR="${TARGET_RELEASE}.bak.$(date +%Y%m%d%H%M%S)"
  log "检测到已存在同版本目录，备份至: ${BACKUP_DIR}"
  mv "${TARGET_RELEASE}" "${BACKUP_DIR}"
fi
mkdir -p "${TARGET_RELEASE}"

# 3. 部署增量代码与资源
log "[1/6] 部署 v${VERSION} 增量应用代码、静态资产与运维脚本..."
cp -a "${PKG_ROOT}/backend" "${TARGET_RELEASE}/"
cp -a "${PKG_ROOT}/frontend" "${TARGET_RELEASE}/"
cp -a "${PKG_ROOT}/deploy" "${TARGET_RELEASE}/"
cp -a "${PKG_ROOT}/requirements.txt" "${TARGET_RELEASE}/"
echo "${VERSION}" > "${TARGET_RELEASE}/VERSION"
if [[ -d "${PKG_ROOT}/docs" ]]; then
  mkdir -p "${TARGET_RELEASE}/docs"
  cp -a "${PKG_ROOT}/docs/"* "${TARGET_RELEASE}/docs/" 2>/dev/null || true
fi

# 4. 复用既有 venv（免离线安装依赖，秒级且安全）
log "[2/6] 复用既有虚拟环境 (venv)..."
if [[ -d "${CURRENT_LINK}/venv" ]]; then
  cp -a "${CURRENT_LINK}/venv" "${TARGET_RELEASE}/venv"
  log "已成功从 ${CURRENT_LINK}/venv 继承虚拟环境"
else
  fail "在旧版本 ${CURRENT_LINK} 中未找到 venv 目录！"
fi

# 5. 延续 encryption.key 密钥与配置
log "[3/6] 同步并校验加密密钥 (encryption.key)..."
mkdir -p "${TARGET_RELEASE}/data"
if [[ -f "${CURRENT_LINK}/data/encryption.key" ]]; then
  cp "${CURRENT_LINK}/data/encryption.key" "${TARGET_RELEASE}/data/encryption.key"
  log "已从旧版本同步 data/encryption.key"
elif [[ -f "${INSTALL_DIR}/data/encryption.key" ]]; then
  cp "${INSTALL_DIR}/data/encryption.key" "${TARGET_RELEASE}/data/encryption.key"
  log "已从 ${INSTALL_DIR}/data/encryption.key 继承密钥"
fi

# 确保存量 .env 存在
[[ -f "${INSTALL_DIR}/.env" ]] || fail "未找到 ${INSTALL_DIR}/.env 配置文件！"
chmod 600 "${INSTALL_DIR}/.env"

# 6. 原子切换 current 软链接
log "[4/6] 切换 current 软链接 ➔ releases/v${VERSION}..."
echo "${CURRENT_LINK}" > "${INSTALL_DIR}/.previous_release"
ln -sfn "${TARGET_RELEASE}" "${INSTALL_DIR}/current"
id "${RUN_USER}" >/dev/null 2>&1 && chown -R "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}"

# 7. 重启 systemd 服务
log "[5/6] 重启 systemd 服务 tdsql-sqlcheck..."
if systemctl is-active tdsql-sqlcheck >/dev/null 2>&1; then
  systemctl restart tdsql-sqlcheck
  log "systemctl restart tdsql-sqlcheck 执行成功"
elif command -v systemctl >/dev/null 2>&1; then
  systemctl restart tdsql-sqlcheck 2>/dev/null || systemctl start tdsql-sqlcheck
else
  warn "非 systemd 环境，尝试杀掉旧进程并以后台进程方式拉起"
  pkill -f "uvicorn backend.main:app" || true
  sleep 1
  nohup "${TARGET_RELEASE}/venv/bin/python" -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT}" > "${INSTALL_DIR}/logs/app.log" 2>&1 &
fi

# 8. 执行部署验证
log "[6/6] 等待服务就绪并执行部署后验证..."
sleep 5
VERIFY_SCRIPT="${TARGET_RELEASE}/deploy/verify_deploy.sh"
if [[ -f "${VERIFY_SCRIPT}" ]]; then
  chmod +x "${VERIFY_SCRIPT}"
  bash "${VERIFY_SCRIPT}" --port "${PORT}"
else
  warn "未找到 ${VERIFY_SCRIPT}，尝试通过 curl 验证健康探针"
  curl -fsS "http://127.0.0.1:${PORT}/health"
fi

log "══════════════════════════════════════════════════════════════════"
log " ✅ v${VERSION} 增量升级已圆满完成！"
log " 当前版本: $(cat "${INSTALL_DIR}/current/VERSION")"
log " 如需回滚: ln -sfn ${CURRENT_LINK} ${INSTALL_DIR}/current && systemctl restart tdsql-sqlcheck"
log "══════════════════════════════════════════════════════════════════"
