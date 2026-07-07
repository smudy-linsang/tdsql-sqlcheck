#!/usr/bin/env bash
# 一键回滚: 将 current 软链切回上一发布目录并重启服务
# 用法: sudo ./rollback.sh [--dir /opt/tdsql-sqlcheck]
set -euo pipefail
INSTALL_DIR="/opt/tdsql-sqlcheck"
[[ "${1:-}" == "--dir" ]] && INSTALL_DIR="$2"

PREV_FILE="${INSTALL_DIR}/.previous_release"
[[ -f "$PREV_FILE" ]] || { echo "[FAIL] 无上一版本记录(${PREV_FILE})，无法自动回滚"; exit 1; }
PREV="$(cat "$PREV_FILE")"
[[ -d "$PREV" ]] || { echo "[FAIL] 上一版本目录不存在: ${PREV}"; exit 1; }

CUR="$(readlink "${INSTALL_DIR}/current")"
echo "[ROLLBACK] ${CUR} -> ${PREV}"
ln -sfn "${PREV}" "${INSTALL_DIR}/current"
echo "${CUR}" > "${INSTALL_DIR}/.previous_release"   # 支持来回切换
systemctl restart tdsql-sqlcheck
sleep 5
curl -s -m 5 "http://127.0.0.1:$(grep -oE 'port [0-9]+' /etc/systemd/system/tdsql-sqlcheck.service | awk '{print $2}')/health" || true
echo ""
echo "[ROLLBACK] 完成。请执行 verify_deploy.sh 确认服务状态。"
echo "说明: 本系统元数据库表结构为增量兼容设计(ensure_db 只增不删)，v1.0.2 未引入破坏性变更，回滚无需降级数据库。"
