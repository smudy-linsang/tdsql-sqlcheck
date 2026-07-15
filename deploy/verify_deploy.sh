#!/usr/bin/env bash
# 部署后自动验证（一键冒烟）: health/版本/登录/规则数119/Oracle兼容规则命中/前端资产
# 用法: ./verify_deploy.sh [--port 8000] [--host 127.0.0.1]
set -uo pipefail
PORT=8000; HOST=127.0.0.1; TIMEOUT=10
while [[ $# -gt 0 ]]; do case "$1" in
  --port) PORT="$2"; shift 2;; --host) HOST="$2"; shift 2;; --timeout) TIMEOUT="$2"; shift 2;; *) shift;; esac; done
BASE="http://${HOST}:${PORT}"
PASS=0; FAILC=0
ok()  { echo "  [PASS] $*"; PASS=$((PASS+1)); }
bad() { echo "  [FAIL] $*"; FAILC=$((FAILC+1)); }
J() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)" 2>/dev/null; }

echo "════ 部署验证 v1.0.4.0 @ ${BASE} ════"

# 1. 健康检查与版本
HV=$(curl -s -m ${TIMEOUT} "${BASE}/health")
[[ "$(echo "$HV" | J 'd["status"]')" == "ok" ]] && ok "健康检查 /health" || bad "/health 异常: ${HV:-无响应}"
VER=$(echo "$HV" | J 'd["version"]')
[[ "$VER" == "1.0.4.0" ]] && ok "版本号 ${VER}" || bad "版本号异常: ${VER}(期望1.0.4.0)"

# 2. 前端资产
FRONT=$(curl -s -m ${TIMEOUT} "${BASE}/")
echo "$FRONT" | grep -q "TDSQL" && ok "首页可访问" || bad "首页不可访问"
for f in /static/js/app.js /static/css/app.css /static/vendor/vue.global.prod.js; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -m ${TIMEOUT} "${BASE}${f}")
  [[ "$CODE" == "200" ]] && ok "静态资产 ${f}" || bad "静态资产 ${f} => ${CODE}"
done

# 3. 登录（admin + .env 初始口令；若已改密可 export SQLCHECK_VERIFY_PASSWORD 覆盖）
ENVF="$(dirname "${BASH_SOURCE[0]}")/../.env"
[[ -f "$ENVF" ]] || ENVF="/opt/tdsql-sqlcheck/.env"
PW="${SQLCHECK_VERIFY_PASSWORD:-$(grep -E '^ADMIN_INITIAL_PASSWORD=' "$ENVF" 2>/dev/null | cut -d= -f2-)}"
TOKEN=""
if [[ -n "$PW" ]]; then
  LOGIN=$(curl -s -m ${TIMEOUT} -XPOST "${BASE}/api/v1/auth/login" -H 'Content-Type: application/json' \
          -d "{\"username\":\"admin\",\"password\":\"${PW}\"}")
  TOKEN=$(echo "$LOGIN" | J 'd.get("token","")')
  [[ -n "$TOKEN" ]] && ok "admin 登录成功（认证已启用）" || bad "admin 登录失败: $(echo "$LOGIN" | head -c 120)"
else
  bad "未取到 ADMIN_INITIAL_PASSWORD，无法验证登录"
fi
AUTHH=(-H "Authorization: Bearer ${TOKEN}")

# 4. 规则库 119 条（含 oracle_compat 42 条）
RULES=$(curl -s -m ${TIMEOUT} "${AUTHH[@]}" "${BASE}/api/v1/rules")
TOTAL=$(echo "$RULES" | J 'd["total"]')
OC=$(echo "$RULES" | J 'len([r for r in d["rules"] if r["category"]=="oracle_compat"])')
[[ "$TOTAL" == "119" ]] && ok "规则总数 119" || bad "规则总数=${TOTAL}"
[[ "$OC" == "42" ]] && ok "Oracle迁移兼容规则 42 条" || bad "oracle_compat=${OC}"

# 5. 审核链路（nvl 必须命中 R080）
AUD=$(curl -s -m 15 -XPOST "${AUTHH[@]}" "${BASE}/api/v1/audit/sql" \
      -H 'Content-Type: application/json' -d '{"sql":"SELECT nvl(a,0) FROM t"}')
HIT=$(echo "$AUD" | J 'any(v["rule_id"]=="R080" for v in d["violations"])')
[[ "$HIT" == "True" ]] && ok "审核引擎命中 R080(nvl)" || bad "审核引擎未命中 R080: $(echo "$AUD" | head -c 120)"

# 6. 元数据库读写（dashboard 概览走元数据库）
DASH=$(curl -s -m ${TIMEOUT} "${AUTHH[@]}" "${BASE}/api/v1/dashboard/summary")
echo "$DASH" | J 'd["audit"]["today_count"]' >/dev/null && ok "元数据库读写正常(概览)" || bad "概览接口异常（检查TDSQL元数据库连接）"

# 7. Prometheus 指标
METRICS=$(curl -s -m ${TIMEOUT} "${BASE}/metrics")
echo "$METRICS" | grep -q "tdsql_" && ok "/metrics 指标输出" || bad "/metrics 无输出（确认 METRICS_ENABLED=true）"

echo "════ 验证结果: PASS=${PASS} FAIL=${FAILC} ════"
[[ "$FAILC" -eq 0 ]] && { echo "部署验证全部通过 ✔"; exit 0; } || exit 1
