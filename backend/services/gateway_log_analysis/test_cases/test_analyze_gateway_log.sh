#!/usr/bin/env bash
# ============================================================
# TDSQL Gateway 日志分析脚本 - 综合功能测试（真实数据）
#
# 使用服务器上的真实日志目录进行全功能测试，
# 覆盖 -d/-p 参数、输出格式、日期过滤、采样等场景。
#
# 用法：
#   chmod +x test_analyze_gateway_log.sh
#   ./test_analyze_gateway_log.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANALYZER="${SCRIPT_DIR}/../analyze_gateway_log.py"
REPORT_DIR="${SCRIPT_DIR}/test_reports"

# 真实日志目录
LOG_DIR_15001="/data/tdsql_run/15001/gateway/log"
LOG_DIR_15002="/data/tdsql_run/15002/gateway/log"
LOG_DIR_15003="/data/tdsql_run/15003/gateway/log"

# 颜色
RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; CYAN='\033[36m'; BOLD='\033[1m'; RESET='\033[0m'

info()  { echo -e "${CYAN}[INFO]${RESET} $*"; }
ok()    { echo -e "${GREEN}[PASS]${RESET} $*"; }
fail()  { echo -e "${RED}[FAIL]${RESET} $*"; FAILURES=$((FAILURES+1)); }
warn()  { echo -e "${YELLOW}[WARN]${RESET} $*"; }

FAILURES=0
TOTAL=0

# 通用测试执行函数
# 参数: $1=描述  $2=输出文件名  其余=命令参数
run_test() {
    local desc="$1"; shift
    local outfile="$1"; shift
    TOTAL=$((TOTAL+1))
    info "测试 #${TOTAL}: ${desc}"
    local t0
    t0=$(date +%s)
    if python3 "${ANALYZER}" "$@" -o "${REPORT_DIR}/${outfile}" 2>&1 | tail -5; then
        local t1
        t1=$(date +%s)
        local sz
        sz=$(du -h "${REPORT_DIR}/${outfile}" | cut -f1)
        ok "  → 通过 (${sz}, $((t1-t0))s)"
    else
        fail "  → 失败"
    fi
    echo ""
}

# ============================================================
# 环境检查
# ============================================================
check_env() {
    echo ""
    echo -e "${BOLD}${CYAN}╔═══════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}${CYAN}║   TDSQL Gateway 日志分析脚本 - 综合功能测试（真实数据）    ║${RESET}"
    echo -e "${BOLD}${CYAN}╚═══════════════════════════════════════════════════════════╝${RESET}"
    echo ""

    if [ ! -f "${ANALYZER}" ]; then
        echo -e "${RED}[错误] 分析脚本不存在: ${ANALYZER}${RESET}"
        exit 1
    fi

    # 检查日志目录
    local available=0
    for d in "${LOG_DIR_15001}" "${LOG_DIR_15002}" "${LOG_DIR_15003}"; do
        if [ -d "$d" ]; then
            local cnt
            cnt=$(ls "$d" | wc -l)
            local sz
            sz=$(du -sh "$d" | cut -f1)
            info "日志目录: $d (${cnt} 文件, ${sz})"
            available=$((available+1))
        else
            warn "日志目录不存在: $d"
        fi
    done

    if [ "$available" -eq 0 ]; then
        echo -e "${RED}[错误] 无可用日志目录${RESET}"
        exit 1
    fi

    # 获取可用日期范围
    info "获取可用日期范围..."
    AVAILABLE_DATES=$(ls "${LOG_DIR_15001}"/ 2>/dev/null | grep -oP "\d{4}-\d{2}-\d{2}" | sort -u)
    LATEST_DATE=$(echo "${AVAILABLE_DATES}" | tail -1)
    SECOND_DATE=$(echo "${AVAILABLE_DATES}" | tail -2 | head -1)
    THIRD_DATE=$(echo "${AVAILABLE_DATES}" | tail -3 | head -1)
    EARLIEST_DATE=$(echo "${AVAILABLE_DATES}" | head -1)
    info "日期范围: ${EARLIEST_DATE} ~ ${LATEST_DATE}"
    info "测试将使用: ${THIRD_DATE}, ${SECOND_DATE}, ${LATEST_DATE}"

    rm -rf "${REPORT_DIR}"
    mkdir -p "${REPORT_DIR}"
    echo ""
}

# ============================================================
# 测试用例
# ============================================================
run_all_tests() {
    echo -e "${BOLD}════════════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD}  开始执行测试用例${RESET}"
    echo -e "${BOLD}════════════════════════════════════════════════════════════${RESET}"
    echo ""

    # ── 1. 基础输出格式测试（单目录 15001，全量） ──────────

    run_test "单目录全量 - terminal 格式" \
        "01_full_terminal.txt" \
        -d "${LOG_DIR_15001}" -f terminal

    run_test "单目录全量 - markdown 格式" \
        "02_full_markdown.md" \
        -d "${LOG_DIR_15001}" -f markdown

    run_test "单目录全量 - HTML 格式" \
        "03_full_html.html" \
        -d "${LOG_DIR_15001}" -f html

    # ── 2. 日期过滤测试 ──────────────────────────────────

    run_test "日期过滤 - 单日 (${LATEST_DATE})" \
        "04_date_single_terminal.txt" \
        -d "${LOG_DIR_15001}" --dates "${LATEST_DATE}" -f terminal

    run_test "日期过滤 - 双日 (${SECOND_DATE} ${LATEST_DATE})" \
        "05_date_two_terminal.txt" \
        -d "${LOG_DIR_15001}" --dates "${SECOND_DATE}" "${LATEST_DATE}" -f terminal

    run_test "日期过滤 - 三日 HTML" \
        "06_date_three_html.html" \
        -d "${LOG_DIR_15001}" --dates "${THIRD_DATE}" "${SECOND_DATE}" "${LATEST_DATE}" -f html

    # ── 3. 跨日日志验证 ──────────────────────────────────
    #    过滤某一天时，应自动扫描前一天文件中的跨日记录

    run_test "跨日日志验证 - ${SECOND_DATE} (应扫描${THIRD_DATE}文件)" \
        "07_crossday_terminal.txt" \
        -d "${LOG_DIR_15001}" --dates "${SECOND_DATE}" -f terminal

    run_test "跨日日志验证 - HTML 报告" \
        "08_crossday_html.html" \
        -d "${LOG_DIR_15001}" --dates "${SECOND_DATE}" -f html

    # ── 4. Top-N 参数测试 ────────────────────────────────

    run_test "Top-N = 5" \
        "09_topn5_terminal.txt" \
        -d "${LOG_DIR_15001}" --dates "${LATEST_DATE}" -n 5 -f terminal

    run_test "Top-N = 3 (markdown)" \
        "10_topn3_markdown.md" \
        -d "${LOG_DIR_15001}" --dates "${LATEST_DATE}" -n 3 -f markdown

    run_test "Top-N = 50 (HTML)" \
        "11_topn50_html.html" \
        -d "${LOG_DIR_15001}" --dates "${LATEST_DATE}" -n 50 -f html

    # ── 5. 采样模式测试 ──────────────────────────────────

    run_test "采样模式 --sample 100" \
        "12_sample100_terminal.txt" \
        -d "${LOG_DIR_15001}" --sample 100 -f terminal

    run_test "采样模式 --sample 500 (HTML)" \
        "13_sample500_html.html" \
        -d "${LOG_DIR_15001}" --sample 500 -f html

    # ── 6. 多目录测试 ────────────────────────────────────

    run_test "双目录 (15001 + 15002)" \
        "14_multi2_terminal.txt" \
        -d "${LOG_DIR_15001}" "${LOG_DIR_15002}" -f terminal

    run_test "双目录 (15001 + 15003) HTML" \
        "15_multi2_html.html" \
        -d "${LOG_DIR_15001}" "${LOG_DIR_15003}" -f html

    run_test "三目录 (15001 + 15002 + 15003)" \
        "16_multi3_terminal.txt" \
        -d "${LOG_DIR_15001}" "${LOG_DIR_15002}" "${LOG_DIR_15003}" -f terminal

    run_test "三目录 HTML" \
        "17_multi3_html.html" \
        -d "${LOG_DIR_15001}" "${LOG_DIR_15002}" "${LOG_DIR_15003}" -f html

    # ── 6b. 端口号参数测试 (-p) ────────────────────────────

    run_test "端口号 - 单个端口 (-p 15001)" \
        "25_port_single.html" \
        -p 15001 --dates "${LATEST_DATE}" -f html

    run_test "端口号 - 连续范围 (-p 15001:15003)" \
        "26_port_range.html" \
        -p 15001:15003 --dates "${LATEST_DATE}" -f html

    run_test "端口号 - 波浪号范围 (-p 15001~15003)" \
        "27_port_tilde.txt" \
        -p 15001~15003 --dates "${LATEST_DATE}" -f terminal

    run_test "端口号 - 混合格式 (-p 15001:15002,15003)" \
        "28_port_mixed.html" \
        -p 15001:15002,15003 --dates "${LATEST_DATE}" -f html

    run_test "端口号 + 日期过滤 + Top5" \
        "29_port_combo.html" \
        -p 15001:15003 --dates "${SECOND_DATE}" "${LATEST_DATE}" -n 5 -f html

    # ── 6c. -p 与 -d 组合使用 ─────────────────────────────

    run_test "端口号 + 目录混用 (-p 15001 -d 15003)" \
        "30_port_and_dir.txt" \
        -p 15001 -d "${LOG_DIR_15003}" --dates "${LATEST_DATE}" -f terminal

    # ── 6d. -p 容错测试 ──────────────────────────────────

    TOTAL=$((TOTAL+1))
    info "测试 #${TOTAL}: 端口号 - 不存在的端口 (应提示警告并报错)"
    local port_err_out
    port_err_out=$(python3 "${ANALYZER}" -p 19999 -f terminal 2>&1 || true)
    if echo "${port_err_out}" | grep -q "目录不存在"; then
        ok "  → 通过 (正确提示目录不存在)"
    else
        fail "  → 未正确处理不存在的端口目录"
    fi
    echo ""

    TOTAL=$((TOTAL+1))
    info "测试 #${TOTAL}: 端口号 - 无效表达式 (应报错)"
    local port_inv_out
    port_inv_out=$(python3 "${ANALYZER}" -p "abc" 2>&1 || true)
    if echo "${port_inv_out}" | grep -qi "错误\|error"; then
        ok "  → 通过 (正确报错)"
    else
        fail "  → 未正确处理无效端口号"
    fi
    echo ""

    # ── 7. 多目录 + 日期过滤组合 ─────────────────────────

    run_test "三目录 + 日期过滤 (${LATEST_DATE})" \
        "18_multi3_date_terminal.txt" \
        -d "${LOG_DIR_15001}" "${LOG_DIR_15002}" "${LOG_DIR_15003}" \
        --dates "${LATEST_DATE}" -f terminal

    run_test "三目录 + 日期过滤 + HTML" \
        "19_multi3_date_html.html" \
        -d "${LOG_DIR_15001}" "${LOG_DIR_15002}" "${LOG_DIR_15003}" \
        --dates "${LATEST_DATE}" -f html

    run_test "三目录 + 日期过滤 + markdown" \
        "20_multi3_date_markdown.md" \
        -d "${LOG_DIR_15001}" "${LOG_DIR_15002}" "${LOG_DIR_15003}" \
        --dates "${LATEST_DATE}" -f markdown

    # ── 8. 组合参数测试 ──────────────────────────────────

    run_test "组合: 日期+Top5+采样1000+HTML" \
        "21_combo_html.html" \
        -d "${LOG_DIR_15001}" --dates "${SECOND_DATE}" "${LATEST_DATE}" \
        -n 5 --sample 1000 -f html

    run_test "组合: 三目录+双日+Top10+markdown" \
        "22_combo_markdown.md" \
        -d "${LOG_DIR_15001}" "${LOG_DIR_15002}" "${LOG_DIR_15003}" \
        --dates "${SECOND_DATE}" "${LATEST_DATE}" -n 10 -f markdown

    # ── 9. stdout 输出（不指定 -o） ──────────────────────

    TOTAL=$((TOTAL+1))
    info "测试 #${TOTAL}: stdout 输出 (不指定 -o)"
    local t0
    t0=$(date +%s)
    if python3 "${ANALYZER}" -d "${LOG_DIR_15001}" --dates "${LATEST_DATE}" -f terminal \
        > "${REPORT_DIR}/23_stdout.txt" 2>/dev/null; then
        local t1
        t1=$(date +%s)
        local sz
        sz=$(du -h "${REPORT_DIR}/23_stdout.txt" | cut -f1)
        ok "  → 通过 (${sz}, $((t1-t0))s)"
    else
        fail "  → 失败"
    fi
    echo ""

    # ── 10. 版本信息 ─────────────────────────────────────

    TOTAL=$((TOTAL+1))
    info "测试 #${TOTAL}: 版本信息 (-v)"
    local ver_output
    ver_output=$(python3 "${ANALYZER}" -v 2>/dev/null || true)
    if echo "${ver_output}" | grep -q '3.2'; then
        ok "  → 通过 (${ver_output})"
    else
        fail "  → 版本号未包含 3.2"
    fi
    echo ""

    # ── 11. 容错测试 ─────────────────────────────────────

    TOTAL=$((TOTAL+1))
    info "测试 #${TOTAL}: 不存在的目录 (不应崩溃)"
    if python3 "${ANALYZER}" -d /tmp/nonexistent_xyz_test -f terminal \
        -o "${REPORT_DIR}/24_nonexistent.txt" 2>/dev/null; then
        ok "  → 通过 (正常退出)"
    else
        fail "  → 崩溃"
    fi
    echo ""

    # ── 12. 验证报告内容 ─────────────────────────────────

    TOTAL=$((TOTAL+1))
    info "测试 #${TOTAL}: terminal 报告内容完整性"
    local content_ok=true
    for keyword in "日志概览" "每日请求量" "耗时分布" "高耗时" "SQL 模式" "错误码" "慢SQL" "系统日志" "核心结论"; do
        if ! grep -q "${keyword}" "${REPORT_DIR}/01_full_terminal.txt" 2>/dev/null; then
            fail "  → terminal 报告缺少: ${keyword}"
            content_ok=false
        fi
    done
    if $content_ok; then ok "  → 所有章节完整"; fi
    echo ""

    TOTAL=$((TOTAL+1))
    info "测试 #${TOTAL}: HTML 报告结构完整性"
    content_ok=true
    for keyword in "<html" "<table" "section" "summary-card" "toc" "flame" "canvas"; do
        if ! grep -q "${keyword}" "${REPORT_DIR}/03_full_html.html" 2>/dev/null; then
            fail "  → HTML 缺少: ${keyword}"
            content_ok=false
        fi
    done
    if $content_ok; then ok "  → HTML 结构完整"; fi
    echo ""

    TOTAL=$((TOTAL+1))
    info "测试 #${TOTAL}: markdown 报告结构完整性"
    content_ok=true
    for keyword in "# " "## " "| " "|--"; do
        if ! grep -q "${keyword}" "${REPORT_DIR}/02_full_markdown.md" 2>/dev/null; then
            fail "  → markdown 缺少: ${keyword}"
            content_ok=false
        fi
    done
    if $content_ok; then ok "  → markdown 结构完整"; fi
    echo ""

    # ── 13. 多目录报告验证 ───────────────────────────────

    TOTAL=$((TOTAL+1))
    info "测试 #${TOTAL}: 多目录报告包含所有目录"
    content_ok=true
    if ! grep -q "15001" "${REPORT_DIR}/16_multi3_terminal.txt" 2>/dev/null; then
        fail "  → 缺少 15001"; content_ok=false
    fi
    if ! grep -q "15002" "${REPORT_DIR}/16_multi3_terminal.txt" 2>/dev/null; then
        fail "  → 缺少 15002"; content_ok=false
    fi
    if ! grep -q "15003" "${REPORT_DIR}/16_multi3_terminal.txt" 2>/dev/null; then
        fail "  → 缺少 15003"; content_ok=false
    fi
    if $content_ok; then ok "  → 三个目录均包含"; fi
    echo ""

    # ── 13b. 端口号报告验证 ──────────────────────────────

    TOTAL=$((TOTAL+1))
    info "测试 #${TOTAL}: 端口号范围报告包含所有端口"
    content_ok=true
    for port in 15001 15002 15003; do
        if ! grep -q "${port}" "${REPORT_DIR}/26_port_range.html" 2>/dev/null; then
            fail "  → 端口号报告缺少 ${port}"; content_ok=false
        fi
    done
    if $content_ok; then ok "  → 端口号报告完整"; fi
    echo ""

    # ── 14. 采样 vs 全量对比 ─────────────────────────────

    TOTAL=$((TOTAL+1))
    info "测试 #${TOTAL}: 采样报告 < 全量报告"
    local full_sz sample_sz
    full_sz=$(wc -c < "${REPORT_DIR}/01_full_terminal.txt" 2>/dev/null || echo 0)
    sample_sz=$(wc -c < "${REPORT_DIR}/12_sample100_terminal.txt" 2>/dev/null || echo 0)
    if [ "$sample_sz" -lt "$full_sz" ] && [ "$sample_sz" -gt 0 ]; then
        ok "  → 通过 (全量:${full_sz} vs 采样:${sample_sz})"
    else
        warn "  → 对比不明显 (全量:${full_sz} vs 采样:${sample_sz})"
    fi
    echo ""
}

# ============================================================
# 汇总报告
# ============================================================
print_summary() {
    echo -e "${BOLD}════════════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD}  测试报告汇总${RESET}"
    echo -e "${BOLD}════════════════════════════════════════════════════════════${RESET}"
    echo ""

    echo "生成的测试报告："
    echo ""
    printf "  %-45s %8s\n" "文件名" "大小"
    printf "  %-45s %8s\n" "---------------------------------------------" "--------"
    for f in "${REPORT_DIR}"/*; do
        [ -f "$f" ] || continue
        local name sz
        name=$(basename "$f")
        sz=$(du -h "$f" | cut -f1)
        printf "  %-45s %8s\n" "${name}" "${sz}"
    done

    echo ""
    local passed=$((TOTAL-FAILURES))
    echo -e "${BOLD}测试结果: ${TOTAL} 个测试, ${GREEN}${passed} 通过${RESET}, ${RED}${FAILURES} 失败${RESET}"
    echo ""

    if [ "${FAILURES}" -eq 0 ]; then
        echo -e "${GREEN}${BOLD}✓ 全部测试通过！${RESET}"
    else
        echo -e "${RED}${BOLD}✗ 有 ${FAILURES} 个测试失败${RESET}"
    fi

    echo ""
    echo "报告目录: ${REPORT_DIR}"
    echo ""
}

# ============================================================
# 主流程
# ============================================================
main() {
    check_env
    run_all_tests
    print_summary
    exit ${FAILURES}
}

main "$@"
