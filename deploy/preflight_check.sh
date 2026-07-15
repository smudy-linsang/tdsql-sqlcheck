#!/usr/bin/env bash
# 部署前环境预检（麒麟V10 SP3 / TDSQL集中式元数据库）
# 用法: ./preflight_check.sh [--port 8000] [--pkg-root <发布包根目录>]
set -uo pipefail
PORT=8000; PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
while [[ $# -gt 0 ]]; do case "$1" in
  --port) PORT="$2"; shift 2;; --pkg-root) PKG_ROOT="$2"; shift 2;; *) shift;; esac; done

PASS=0; WARN=0; FAILC=0
ok()   { echo "  [PASS] $*"; PASS=$((PASS+1)); }
warn() { echo "  [WARN] $*"; WARN=$((WARN+1)); }
bad()  { echo "  [FAIL] $*"; FAILC=$((FAILC+1)); }

echo "════ TDSQL SQL审核工具 v1.0.4.4 部署预检 ════"

# 1. 操作系统
if grep -qiE "kylin" /etc/os-release 2>/dev/null; then
  ok "操作系统: $(grep PRETTY_NAME /etc/os-release | cut -d'"' -f2)"
else
  warn "非麒麟系统: $(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d'"' -f2)（可继续，但目标环境为麒麟V10SP3）"
fi
echo "  架构: $(uname -m) | 内核: $(uname -r)"

# 2. Python ≥3.9
PYOK=""
for c in python3.11 python3.10 python3.9; do
  command -v "$c" >/dev/null 2>&1 && { PYOK="$c"; break; }
done
if [[ -n "$PYOK" ]]; then ok "Python: $($PYOK --version 2>&1)"
elif [[ -x "${PKG_ROOT}/python/bin/python3" ]]; then ok "使用发布包内置便携 Python: $(${PKG_ROOT}/python/bin/python3 --version 2>&1)"
else bad "无 python3.9+，且发布包未内置 Python。处理: 内网源 yum install -y python39，或重新打包加 --with-python"; fi

# 3. 端口占用
if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then bad "端口 ${PORT} 已被占用"; else ok "端口 ${PORT} 空闲"; fi

# 4. wheels 完整性（架构匹配）
if [[ -d "${PKG_ROOT}/wheels" ]]; then
  N=$(ls "${PKG_ROOT}/wheels"/*.whl 2>/dev/null | wc -l)
  ARCH=$(uname -m)
  BADARCH=$(ls "${PKG_ROOT}/wheels" | grep -cE "manylinux.*(x86_64|aarch64)" | head -1)
  if [[ "$N" -ge 9 ]]; then ok "wheels 离线依赖: ${N} 个"; else bad "wheels 目录不完整(${N}个)，请用 make_release.sh 重新打包"; fi
  if ls "${PKG_ROOT}/wheels" | grep -qE "manylinux" && ! ls "${PKG_ROOT}/wheels" | grep -q "${ARCH}"; then
    bad "wheels 架构与本机(${ARCH})不匹配，请用 make_release.sh --arch ${ARCH} 重新打包"
  fi
else bad "缺少 wheels/ 目录（离线依赖）"; fi

# 5. TDSQL 集中式元数据库连通性（读取 .env）
ENVF="${PKG_ROOT}/deploy/.env"
if [[ -f "$ENVF" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENVF"; set +a
  H="${SQLCHECK_DB_HOST:-}"; P="${SQLCHECK_DB_PORT:-3306}"
  if [[ -n "$H" ]]; then
    if (echo > "/dev/tcp/${H}/${P}") >/dev/null 2>&1; then
      ok "元数据库 TCP 可达: ${H}:${P}"
      if command -v mysql >/dev/null 2>&1; then
        if mysql -h"$H" -P"$P" -u"${SQLCHECK_DB_USER}" -p"${SQLCHECK_DB_PASSWORD}" \
             -e "SELECT 1" >/dev/null 2>&1; then
          ok "元数据库账号认证通过"
          CHARSET=$(mysql -h"$H" -P"$P" -u"${SQLCHECK_DB_USER}" -p"${SQLCHECK_DB_PASSWORD}" -N \
            -e "SHOW VARIABLES LIKE 'character_set_server'" 2>/dev/null | awk '{print $2}')
          [[ "$CHARSET" == utf8mb4* ]] && ok "server字符集: ${CHARSET}" || warn "server字符集为 ${CHARSET}，建议库级显式 utf8mb4（建库语句已在部署手册）"
        else bad "元数据库账号认证失败（核对 SQLCHECK_DB_USER/PASSWORD 与授权）"; fi
      else warn "本机无 mysql 客户端，跳过认证与字符集检查（TCP已通）"; fi
    else bad "元数据库 TCP 不可达: ${H}:${P}（检查网络策略/安全组）"; fi
  else bad ".env 中 SQLCHECK_DB_HOST 未配置"; fi
  # 关键生产开关
  [[ "${AUTH_ENABLED:-}" == "true" ]] && ok "AUTH_ENABLED=true（生产必须）" || bad "AUTH_ENABLED 必须为 true"
  [[ -n "${ADMIN_INITIAL_PASSWORD:-}" ]] && ok "ADMIN_INITIAL_PASSWORD 已设置" || bad "ADMIN_INITIAL_PASSWORD 未设置"
  [[ "${GITLAB_WEBHOOK_ALLOW_INSECURE:-false}" == "false" ]] && ok "Webhook 严格校验开启" || warn "GITLAB_WEBHOOK_ALLOW_INSECURE=true（生产建议false）"
else
  bad "缺少 deploy/.env（复制 env.template 为 .env 并填写）"
fi

# 6. systemd / 磁盘 / 时钟
command -v systemctl >/dev/null 2>&1 && ok "systemd 可用" || bad "无 systemd"
AVAIL=$(df -m /opt 2>/dev/null | awk 'NR==2{print $4}')
[[ "${AVAIL:-0}" -ge 2048 ]] && ok "/opt 可用空间 ${AVAIL}MB" || warn "/opt 可用空间不足2GB(${AVAIL:-?}MB)"
command -v chronyc >/dev/null 2>&1 && chronyc tracking >/dev/null 2>&1 && ok "chrony 时钟同步正常" || warn "时钟同步未确认（审计日志时间戳依赖NTP）"

echo "════ 预检结果: PASS=${PASS} WARN=${WARN} FAIL=${FAILC} ════"
[[ "$FAILC" -eq 0 ]] || exit 1
exit 0
