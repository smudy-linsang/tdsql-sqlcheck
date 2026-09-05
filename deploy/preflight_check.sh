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

VERSION="$(tr -d ' \r\n' < "${PKG_ROOT}/VERSION" 2>/dev/null || echo "1.6.3.2")"
echo "════ TDSQL SQL审核工具 v${VERSION} 部署预检 ════"

# 1. 操作系统
if grep -qiE "kylin" /etc/os-release 2>/dev/null; then
  ok "操作系统: $(grep PRETTY_NAME /etc/os-release | cut -d'"' -f2)"
else
  warn "非麒麟系统: $(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d'"' -f2)（可继续，但目标环境为麒麟V10SP3）"
fi
echo "  架构: $(uname -m) | 内核: $(uname -r)"

# 2. Python ≥3.9（优先使用麒麟/海光生产环境专用 Python /opt/python311）
PYOK=""
if [[ -x "/opt/python311/python/bin/python3.11" ]]; then
  PYOK="/opt/python311/python/bin/python3.11"
else
  for c in python3.11 python3.10 python3.9; do
    command -v "$c" >/dev/null 2>&1 && { PYOK="$c"; break; }
  done
fi
if [[ -n "$PYOK" ]]; then ok "Python: $($PYOK --version 2>&1) [${PYOK}]"
elif [[ -x "${PKG_ROOT}/python/bin/python3" ]]; then ok "使用发布包内置便携 Python: $(${PKG_ROOT}/python/bin/python3 --version 2>&1)"
else bad "无 python3.9+，且发布包未内置 Python。处理: 内网源 yum install -y python39，或重新打包加 --with-python"; fi

# 3. 端口占用（覆盖/增量升级场景下现有服务在跑，标记为 WARN 而非 FAIL 阻断）
if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
  warn "端口 ${PORT} 已被占用（在轨升级/增量部署场景下属于正常现象，部署后重启服务即可）"
else
  ok "端口 ${PORT} 空闲"
fi

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
if [[ ! -f "$ENVF" && -f "/opt/tdsql-sqlcheck/.env" ]]; then
  ENVF="/opt/tdsql-sqlcheck/.env"
fi
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
if command -v ssh >/dev/null 2>&1 && command -v ssh-keygen >/dev/null 2>&1 && ssh -G localhost >/dev/null 2>&1; then
  SSHV="$(ssh -V 2>&1 | head -1)"
  if [[ "$SSHV" =~ OpenSSH_([0-9]+\.[0-9]+) ]] && [[ "$(printf '7.4\n%s\n' "${BASH_REMATCH[1]}" | sort -V | head -1)" == "7.4" ]]; then
    ok "OpenSSH client: ${SSHV}（严格主机密钥校验配置可用）"
  else
    bad "OpenSSH 版本不满足 >=7.4: ${SSHV}"
  fi
else
  bad "缺少 OpenSSH client/ssh-keygen，或 ssh -G 不支持（原始慢日志采集前置条件）"
fi
AVAIL=$(df -m /opt 2>/dev/null | awk 'NR==2{print $4}')
[[ "${AVAIL:-0}" -ge 2048 ]] && ok "/opt 可用空间 ${AVAIL}MB" || warn "/opt 可用空间不足2GB(${AVAIL:-?}MB)"
command -v chronyc >/dev/null 2>&1 && chronyc tracking >/dev/null 2>&1 && ok "chrony 时钟同步正常" || warn "时钟同步未确认（审计日志时间戳依赖NTP）"

# 7. 后端可导入性（拦截导入期错误，如误删请求模型；规约 R-17）
PYIMP="${PYOK:-}"
[[ -z "$PYIMP" && -x "${PKG_ROOT}/python/bin/python3" ]] && PYIMP="${PKG_ROOT}/python/bin/python3"
if [[ -n "$PYIMP" ]]; then
  if [[ -f "$ENVF" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENVF"; set +a
  fi
  if (cd "${PKG_ROOT}" && "$PYIMP" -c "import backend.main" >/dev/null 2>&1); then
    ok "backend.main 可导入"
  else
    bad "backend.main 导入失败，禁止部署（运行 cd ${PKG_ROOT} && $PYIMP -c 'import backend.main' 查看堆栈）"
  fi
else
  warn "无可用 python3，跳过后端导入检查"
fi

# 8. v1.6.2.2-UAT-O-30 升级预检：历史 checksum 漂移与遗留开关
# - 历史遗留的长期开关 SCHEMA_CHECKSUM_RECONCILE 已被移除（长期变量可放行未来任意漂移），
#   若既有 .env 中残留该变量必须删除后重检；
# - v9_090_connection_unique 的历史 checksum 漂移由应用启动时按代码内精确三元组账本
#   自动一次性调和（无需运维手工改库），此处仅做状态提示与预警。
if [[ -f "$ENVF" ]] && grep -q "^SCHEMA_CHECKSUM_RECONCILE=" "$ENVF" 2>/dev/null; then
  bad ".env 残留已废弃的长期开关 SCHEMA_CHECKSUM_RECONCILE，请删除该行后重检（v1.6.2.2 起由代码内精确三元组账本一次性自动调和，任何手工开关均可绕过未来漂移检测）"
else
  ok "无遗留调和开关（SCHEMA_CHECKSUM_RECONCILE 已废弃并移除）"
fi
if command -v mysql >/dev/null 2>&1 && [[ -n "${H:-}" ]] && [[ -n "${SQLCHECK_DB_USER:-}" ]]; then
  DBNAME="${SQLCHECK_DB_NAME:-tdsql_sqlcheck}"
  DRIFT_STATE=$(mysql -h"$H" -P"$P" -u"${SQLCHECK_DB_USER}" -p"${SQLCHECK_DB_PASSWORD}" -N -e \
    "SELECT CASE \
       WHEN NOT EXISTS (SELECT 1 FROM \\`$DBNAME\\`.schema_migrations WHERE version_key='v9_090_connection_unique') THEN 'fresh' \
       WHEN EXISTS (SELECT 1 FROM \\`$DBNAME\\`.schema_migrations WHERE version_key='v9_090_connection_unique' AND checksum='c6cf33bb385456fef12af3d4888ea6b22dcfc2a64052d734adc4c37457915209') THEN 'current' \
       WHEN EXISTS (SELECT 1 FROM \\`$DBNAME\\`.schema_migrations WHERE version_key='v9_090_connection_unique' AND checksum='54ee2e97c804f5d8ec216d9f51600c19cc8463f2cede1de07fa67635abe6de28') THEN 'historical' \
       ELSE 'unknown' END" 2>/dev/null || echo "unreachable")
  case "$DRIFT_STATE" in
    fresh)      ok "迁移 v9_090：全新库，首次启动按当前 checksum 登记";;
    current)    ok "迁移 v9_090：checksum 已为当前值（无需调和）";;
    historical) warn "迁移 v9_090：检测到历史 checksum（老库升级场景），首次启动将由代码内精确账本自动一次性调和，无需人工改库；请确认已备份元数据库并关注启动日志中的调和审计记录";;
    unknown)    bad "迁移 v9_090：checksum 与已知新旧值均不符（疑似手工篡改），应用将失败关闭，请人工核实";;
    *)          warn "迁移 v9_090 状态未能读取（${DRIFT_STATE}），启动时按实际状态处理";;
  esac
fi

echo "════ 预检结果: PASS=${PASS} WARN=${WARN} FAIL=${FAILC} ════"
[[ "$FAILC" -eq 0 ]] || exit 1
exit 0
