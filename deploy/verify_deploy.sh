#!/usr/bin/env bash
# 部署后自动验证（一键冒烟）: health/版本/首页/静态资产/登录/规则数121/Oracle42/R080/概览/metrics
# 用法: ./verify_deploy.sh [--port 8000] [--host 127.0.0.1] [--timeout 10]
#
# v1.6.3.2 / UAT-O-1632-REL-01（P1）整改：
#   1) JSON 解析改为白名单式 json_get（应用 venv Python 或系统 python3），
#      不再依赖从未定义的 J 函数（历史上导致登录被误判失败 + 空令牌连锁 401）；
#   2) 健康探针检查 curl 退出码，禁止无条件 ok；
#   3) 首页判断改 Bash 字符串匹配，规避 pipefail + grep -q 对大 HTML 的
#      SIGPIPE 假失败；
#   4) 登录请求体由 Python json.dumps 生成（正确处理口令中的引号/反斜杠）；
#      任何失败只输出 HTTP 状态与固定文案，绝不回显登录响应体 /
#      Authorization / token 前缀（防管理员令牌泄漏进终端与部署日志）；
#   5) token 为空时认证后检查明确记 SKIP「登录前置失败而跳过」，
#      且 SKIP>0 时退出码为 1（不能视为部署验证通过）。
set -uo pipefail
PORT=8000; HOST=127.0.0.1; TIMEOUT=10
while [[ $# -gt 0 ]]; do case "$1" in
  --port) PORT="$2"; shift 2;; --host) HOST="$2"; shift 2;; --timeout) TIMEOUT="$2"; shift 2;; *) shift;; esac; done
BASE="http://${HOST}:${PORT}"
PASS=0; FAILC=0; SKIP=0
ok()   { echo "  [PASS] $*"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $*"; FAILC=$((FAILC+1)); }
skip() { echo "  [SKIP] $*"; SKIP=$((SKIP+1)); }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED_VER="$(tr -d '\r\n' < "${SCRIPT_DIR}/../VERSION" 2>/dev/null || echo "unknown")"

summary_and_exit() {
  echo "════ 验证结果: PASS=${PASS} FAIL=${FAILC} SKIP=${SKIP} ════"
  if [[ "$FAILC" -eq 0 && "$SKIP" -eq 0 ]]; then
    echo "部署验证全部通过"
    exit 0
  fi
  [[ "$SKIP" -gt 0 ]] && echo "存在跳过项（登录前置失败），不能视为部署验证通过"
  exit 1
}

echo "════ 部署验证 v${EXPECTED_VER} @ ${BASE} ════"
echo ""

# 0. JSON 解析器：优先 SQLCHECK_VERIFY_PYTHON 显式指定（契约测试/特殊环境），
#    其次应用 venv Python，最后系统 python3（生产麒麟环境为 python3.11）；
#    找不到即 FAIL 中止。白名单式 selector（仅本脚本用到的六个），不使用 eval。
PY_BIN="${SQLCHECK_VERIFY_PYTHON:-}"
if [[ -z "$PY_BIN" ]]; then
  PY_BIN="${SCRIPT_DIR}/../venv/bin/python"
  if [[ ! -x "$PY_BIN" ]]; then
    PY_BIN=""
    for c in python3.11 python3.10 python3.9 python3 python; do
      if command -v "$c" >/dev/null 2>&1; then PY_BIN="$(command -v "$c")"; break; fi
    done
  fi
fi
if [[ -z "$PY_BIN" ]]; then
  bad "找不到可用 Python，无法解析 JSON（部署验证中止）"
  summary_and_exit
fi

json_get() {
  # 用法: printf '%s' "$BODY" | json_get <selector>
  # 任何解析异常收敛为非零退出码（调用侧以 || true 收敛为 FAIL），不输出 traceback。
  local selector="$1"
  "$PY_BIN" -c '
import json, sys
try:
    d = json.load(sys.stdin)
    s = sys.argv[1]
    if s == "version": value = d.get("version", "")
    elif s == "token": value = d.get("token", "")
    elif s == "total": value = d.get("total", "")
    elif s == "oracle_count": value = sum(r.get("category") == "oracle_compat" for r in d.get("rules", []))
    elif s == "r080_hit": value = any(v.get("rule_id") == "R080" for v in d.get("violations", []))
    elif s == "today_count": value = d["audit"]["today_count"]
    else: raise SystemExit(2)
    print(value)
except SystemExit:
    raise
except Exception:
    raise SystemExit(1)
' "$selector"
}

# 1. 健康探针（必须检查 curl 退出码，禁止无条件 ok）
HEALTH=""
if HEALTH=$(curl -fsS -m 3 "${BASE}/health" 2>/dev/null); then
  ok "健康探针 HTTP 成功"
else
  bad "健康探针不可达（${BASE}/health）"
fi
VER=$(printf '%s' "$HEALTH" | json_get version 2>/dev/null || true)
if [[ "$VER" == "$EXPECTED_VER" ]]; then
  ok "版本号 ${VER}"
else
  bad "版本号异常: ${VER:-<解析失败>}（期望 ${EXPECTED_VER}）"
fi

# 2. 前端资产（Bash 字符串匹配；不用 echo|grep -q 管道，规避大 HTML SIGPIPE 假失败）
FRONT=""
if FRONT=$(curl -fsS -m "${TIMEOUT}" "${BASE}/" 2>/dev/null); then
  if [[ "$FRONT" == *TDSQL* ]]; then ok "首页可访问"; else bad "首页内容异常（未包含 TDSQL 标识）"; fi
else
  bad "首页不可访问"
fi
for f in /static/js/app.js /static/css/app.css /static/vendor/vue.global.prod.js; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -m "${TIMEOUT}" "${BASE}${f}")
  [[ "$CODE" == "200" ]] && ok "静态资产 ${f}" || bad "静态资产 ${f} => ${CODE}"
done

# 3. 登录（admin + .env 初始口令；若已改密可 export SQLCHECK_VERIFY_PASSWORD 覆盖）
#    失败分支只输出 HTTP 状态码与固定文案，绝不回显响应体（防令牌泄漏）。
ENVF="${SCRIPT_DIR}/../.env"
[[ -f "$ENVF" ]] || ENVF="/opt/tdsql-sqlcheck/.env"
PW="${SQLCHECK_VERIFY_PASSWORD:-$(grep -E '^ADMIN_INITIAL_PASSWORD=' "$ENVF" 2>/dev/null | cut -d= -f2-)}"
TOKEN=""
if [[ -z "$PW" ]]; then
  bad "未取到 ADMIN_INITIAL_PASSWORD（或 SQLCHECK_VERIFY_PASSWORD），无法验证登录"
else
  LOGIN_BODY=$("$PY_BIN" -c 'import json,sys; print(json.dumps({"username":"admin","password":sys.argv[1]}))' "$PW" 2>/dev/null || true)
  if [[ -z "$LOGIN_BODY" ]]; then
    bad "登录请求体生成失败（Python json.dumps 异常）"
  else
    LOGIN_TMP=$(mktemp 2>/dev/null || echo "/tmp/_vd_login.$$")
    LOGIN_HTTP=$(curl -s -m "${TIMEOUT}" -XPOST "${BASE}/api/v1/auth/login" \
            -H 'Content-Type: application/json' -d "$LOGIN_BODY" \
            -o "$LOGIN_TMP" -w "%{http_code}" 2>/dev/null) || LOGIN_HTTP="000"
    TOKEN=$(json_get token < "$LOGIN_TMP" 2>/dev/null || true)
    rm -f "$LOGIN_TMP"
    if [[ -n "$TOKEN" ]]; then
      ok "admin 登录成功（认证已启用）"
    else
      bad "admin 登录失败（HTTP=${LOGIN_HTTP}；响应体不回显，防令牌泄漏）"
    fi
  fi
fi

# 4-6. 认证后检查：token 为空时明确记 SKIP（登录前置失败），不伪装成业务接口故障
if [[ -z "$TOKEN" ]]; then
  skip "规则总数/Oracle 兼容检查（登录前置失败而跳过）"
  skip "审核引擎 R080 检查（登录前置失败而跳过）"
  skip "元数据库概览检查（登录前置失败而跳过）"
else
  AUTHH=(-H "Authorization: Bearer ${TOKEN}")

  # 4. 规则库 121 条（含 oracle_compat 42 条）
  RULES=$(curl -fsS -m "${TIMEOUT}" "${AUTHH[@]}" "${BASE}/api/v1/rules" 2>/dev/null) || RULES=""
  TOTAL=$(printf '%s' "$RULES" | json_get total 2>/dev/null || true)
  OC=$(printf '%s' "$RULES" | json_get oracle_count 2>/dev/null || true)
  [[ "$TOTAL" == "121" ]] && ok "规则总数 121" || bad "规则总数=${TOTAL:-<解析失败>}"
  [[ "$OC" == "42" ]] && ok "Oracle迁移兼容规则 42 条" || bad "oracle_compat=${OC:-<解析失败>}"

  # 5. 审核链路（nvl 必须命中 R080）
  AUD=$(curl -fsS -m 15 -XPOST "${AUTHH[@]}" "${BASE}/api/v1/audit/sql" \
        -H 'Content-Type: application/json' -d '{"sql":"SELECT nvl(a,0) FROM t"}' 2>/dev/null) || AUD=""
  HIT=$(printf '%s' "$AUD" | json_get r080_hit 2>/dev/null || true)
  [[ "$HIT" == "True" ]] && ok "审核引擎命中 R080(nvl)" || bad "审核引擎未命中 R080（HTTP 或解析失败，响应体不回显）"

  # 6. 元数据库读写（dashboard 概览走元数据库）
  DASH=$(curl -fsS -m "${TIMEOUT}" "${AUTHH[@]}" "${BASE}/api/v1/dashboard/summary" 2>/dev/null) || DASH=""
  TODAY=$(printf '%s' "$DASH" | json_get today_count 2>/dev/null || true)
  [[ -n "$TODAY" ]] && ok "元数据库读写正常(概览 today_count=${TODAY})" || bad "概览接口异常（检查TDSQL元数据库连接）"
fi

# 7. Prometheus 指标（无认证；同样用字符串匹配替代 grep -q 管道）
METRICS=$(curl -fsS -m "${TIMEOUT}" "${BASE}/metrics" 2>/dev/null) || METRICS=""
[[ "$METRICS" == *tdsql_* ]] && ok "/metrics 指标输出" || bad "/metrics 无输出（确认 METRICS_ENABLED=true）"

summary_and_exit
