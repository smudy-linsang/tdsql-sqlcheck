#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# ============================================================
# TDSQL Gateway 日志集中采集与整合脚本 v1.0
#
# 作者: lynx,boogqwang
#
# 功能:
#   从多台 Gateway 服务器集中采集日志分析数据，汇总到本机并生成整合报告。
#
# 工作流程:
#   1. 读取 gateway_servers.conf 配置文件
#   2. 将 analyze_gateway_log.py 分发到各 Gateway 服务器
#   3. 在各服务器上远程执行分析，生成 .json.gz 数据文件
#   4. 将数据文件回收到本地汇总目录
#   5. 调用 merge_gateway_reports.py 生成整合报告
#
# 用法:
#   ./gateway_collect.sh [选项]
#   ./gateway_collect.sh --dates 2026-03-01
#   ./gateway_collect.sh --dates 2026-03-01 2026-03-02 --report report.html
#   ./gateway_collect.sh --config /path/to/servers.conf
#   ./gateway_collect.sh --collect-only
#   ./gateway_collect.sh --merge-only
#
# 依赖:
#   本机: bash, ssh, scp, python3 (运行 merge 脚本)
#   若配置使用密码认证: sshpass
# ============================================================

set -uo pipefail

VERSION="1.1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
TOOLKIT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── 默认配置 ──────────────────────────────────────────────

DEFAULT_CONF="${SCRIPT_DIR}/gateway_servers.conf"
ANALYZER_SCRIPT="${SCRIPT_DIR}/analyze_gateway_log.py"
MERGE_SCRIPT="${SCRIPT_DIR}/merge_gateway_reports.py"

# 远程服务器上的临时工作目录
REMOTE_WORK_DIR="/tmp/tdsql_gateway_collect"

# 本地数据汇总目录（默认）
DEFAULT_OUTPUT_DIR="${SCRIPT_DIR}/collected_data"

# SSH 公共选项
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3"

# 并发控制
MAX_PARALLEL=5

# ── 颜色定义 ──────────────────────────────────────────────

if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    DIM='\033[2m'
    RESET='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' CYAN='' BOLD='' DIM='' RESET=''
fi

# ── 日志函数 ──────────────────────────────────────────────

_ts() { date '+%H:%M:%S'; }
info()  { echo -e "${BLUE}[$(_ts)]${RESET} ${BOLD}$*${RESET}"; }
ok()    { echo -e "${GREEN}[$(_ts)] [OK]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[$(_ts)] [警告]${RESET} $*"; }
fail()  { echo -e "${RED}[$(_ts)] [失败]${RESET} $*"; }
step()  { echo -e "\n${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"; info "$*"; echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"; }

# ── Banner ────────────────────────────────────────────────

print_banner() {
    if [[ -n "${NO_COLOR:-}" ]]; then
        echo "# TDSQL Gateway Log Collect Tool v${VERSION}" >&2
        echo "" >&2
        return
    fi
    echo -e "${CYAN}" >&2
    echo "╔═══════════════════════════════════════════════════════════╗" >&2
    echo "║                                                           ║" >&2
    echo "║   ████████╗██████╗ ███████╗ ██████╗ ██╗                   ║" >&2
    echo "║   ╚══██╔══╝██╔══██╗██╔════╝██╔═══██╗██║                   ║" >&2
    echo "║      ██║   ██║  ██║███████╗██║   ██║██║                   ║" >&2
    echo "║      ██║   ██║  ██║╚════██║██║▄▄ ██║██║                   ║" >&2
    echo "║      ██║   ██████╔╝███████║╚██████╔╝███████╗              ║" >&2
    echo "║      ╚═╝   ╚═════╝ ╚══════╝ ╚══▀▀═╝ ╚══════╝              ║" >&2
    echo "║                                                           ║" >&2
    echo "║        Gateway Log Collect Tool v${VERSION}                        ║" >&2
    echo "╚═══════════════════════════════════════════════════════════╝" >&2
    echo -e "${RESET}" >&2
}

# ── 帮助信息 ──────────────────────────────────────────────

usage() {
    cat >&2 <<EOF
用法 / Usage: ${SCRIPT_NAME} [选项]

TDSQL Gateway 日志集中采集与整合脚本 / TDSQL Gateway Log Collect & Merge Tool

  从多台 Gateway 服务器集中采集日志分析数据，汇总到本机并生成整合报告。
  完整工作流: 读取配置 → 并发 SSH → 远程分析 → 回收数据 → 整合报告

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  参数说明 / Arguments:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  --config FILE         服务器配置文件路径
                        Server list config file path
                        默认: 脚本同目录下 gateway_servers.conf
                        格式: 别名,IP,SSH端口,用户名,认证方式,端口号表达式[,日志路径模板]

  --dates DATE ...      分析日期（传递给 analyze_gateway_log.py --dates）
                        Filter logs by date, passed to analyze_gateway_log.py
                        不指定则分析前一天的日志 / default: yesterday
                        例: --dates 2026-03-01
                            --dates 2026-03-01 2026-03-02

  --output-dir DIR      本地数据汇总目录
                        Local directory for collected data files
                        默认: ./collected_data/{date}

  --report FILE         整合报告输出文件
                        Merged report output file path
                        默认: {output-dir}/report_{date}.html

  --top-n N             Top N 排行数量（默认: 20，传递给 merge 脚本）
                        Number of top entries in merged report (default: 20)

  --sample LINES        每文件采样行数（传递给 analyze_gateway_log.py --sample）
                        Max lines per file, passed to analyze_gateway_log.py

  --parallel N          最大并发采集数（默认: 5）
                        Max concurrent SSH sessions (default: 5)

  --collect-only        只采集数据，不生成整合报告
                        Collect only, skip report generation

  --merge-only          只合并已采集的数据生成报告（跳过采集阶段）
                        Merge only, skip collection phase

  --cleanup             采集完成后清理远程服务器上的临时文件
                        Remove remote temp files after collection

  --dry-run             预览模式，打印将要执行的操作，不实际执行
                        Preview mode, print commands without executing

  -v, --version         显示版本号 / Show version
  -h, --help            显示帮助信息 / Show this help

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  使用示例 / Examples:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ./${SCRIPT_NAME}
  ./${SCRIPT_NAME} --dates 2026-03-01
  ./${SCRIPT_NAME} --dates 2026-03-01 2026-03-02 --report /tmp/report.html
  ./${SCRIPT_NAME} --dates 2026-03-01 --collect-only
  ./${SCRIPT_NAME} --merge-only --output-dir ./collected_data/2026-03-01
  ./${SCRIPT_NAME} --config /etc/tdsql/gateways.conf --dates 2026-03-01
  ./${SCRIPT_NAME} --dates 2026-03-01 --dry-run
  ./${SCRIPT_NAME} --dates 2026-03-01 --parallel 10 --cleanup

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  配置文件格式 / Config File Format (gateway_servers.conf):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  每行一台服务器，逗号分隔，# 开头为注释:
  别名,IP,SSH端口,用户名,认证方式,端口号表达式[,日志路径模板]

  认证方式:
    key:/path/to/id_rsa      SSH 密钥认证（推荐）
    pass:yourpassword         密码认证（需安装 sshpass）

  注意: 端口号表达式中多端口用分号(;)分隔（避免与逗号字段分隔符冲突）
        连续范围仍用冒号(:)或波浪号(~)
        脚本会自动将分号还原为逗号传递给分析脚本

  示例:
    gw-node1,10.0.1.10,22,root,key:/root/.ssh/id_rsa,15001:15020
    gw-node2,10.0.1.11,22,root,key:/root/.ssh/id_rsa,15001:15010;15012
    gw-node3,10.0.1.12,22,root,pass:MyPassword123,15001:15003
    gw-node4,10.0.1.13,22,root,key:/root/.ssh/id_rsa,15001:15010,/data1/tdengine/{port}/gateway/log

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  工作流程 / Workflow:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. 读取 gateway_servers.conf 配置文件
  2. 将 analyze_gateway_log.py 分发到各 Gateway 服务器
  3. 并发 SSH 远程执行分析，生成 .json.gz 数据文件
  4. SCP 回收数据文件到本地 collected_data/{date}/ 目录
  5. 调用 merge_gateway_reports.py 生成整合 HTML 报告

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  输出目录结构 / Output Structure:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  collected_data/
  └── 2026-03-01/
      ├── gw-node1_2026-03-01.json.gz     采集数据（各节点）
      ├── gw-node2_2026-03-01.json.gz
      └── report_2026-03-01.html           整合分析报告

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  依赖 / Dependencies:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  汇总服务器  bash, ssh, scp, python3（运行 merge 脚本）
  密码认证时  sshpass
  Gateway    Python >= 3.6 标准库（无需 pip install）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  定时采集（crontab）:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  # 每天凌晨 2 点采集前一天的日志数据
  0 2 * * * /path/to/${SCRIPT_NAME} --cleanup >> /var/log/tdsql_collect.log 2>&1

EOF
}

# ── 参数解析 ──────────────────────────────────────────────

CONF_FILE=""
ENV_CONF=""
DATES=()
OUTPUT_DIR=""
REPORT_FILE=""
TOP_N=20
SAMPLE=""
COLLECT_ONLY=false
MERGE_ONLY=false
CLEANUP=false
DRY_RUN=false

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --config)
                CONF_FILE="$2"; shift 2 ;;
            --env-conf)
                ENV_CONF="$2"; shift 2 ;;
            --dates)
                shift
                while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do
                    DATES+=("$1"); shift
                done
                ;;
            --output-dir)
                OUTPUT_DIR="$2"; shift 2 ;;
            --report)
                REPORT_FILE="$2"; shift 2 ;;
            --top-n)
                TOP_N="$2"; shift 2 ;;
            --sample)
                SAMPLE="$2"; shift 2 ;;
            --parallel)
                MAX_PARALLEL="$2"; shift 2 ;;
            --collect-only)
                COLLECT_ONLY=true; shift ;;
            --merge-only)
                MERGE_ONLY=true; shift ;;
            --cleanup)
                CLEANUP=true; shift ;;
            --dry-run)
                DRY_RUN=true; shift ;;
            -v|--version)
                echo "TDSQL Gateway Collect Tool v${VERSION}"; exit 0 ;;
            -h|--help)
                usage; exit 0 ;;
            *)
                fail "未知参数: $1"; echo "使用 --help 查看帮助"; exit 1 ;;
        esac
    done

    # 统一配置文件支持: 从 tdsql_env.conf [gateway_servers] 生成临时 CSV
    if [[ -n "${ENV_CONF}" ]]; then
        if [[ -f "${TOOLKIT_ROOT}/tdsql_env_loader.sh" ]]; then
            source "${TOOLKIT_ROOT}/tdsql_env_loader.sh"
        else
            fail "公共函数库不存在: ${TOOLKIT_ROOT}/tdsql_env_loader.sh"
            exit 1
        fi
        if [[ ! -f "${ENV_CONF}" ]]; then
            fail "通用配置文件不存在: ${ENV_CONF}"
            exit 1
        fi
        local _temp_conf
        _temp_conf=$(mktemp /tmp/tdsql_gateway_XXXXXX.conf)
        tdsql_parse_values "gateway_servers" < "${ENV_CONF}" > "${_temp_conf}"
        if [[ ! -s "${_temp_conf}" ]]; then
            fail "通用配置文件 [gateway_servers] 段落为空或不存在: ${ENV_CONF}"
            rm -f "${_temp_conf}"
            exit 1
        fi
        CONF_FILE="${_temp_conf}"
        info "从通用配置文件加载 [gateway_servers]: ${ENV_CONF}"
        trap "rm -f '${_temp_conf}'" EXIT INT TERM HUP
    fi

    # 默认配置文件
    [[ -z "${CONF_FILE}" ]] && CONF_FILE="${DEFAULT_CONF}"

    # 默认日期：昨天
    if [[ ${#DATES[@]} -eq 0 ]]; then
        DATES=("$(date -d 'yesterday' '+%Y-%m-%d' 2>/dev/null || date -v-1d '+%Y-%m-%d')")
    fi

    # 构造日期标签（用于目录/文件命名）
    if [[ ${#DATES[@]} -eq 1 ]]; then
        DATE_TAG="${DATES[0]}"
    else
        local sorted_dates
        sorted_dates=$(printf '%s\n' "${DATES[@]}" | sort)
        local first last
        first=$(echo "${sorted_dates}" | head -1)
        last=$(echo "${sorted_dates}" | tail -1)
        DATE_TAG="${first}~${last}"
    fi

    # 默认输出目录
    [[ -z "${OUTPUT_DIR}" ]] && OUTPUT_DIR="${DEFAULT_OUTPUT_DIR}/${DATE_TAG}"

    # 默认报告文件
    [[ -z "${REPORT_FILE}" ]] && REPORT_FILE="${OUTPUT_DIR}/report_${DATE_TAG}.html"
}

# ── 配置文件解析 ──────────────────────────────────────────

# 服务器配置数组（每个元素是 "别名|IP|SSH端口|用户|认证方式|端口表达式|路径模板"）
SERVERS=()

parse_config() {
    if [[ ! -f "${CONF_FILE}" ]]; then
        fail "配置文件不存在: ${CONF_FILE}"
        if [[ -f "${CONF_FILE}.example" ]]; then
            echo -e "  ${DIM}提示: 复制示例配置文件并修改:${RESET}"
            echo -e "  ${DIM}  cp ${CONF_FILE}.example ${CONF_FILE}${RESET}"
        fi
        exit 1
    fi

    local line_num=0
    while IFS= read -r line || [[ -n "${line}" ]]; do
        line_num=$((line_num + 1))

        # 跳过空行
        [[ -z "${line}" || "${line}" =~ ^[[:space:]]*$ ]] && continue
        # 跳过注释行
        [[ "${line}" =~ ^[[:space:]]*# ]] && continue

        # 解析字段（逗号分隔）
        local field_count
        field_count=$(echo "${line}" | awk -F',' '{print NF}')

        if [[ ${field_count} -lt 6 ]]; then
            fail "配置文件第 ${line_num} 行格式错误（至少需要 6 个逗号分隔字段）: ${line}"
            fail "  格式: 别名,IP,SSH端口,用户名,认证方式,端口号表达式[,日志路径模板]"
            exit 1
        fi

        local alias ip ssh_port user auth ports base_path
        IFS=',' read -r alias ip ssh_port user auth ports base_path <<< "${line}"

        # trim 各字段空格
        alias=$(echo "${alias}" | xargs)
        ip=$(echo "${ip}" | xargs)
        ssh_port=$(echo "${ssh_port}" | xargs)
        user=$(echo "${user}" | xargs)
        auth=$(echo "${auth}" | xargs)
        ports=$(echo "${ports}" | xargs)
        base_path=$(echo "${base_path}" | xargs)

        [[ -z "${alias}" || -z "${ip}" ]] && continue

        # 端口号表达式中的分号还原为逗号（配置文件中用分号避免与字段分隔符冲突）
        ports="${ports//;/,}"

        # 校验认证方式
        if [[ ! "${auth}" =~ ^(key:|pass:) ]]; then
            fail "配置文件第 ${line_num} 行: 认证方式必须以 key: 或 pass: 开头"
            fail "  当前值: ${auth}"
            exit 1
        fi

        # 密码认证检查 sshpass
        if [[ "${auth}" =~ ^pass: ]]; then
            if ! command -v sshpass &>/dev/null; then
                fail "服务器 ${alias} 使用密码认证，但本机未安装 sshpass"
                fail "  安装方式: apt install sshpass / yum install sshpass / brew install sshpass"
                exit 1
            fi
        fi

        SERVERS+=("${alias}|${ip}|${ssh_port}|${user}|${auth}|${ports}|${base_path}")
    done < "${CONF_FILE}"

    if [[ ${#SERVERS[@]} -eq 0 ]]; then
        fail "配置文件中没有有效的服务器配置: ${CONF_FILE}"
        exit 1
    fi
}

# ── SSH/SCP 封装（根据认证方式自动选择） ──────────────────

_ssh_cmd() {
    local ip="$1" ssh_port="$2" user="$3" auth="$4"
    shift 4
    local remote_cmd="$*"

    if [[ "${auth}" =~ ^key: ]]; then
        local key_file="${auth#key:}"
        ssh ${SSH_OPTS} -p "${ssh_port}" -i "${key_file}" "${user}@${ip}" "${remote_cmd}"
    elif [[ "${auth}" =~ ^pass: ]]; then
        local password="${auth#pass:}"
        sshpass -p "${password}" ssh ${SSH_OPTS} -p "${ssh_port}" "${user}@${ip}" "${remote_cmd}"
    fi
}

_scp_to() {
    local ip="$1" ssh_port="$2" user="$3" auth="$4" local_file="$5" remote_path="$6"

    if [[ "${auth}" =~ ^key: ]]; then
        local key_file="${auth#key:}"
        scp ${SSH_OPTS} -P "${ssh_port}" -i "${key_file}" "${local_file}" "${user}@${ip}:${remote_path}"
    elif [[ "${auth}" =~ ^pass: ]]; then
        local password="${auth#pass:}"
        sshpass -p "${password}" scp ${SSH_OPTS} -P "${ssh_port}" "${local_file}" "${user}@${ip}:${remote_path}"
    fi
}

_scp_from() {
    local ip="$1" ssh_port="$2" user="$3" auth="$4" remote_path="$5" local_file="$6"

    if [[ "${auth}" =~ ^key: ]]; then
        local key_file="${auth#key:}"
        scp ${SSH_OPTS} -P "${ssh_port}" -i "${key_file}" "${user}@${ip}:${remote_path}" "${local_file}"
    elif [[ "${auth}" =~ ^pass: ]]; then
        local password="${auth#pass:}"
        sshpass -p "${password}" scp ${SSH_OPTS} -P "${ssh_port}" "${user}@${ip}:${remote_path}" "${local_file}"
    fi
}

# ── 单台服务器采集流程 ────────────────────────────────────

collect_one_server() {
    local server_entry="$1"
    local alias ip ssh_port user auth ports base_path

    IFS='|' read -r alias ip ssh_port user auth ports base_path <<< "${server_entry}"

    local log_prefix="${alias}(${ip})"
    local date_args=""
    for d in "${DATES[@]}"; do
        date_args="${date_args} --dates ${d}"
    done

    local sample_args=""
    [[ -n "${SAMPLE}" ]] && sample_args="--sample ${SAMPLE}"

    local base_path_args=""
    [[ -n "${base_path}" ]] && base_path_args="--base-path ${base_path}"

    local remote_data_file="${REMOTE_WORK_DIR}/${alias}_${DATE_TAG}.json.gz"
    local local_data_file="${OUTPUT_DIR}/${alias}_${DATE_TAG}.json.gz"

    # ── 日志输出文件（用于并发时不混乱） ──
    local log_file="${OUTPUT_DIR}/.log_${alias}.txt"

    {
        echo "[${log_prefix}] 开始采集..."

        # 1. 创建远程工作目录
        if ! _ssh_cmd "${ip}" "${ssh_port}" "${user}" "${auth}" \
            "mkdir -p ${REMOTE_WORK_DIR}" 2>&1; then
            echo "[${log_prefix}] 创建远程目录失败"
            echo "RESULT:FAIL:${alias}"
            return
        fi

        # 2. 分发分析脚本
        if ! _scp_to "${ip}" "${ssh_port}" "${user}" "${auth}" \
            "${ANALYZER_SCRIPT}" "${REMOTE_WORK_DIR}/analyze_gateway_log.py" 2>&1; then
            echo "[${log_prefix}] 上传分析脚本失败"
            echo "RESULT:FAIL:${alias}"
            return
        fi
        echo "[${log_prefix}] 分析脚本已上传"

        # 3. 远程执行分析
        echo "[${log_prefix}] 开始远程分析 (端口: ${ports}, 日期: ${DATES[*]})..."
        local remote_cmd="cd ${REMOTE_WORK_DIR} && python3 analyze_gateway_log.py -p ${ports} ${base_path_args} ${date_args} ${sample_args} -o ${remote_data_file}"

        if ! _ssh_cmd "${ip}" "${ssh_port}" "${user}" "${auth}" "${remote_cmd}" 2>&1; then
            echo "[${log_prefix}] 远程分析执行失败"
            echo "RESULT:FAIL:${alias}"
            return
        fi
        echo "[${log_prefix}] 远程分析完成"

        # 4. 回收数据文件
        if ! _scp_from "${ip}" "${ssh_port}" "${user}" "${auth}" \
            "${remote_data_file}" "${local_data_file}" 2>&1; then
            echo "[${log_prefix}] 数据文件回收失败"
            echo "RESULT:FAIL:${alias}"
            return
        fi

        local fsize
        fsize=$(du -h "${local_data_file}" 2>/dev/null | cut -f1)
        echo "[${log_prefix}] 数据文件已回收: ${local_data_file} (${fsize})"

        # 5. 清理远程临时文件（可选）
        if ${CLEANUP}; then
            _ssh_cmd "${ip}" "${ssh_port}" "${user}" "${auth}" \
                "rm -rf ${REMOTE_WORK_DIR}" 2>&1 || true
            echo "[${log_prefix}] 远程临时文件已清理"
        fi

        echo "RESULT:OK:${alias}"

    } > "${log_file}" 2>&1
}

# ── 采集阶段 ──────────────────────────────────────────────

do_collect() {
    step "阶段 1/3: 准备环境"

    # 检查分析脚本
    if [[ ! -f "${ANALYZER_SCRIPT}" ]]; then
        fail "分析脚本不存在: ${ANALYZER_SCRIPT}"
        exit 1
    fi

    # 创建输出目录
    mkdir -p "${OUTPUT_DIR}"

    info "配置文件: ${CONF_FILE}"
    info "服务器数: ${#SERVERS[@]}"
    info "分析日期: ${DATES[*]}"
    info "数据目录: ${OUTPUT_DIR}"
    info "并发上限: ${MAX_PARALLEL}"
    [[ -n "${SAMPLE}" ]] && info "采样行数: ${SAMPLE}"

    # 打印服务器列表
    echo ""
    printf "  ${DIM}%-15s %-16s %-6s %-20s${RESET}\n" "别名" "IP" "SSH端口" "端口范围"
    printf "  ${DIM}%-15s %-16s %-6s %-20s${RESET}\n" "───────────────" "────────────────" "──────" "────────────────────"
    for entry in "${SERVERS[@]}"; do
        IFS='|' read -r alias ip ssh_port user auth ports base_path <<< "${entry}"
        printf "  %-15s %-16s %-6s %-20s\n" "${alias}" "${ip}" "${ssh_port}" "${ports}"
    done
    echo ""

    if ${DRY_RUN}; then
        info "[Dry-Run] 将对以下服务器执行采集:"
        for entry in "${SERVERS[@]}"; do
            IFS='|' read -r alias ip ssh_port user auth ports base_path <<< "${entry}"
            local base_path_args=""
            [[ -n "${base_path}" ]] && base_path_args="--base-path ${base_path}"
            echo "  ssh ${user}@${ip}:${ssh_port} → python3 analyze_gateway_log.py -p ${ports} ${base_path_args} --dates ${DATES[*]} -o ${alias}_${DATE_TAG}.json.gz"
        done
        return
    fi

    step "阶段 2/3: 采集数据"

    local pids=()
    local total=${#SERVERS[@]}
    local running=0

    for entry in "${SERVERS[@]}"; do
        # 并发控制
        while [[ ${running} -ge ${MAX_PARALLEL} ]]; do
            # 等待任意一个子进程完成
            wait -n 2>/dev/null || true
            running=$((running - 1))
        done

        collect_one_server "${entry}" &
        pids+=($!)
        running=$((running + 1))

        IFS='|' read -r alias _ _ _ _ _ _ <<< "${entry}"
        info "  启动采集: ${alias} (PID: ${pids[-1]})"
    done

    # 等待所有子进程完成
    info ""
    info "等待所有采集任务完成..."
    for pid in "${pids[@]}"; do
        wait "${pid}" 2>/dev/null || true
    done

    # 汇总结果
    echo ""
    local ok_count=0 fail_count=0
    local ok_list=() fail_list=()

    for entry in "${SERVERS[@]}"; do
        IFS='|' read -r alias _ _ _ _ _ _ <<< "${entry}"
        local log_file="${OUTPUT_DIR}/.log_${alias}.txt"

        if [[ -f "${log_file}" ]]; then
            # 输出该服务器的日志
            while IFS= read -r line; do
                if [[ "${line}" =~ ^RESULT:OK: ]]; then
                    ok_count=$((ok_count + 1))
                    ok_list+=("${alias}")
                elif [[ "${line}" =~ ^RESULT:FAIL: ]]; then
                    fail_count=$((fail_count + 1))
                    fail_list+=("${alias}")
                elif [[ "${line}" =~ ^\[.*\] ]]; then
                    # 带前缀的日志行
                    if [[ "${line}" =~ "失败" ]]; then
                        fail "  ${line}"
                    else
                        ok "  ${line}"
                    fi
                fi
            done < "${log_file}"
            rm -f "${log_file}"
        else
            fail_count=$((fail_count + 1))
            fail_list+=("${alias}")
            fail "  [${alias}] 未获取到结果"
        fi
    done

    # 打印汇总
    echo ""
    echo -e "  ${BOLD}采集结果: ${GREEN}${ok_count} 成功${RESET} / ${RED}${fail_count} 失败${RESET} / 共 ${total} 台${RESET}"

    if [[ ${ok_count} -gt 0 ]]; then
        echo -e "  ${GREEN}成功:${RESET} ${ok_list[*]}"
    fi
    if [[ ${fail_count} -gt 0 ]]; then
        echo -e "  ${RED}失败:${RESET} ${fail_list[*]}"
    fi

    # 列出已采集的数据文件
    echo ""
    info "已采集的数据文件:"
    local data_files
    data_files=$(find "${OUTPUT_DIR}" -name "*.json.gz" -type f 2>/dev/null | sort)
    if [[ -n "${data_files}" ]]; then
        while IFS= read -r f; do
            local fsize
            fsize=$(du -h "${f}" | cut -f1)
            echo -e "  ${DIM}${f}${RESET} (${fsize})"
        done <<< "${data_files}"
    else
        warn "  未找到任何数据文件"
    fi
}

# ── 合并阶段 ──────────────────────────────────────────────

do_merge() {
    step "阶段 3/3: 生成整合报告"

    # 检查 merge 脚本
    if [[ ! -f "${MERGE_SCRIPT}" ]]; then
        fail "整合脚本不存在: ${MERGE_SCRIPT}"
        exit 1
    fi

    # 检查 python3
    if ! command -v python3 &>/dev/null; then
        fail "本机未安装 python3，无法生成整合报告"
        exit 1
    fi

    # 查找数据文件
    local data_files=()
    while IFS= read -r f; do
        [[ -n "${f}" ]] && data_files+=("${f}")
    done < <(find "${OUTPUT_DIR}" -name "*.json.gz" -type f 2>/dev/null | sort)

    if [[ ${#data_files[@]} -eq 0 ]]; then
        fail "数据目录中无 .json.gz 文件: ${OUTPUT_DIR}"
        fail "请先执行采集（去掉 --merge-only），或检查 --output-dir 路径"
        exit 1
    fi

    info "数据文件: ${#data_files[@]} 个"
    for f in "${data_files[@]}"; do
        local fsize
        fsize=$(du -h "${f}" | cut -f1)
        echo -e "  ${DIM}${f}${RESET} (${fsize})"
    done

    if ${DRY_RUN}; then
        info "[Dry-Run] 将执行:"
        echo "  python3 ${MERGE_SCRIPT} ${data_files[*]} -n ${TOP_N} -o ${REPORT_FILE}"
        return
    fi

    # 确保报告输出目录存在
    mkdir -p "$(dirname "${REPORT_FILE}")"

    info "生成报告: ${REPORT_FILE}"
    echo ""

    if python3 "${MERGE_SCRIPT}" "${data_files[@]}" -n "${TOP_N}" -o "${REPORT_FILE}"; then
        local rsize
        rsize=$(du -h "${REPORT_FILE}" | cut -f1)
        echo ""
        ok "整合报告已生成: ${REPORT_FILE} (${rsize})"
    else
        fail "报告生成失败"
        exit 1
    fi
}

# ── 主流程 ────────────────────────────────────────────────

main() {
    if [[ $# -eq 0 ]]; then
        print_banner
        usage
        exit 0
    fi

    parse_args "$@"
    print_banner

    local t0
    t0=$(date +%s)

    if ! ${MERGE_ONLY}; then
        parse_config
        do_collect
    fi

    if ! ${COLLECT_ONLY} && ! ${DRY_RUN}; then
        # merge-only 模式也需要确保输出目录存在
        if ${MERGE_ONLY}; then
            [[ -z "${OUTPUT_DIR}" ]] && OUTPUT_DIR="${DEFAULT_OUTPUT_DIR}/${DATE_TAG}"
            [[ -z "${REPORT_FILE}" ]] && REPORT_FILE="${OUTPUT_DIR}/report_${DATE_TAG}.html"
        fi
        do_merge
    fi

    local t1
    t1=$(date +%s)
    local elapsed=$((t1 - t0))

    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "  ${BOLD}总耗时: $((elapsed / 60))m $((elapsed % 60))s${RESET}"
    echo -e "  ${DIM}数据目录: ${OUTPUT_DIR}${RESET}"
    if ! ${COLLECT_ONLY} && ! ${DRY_RUN}; then
        echo -e "  ${DIM}整合报告: ${REPORT_FILE}${RESET}"
    fi
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""
}

main "$@"
