#!/usr/bin/env bash
# ============================================================================
# TDSQL数据库SQL审核工具 v1.0.3 一键部署脚本
# 适用环境: 银河麒麟高级服务器版 V10 SP3 (x86_64 / aarch64)
# 元数据库: TDSQL 集中式实例 (MySQL 协议)
#
# 用法（在解压后的发布包根目录内执行，root 或有 sudo 权限的用户）:
#   ./deploy/install.sh                          # 默认安装到 /opt/tdsql-sqlcheck，端口 8000
#   ./deploy/install.sh --dir /opt/xxx --port 8080
#
# 前提: 已按 deploy/env.template 编辑好 deploy/.env（数据库连接等）
# ============================================================================
set -euo pipefail

VERSION="1.0.3"
INSTALL_DIR="/opt/tdsql-sqlcheck"
PORT="8000"
RUN_USER="sqlcheck"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"   # 发布包根目录（含 backend/ frontend/ wheels/）

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)  INSTALL_DIR="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --user) RUN_USER="$2"; shift 2;;
    *) echo "未知参数: $1"; exit 1;;
  esac
done

log()  { echo -e "\033[32m[INSTALL]\033[0m $*"; }
fail() { echo -e "\033[31m[FAILED]\033[0m $*"; exit 1; }

# ── 0. 预检 ─────────────────────────────────────────────────────────────
log "步骤0: 环境预检"
bash "${SCRIPT_DIR}/preflight_check.sh" --port "${PORT}" --pkg-root "${PKG_ROOT}" || fail "预检未通过，请先解决预检报告中的问题"

[[ -f "${SCRIPT_DIR}/.env" ]] || fail "缺少 ${SCRIPT_DIR}/.env，请复制 env.template 为 .env 并填写数据库等配置"

# ── 1. 选择 Python 解释器（≥3.9，优先 3.11）────────────────────────────
log "步骤1: 定位 Python 解释器"
PYBIN=""
for c in python3.11 python3.10 python3.9; do
  if command -v "$c" >/dev/null 2>&1; then PYBIN="$(command -v $c)"; break; fi
done
if [[ -z "$PYBIN" ]] && [[ -x "${PKG_ROOT}/python/bin/python3" ]]; then
  # 发布包内置便携 Python（make_release.sh --with-python 打包）
  mkdir -p "${INSTALL_DIR}"
  cp -a "${PKG_ROOT}/python" "${INSTALL_DIR}/python-runtime"
  PYBIN="${INSTALL_DIR}/python-runtime/bin/python3"
fi
[[ -n "$PYBIN" ]] || fail "未找到 python3.9+。方案A: 内网yum源安装(yum install -y python39)；方案B: 重新打包时加 --with-python 内置便携Python"
PYVER="$($PYBIN -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
log "使用 Python: ${PYBIN} (${PYVER})"

# ── 2. 创建运行用户与目录结构 ───────────────────────────────────────────
log "步骤2: 创建运行用户 ${RUN_USER} 与目录 ${INSTALL_DIR}"
id "${RUN_USER}" >/dev/null 2>&1 || useradd -r -M -s /sbin/nologin "${RUN_USER}"
mkdir -p "${INSTALL_DIR}"/{releases,logs,reports}
RELEASE_DIR="${INSTALL_DIR}/releases/v${VERSION}"
if [[ -d "${RELEASE_DIR}" ]]; then
  log "检测到同版本旧目录，备份为 ${RELEASE_DIR}.bak.$(date +%s)"
  mv "${RELEASE_DIR}" "${RELEASE_DIR}.bak.$(date +%s)"
fi
mkdir -p "${RELEASE_DIR}"

# ── 3. 复制代码 ─────────────────────────────────────────────────────────
log "步骤3: 部署代码到 ${RELEASE_DIR}"
cp -a "${PKG_ROOT}/backend" "${PKG_ROOT}/frontend" "${PKG_ROOT}/requirements.txt" "${RELEASE_DIR}/"
echo "${VERSION}" > "${RELEASE_DIR}/VERSION"

# ── 4. 虚拟环境 + 离线安装依赖 ─────────────────────────────────────────
log "步骤4: 创建 venv 并离线安装依赖（wheels/ 目录）"
"$PYBIN" -m venv "${RELEASE_DIR}/venv"
"${RELEASE_DIR}/venv/bin/pip" install --no-index --find-links "${PKG_ROOT}/wheels" --upgrade pip >/dev/null 2>&1 || true
"${RELEASE_DIR}/venv/bin/pip" install --no-index --find-links "${PKG_ROOT}/wheels" -r "${RELEASE_DIR}/requirements.txt" \
  || fail "离线依赖安装失败：检查 wheels/ 是否为目标架构($(uname -m))与Python${PYVER}打包"

# ── 5. 配置文件 ─────────────────────────────────────────────────────────
log "步骤5: 安装配置 ${INSTALL_DIR}/.env"
if [[ -f "${INSTALL_DIR}/.env" ]]; then
  log "保留既有 .env（新模板已放至 .env.new 供比对）"
  cp "${SCRIPT_DIR}/.env" "${INSTALL_DIR}/.env.new"
else
  cp "${SCRIPT_DIR}/.env" "${INSTALL_DIR}/.env"
fi
chmod 600 "${INSTALL_DIR}/.env"; chown "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}/.env"
grep -q "^AUTH_SECRET_KEY=..*" "${INSTALL_DIR}/.env" || {
  SECRET=$("$PYBIN" -c "import secrets;print(secrets.token_hex(32))")
  echo "AUTH_SECRET_KEY=${SECRET}" >> "${INSTALL_DIR}/.env"
  log "已自动生成并固化 AUTH_SECRET_KEY（务必随 .env 一起备份，丢失将导致全员token失效）"
}

# ── 6. 切换 current 软链 ────────────────────────────────────────────────
log "步骤6: 切换 current -> releases/v${VERSION}"
PREV_TARGET="$(readlink "${INSTALL_DIR}/current" 2>/dev/null || true)"
[[ -n "${PREV_TARGET}" ]] && echo "${PREV_TARGET}" > "${INSTALL_DIR}/.previous_release"
ln -sfn "${RELEASE_DIR}" "${INSTALL_DIR}/current"
chown -R "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}"

# ── 7. systemd 服务 ─────────────────────────────────────────────────────
log "步骤7: 安装 systemd 服务 tdsql-sqlcheck.service (端口 ${PORT})"
sed -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" -e "s|__PORT__|${PORT}|g" -e "s|__USER__|${RUN_USER}|g" \
    "${SCRIPT_DIR}/tdsql-sqlcheck.service" > /etc/systemd/system/tdsql-sqlcheck.service
systemctl daemon-reload
systemctl enable tdsql-sqlcheck >/dev/null 2>&1
systemctl restart tdsql-sqlcheck

# ── 8. 防火墙（如启用 firewalld）────────────────────────────────────────
if systemctl is-active firewalld >/dev/null 2>&1; then
  log "步骤8: firewalld 放通 ${PORT}/tcp"
  firewall-cmd --permanent --add-port="${PORT}/tcp" >/dev/null && firewall-cmd --reload >/dev/null
fi

# ── 9. 部署后验证 ───────────────────────────────────────────────────────
log "步骤9: 等待服务就绪并执行部署验证"
sleep 5
bash "${SCRIPT_DIR}/verify_deploy.sh" --port "${PORT}" || {
  echo "──── 最近日志 ────"; journalctl -u tdsql-sqlcheck -n 30 --no-pager || true
  fail "部署验证未通过（服务已安装，可修复配置后 systemctl restart tdsql-sqlcheck 并重跑 verify_deploy.sh）"
}

log "══════════════════════════════════════════════════"
log " 部署成功! TDSQL数据库SQL审核工具 v${VERSION}"
log " 访问地址: http://<本机IP>:${PORT}/"
log " 初始账号: admin / \${ADMIN_INITIAL_PASSWORD}(.env中配置)，首次登录强制改密"
log " 服务管理: systemctl {start|stop|restart|status} tdsql-sqlcheck"
log " 回滚: bash ${SCRIPT_DIR}/rollback.sh"
log "══════════════════════════════════════════════════"
