#!/usr/bin/env bash
# ============================================================================
# v1.6.4.0 / CP-1：Copilot 紧急停用（DETAIL §16.6，N-05）
#
# 用法：sudo bash deploy/copilot_emergency_disable.sh --incident-id INCIDENT_ID
#
# 纪律：
#   · 只接受受限事件编号与固定本模块路径/unit，不把用户输入变成任意命令；
#   · 立即停止新受理（DB 写 settings.enabled=false/accepting=false + 版本目录外
#     持久停用标记）；DB 不可写不能阻挡切断，转本地最小事件记录；
#   · 停止并禁止自动启动 tdsql-copilot-runner，回收其子进程；
#   · 不动 metadata-runner、不动业务数据库、不删除 keyring/表/审计。
# ============================================================================
set -euo pipefail

INCIDENT_ID=""
UNIT="tdsql-copilot-runner"
WEB_UNIT="tdsql-sqlcheck"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --incident-id) INCIDENT_ID="${2:-}"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

# 事件编号白名单：字母/数字/短横线，1—64 字符
if [[ ! "${INCIDENT_ID}" =~ ^[A-Za-z0-9-]{1,64}$ ]]; then
  echo "用法: $0 --incident-id <INCIDENT_ID>（字母/数字/短横线）" >&2
  exit 2
fi

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
INSTALL_DIR="${TDSQL_SQLCHECK_DIR:-/opt/tdsql-sqlcheck}"
# 版本目录外持久停用标记（禁止自动重启后恢复）
PERSIST_FILE="${INSTALL_DIR}/../conf/copilot-disabled.json"
mkdir -p "$(dirname "${PERSIST_FILE}")" 2>/dev/null || true

log() { echo "[emergency-disable] $*"; }
log "事件编号: ${INCIDENT_ID}  时间(UTC): ${TS}"

# ── 1. DB 侧立即停用（尽力；失败不阻挡后续切断）────────────────────────────
DB_OK="false"
PY_BIN="${INSTALL_DIR}/current/venv/bin/python"
if [[ -x "${PY_BIN}" ]]; then
  if "${PY_BIN}" - <<'PYEOF' 2>/dev/null
import json
from backend.services.copilot.repository import RuntimeRepo
from backend.services.database import _get_connection, ensure_db
ensure_db()
conn = _get_connection()
try:
    settings = RuntimeRepo.settings(conn)
    settings["enabled"] = False
    cur = conn.execute(
        "UPDATE copilot_runtime SET settings_json=%s, accepting=0, "
        "updated_at=UTC_TIMESTAMP(6) WHERE id=1",
        (json.dumps(settings, ensure_ascii=False),))
    conn.commit()
    print("db-disabled-ok")
finally:
    conn.close()
PYEOF
  then
    DB_OK="true"
    log "DB 侧已停用（settings.enabled=false, accepting=false）"
  else
    log "DB 侧停用失败（数据库不可用或模块未初始化）——继续本地切断，不阻塞止血"
  fi
else
  log "未找到 venv python（${PY_BIN}），跳过 DB 侧停用"
fi

# ── 2. 版本目录外持久停用标记（禁止自动重启后恢复）──────────────────────────
cat > "${PERSIST_FILE}" <<EOF
{"incident_id": "${INCIDENT_ID}", "disabled_at": "${TS}",
 "db_side": "${DB_OK}", "note": "Copilot 紧急停用标记；恢复须人工查明原因并撤销本文件"}
EOF
chmod 600 "${PERSIST_FILE}" 2>/dev/null || true
log "持久停用标记已写入 ${PERSIST_FILE}"

# ── 3. 停止并禁止自动启动 runner，回收其子进程 ─────────────────────────────
RUNNER_STOPPED="false"
if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1; then
  if systemctl stop "${UNIT}" 2>/dev/null; then
    systemctl disable "${UNIT}" 2>/dev/null || true
    RUNNER_STOPPED="true"
    log "systemd: ${UNIT} 已 stop+disable"
  else
    log "systemd: ${UNIT} stop 失败"
  fi
fi

if [[ "${RUNNER_STOPPED}" != "true" ]]; then
  if command -v pkill >/dev/null 2>&1; then
    pkill -f "backend.workers.copilot_runner" && RUNNER_STOPPED="true" \
      || log "pkill 未匹配到 copilot_runner 进程"
    pkill -f "backend.workers.copilot_text_worker" 2>/dev/null || true
  else
    # 平台无 pkill/systemd：必须显式报错，不能静默放过
    log "错误：本平台既无 systemd 也无 pkill，无法自动停止 ${UNIT}。"
    log "      请人工终止该进程后重跑本脚本；在此期间 DB 侧已停用，"
    log "      助手不会受理新任务，但已发出的模型请求无法撤回。"
    RUNNER_STOPPED="manual-required"
  fi
fi

# ── 4. 最小本地事件记录（无正文/无敏感值）──────────────────────────────────
EVENT_LOG="${INSTALL_DIR}/logs/copilot-emergency.log"
mkdir -p "${INSTALL_DIR}/logs" 2>/dev/null || true
printf '%s incident=%s db_side=%s runner_stopped=%s result=%s\n' \
  "${TS}" "${INCIDENT_ID}" "${DB_OK}" "${RUNNER_STOPPED}" \
  "$(if [[ "${RUNNER_STOPPED}" == "true" ]]; then echo disabled; else echo partial; fi)" \
  >> "${EVENT_LOG}" 2>/dev/null || true

if [[ "${RUNNER_STOPPED}" != "true" ]]; then
  log "停用未完成：runner 仍在运行（${RUNNER_STOPPED}）。"
  log "请人工处理，并复核网关侧是否仍有出站。"
  exit 3
fi
log "Copilot 已停用（事件 ${INCIDENT_ID}）。恢复须：查明原因 → 修复/撤销配置 → "
log "  重跑受影响安全/黄金集门禁 → 人工批准 → 删除 ${PERSIST_FILE} 并重启 ${UNIT}。"
