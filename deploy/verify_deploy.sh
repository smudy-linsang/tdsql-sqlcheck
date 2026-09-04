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
#
# v1.6.3.2 / UAT-O-1632-R2-01（P2）整改：
#   6) json_get 改为按 UTF-8 文件路径解析（json_get <selector> <json_file>），
#      不再用 printf|管道把响应正文交给 Python —— Git Bash/MSYS 向 Windows
#      原生 Python 的 stdin 传递大体量中文（真实规则响应约 44KB）会发生字符
#      转码破坏，导致 JSONDecodeError、规则总数/Oracle 分类误判失败；
#   7) 所有 JSON HTTP 响应（health/login/rules/audit/dashboard）先落 mktemp -d
#      私有临时目录中的文件，再由 Python open(...,encoding='utf-8') 读取；
#      首页与 metrics 非 JSON，保留 Bash 字符串匹配；
#   8) 临时目录经 trap 在 EXIT/HUP/INT/TERM 均清理；目录内不打印/保留 token，
#      登录响应读取后即删；绝不回退到可预测的共享 /tmp/_vd_* 文件名。
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

# 0b. 私有临时目录（P2）：所有 JSON 响应落此目录，退出/信号均清理。
#     不回退到可预测的共享 /tmp/_vd_* 文件名（防符号链接/竞争与令牌残留）。
VERIFY_TMP_DIR=$(mktemp -d 2>/dev/null || true)
if [[ -z "$VERIFY_TMP_DIR" || ! -d "$VERIFY_TMP_DIR" ]]; then
  bad "无法创建部署验证临时目录（部署验证中止）"
  summary_and_exit
fi
cleanup() { rm -rf -- "$VERIFY_TMP_DIR"; }
trap cleanup EXIT HUP INT TERM

json_get() {
  # 用法: json_get <selector> <json_file>
  # P2：按 UTF-8 文件路径读取，禁止 stdin/pipe（Git Bash→Windows Python 大体量
  # 中文经 stdin 会转码破坏）。任何解析异常收敛为非零退出码（调用侧以 || true
  # 收敛为 FAIL），不输出 traceback。
  local selector="$1" json_file="$2" py_path
  # Git Bash/MSYS：mktemp -d 产出 POSIX 路径（/tmp/...），Windows 原生 Python 的
  # open() 无法识别，须经 cygpath -w 转成 Windows 路径；Linux 无 cygpath 时原样透传。
  py_path="$json_file"
  if command -v cygpath >/dev/null 2>&1; then
    py_path="$(cygpath -w "$json_file" 2>/dev/null || printf '%s' "$json_file")"
  fi
  "$PY_BIN" -c '
import json, sys
try:
    with open(sys.argv[2], "r", encoding="utf-8") as f:
        d = json.load(f)
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
' "$selector" "$py_path"
}

# 1. 健康探针（落文件 + 检查 HTTP 状态码，禁止无条件 ok）
HEALTH_FILE="${VERIFY_TMP_DIR}/health.json"
HEALTH_HTTP=$(curl -sS -m 3 -o "$HEALTH_FILE" -w "%{http_code}" "${BASE}/health" 2>/dev/null) || HEALTH_HTTP="000"
if [[ "$HEALTH_HTTP" == "200" ]]; then
  ok "健康探针 HTTP 成功"
else
  bad "健康探针不可达（${BASE}/health，HTTP=${HEALTH_HTTP}）"
fi
VER=""
[[ "$HEALTH_HTTP" == "200" ]] && VER=$(json_get version "$HEALTH_FILE" 2>/dev/null || true)
if [[ "$VER" == "$EXPECTED_VER" ]]; then
  ok "版本号 ${VER}"
else
  bad "版本号异常: ${VER:-<解析失败>}（期望 ${EXPECTED_VER}）"
fi

# 2. 前端资产（非 JSON，Bash 字符串匹配；不用 echo|grep -q 管道，规避大 HTML SIGPIPE 假失败）
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
#    响应落私有临时文件、读取 token 后即删；失败分支只输出 HTTP 状态码与固定文案，
#    绝不回显响应体（防令牌泄漏）。
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
    LOGIN_FILE="${VERIFY_TMP_DIR}/login.json"
    LOGIN_HTTP=$(curl -sS -m "${TIMEOUT}" -XPOST "${BASE}/api/v1/auth/login" \
            -H 'Content-Type: application/json' -d "$LOGIN_BODY" \
            -o "$LOGIN_FILE" -w "%{http_code}" 2>/dev/null) || LOGIN_HTTP="000"
    TOKEN=$(json_get token "$LOGIN_FILE" 2>/dev/null || true)
    rm -f -- "$LOGIN_FILE"           # 读取 token 后立即删除登录响应，不在临时目录留存
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

  # 4. 规则库 121 条（含 oracle_compat 42 条）——真实响应约 44KB 中文，落文件解析（P2）
  RULES_FILE="${VERIFY_TMP_DIR}/rules.json"
  RULES_HTTP=$(curl -sS -m "${TIMEOUT}" "${AUTHH[@]}" -o "$RULES_FILE" -w "%{http_code}" "${BASE}/api/v1/rules" 2>/dev/null) || RULES_HTTP="000"
  TOTAL=""; OC=""
  if [[ "$RULES_HTTP" == "200" ]]; then
    TOTAL=$(json_get total "$RULES_FILE" 2>/dev/null || true)
    OC=$(json_get oracle_count "$RULES_FILE" 2>/dev/null || true)
  fi
  [[ "$TOTAL" == "121" ]] && ok "规则总数 121" || bad "规则总数=${TOTAL:-<解析失败>}（HTTP=${RULES_HTTP}）"
  [[ "$OC" == "42" ]] && ok "Oracle迁移兼容规则 42 条" || bad "oracle_compat=${OC:-<解析失败>}"

  # 5. 审核链路（nvl 必须命中 R080）——响应落文件解析
  AUD_FILE="${VERIFY_TMP_DIR}/audit.json"
  AUD_HTTP=$(curl -sS -m 15 -XPOST "${AUTHH[@]}" -o "$AUD_FILE" -w "%{http_code}" \
        -H 'Content-Type: application/json' -d '{"sql":"SELECT nvl(a,0) FROM t"}' \
        "${BASE}/api/v1/audit/sql" 2>/dev/null) || AUD_HTTP="000"
  HIT=""
  [[ "$AUD_HTTP" == "200" ]] && HIT=$(json_get r080_hit "$AUD_FILE" 2>/dev/null || true)
  [[ "$HIT" == "True" ]] && ok "审核引擎命中 R080(nvl)" || bad "审核引擎未命中 R080（HTTP=${AUD_HTTP}，响应体不回显）"

  # 6. 元数据库读写（dashboard 概览走元数据库）——响应落文件解析
  DASH_FILE="${VERIFY_TMP_DIR}/dashboard.json"
  DASH_HTTP=$(curl -sS -m "${TIMEOUT}" "${AUTHH[@]}" -o "$DASH_FILE" -w "%{http_code}" "${BASE}/api/v1/dashboard/summary" 2>/dev/null) || DASH_HTTP="000"
  TODAY=""
  [[ "$DASH_HTTP" == "200" ]] && TODAY=$(json_get today_count "$DASH_FILE" 2>/dev/null || true)
  [[ -n "$TODAY" ]] && ok "元数据库读写正常(概览 today_count=${TODAY})" || bad "概览接口异常（HTTP=${DASH_HTTP}，检查TDSQL元数据库连接）"
fi

# 7. Prometheus 指标（非 JSON，无认证；用字符串匹配替代 grep -q 管道）
METRICS=$(curl -fsS -m "${TIMEOUT}" "${BASE}/metrics" 2>/dev/null) || METRICS=""
[[ "$METRICS" == *tdsql_* ]] && ok "/metrics 指标输出" || bad "/metrics 无输出（确认 METRICS_ENABLED=true）"

summary_and_exit
