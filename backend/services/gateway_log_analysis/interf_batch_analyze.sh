#!/bin/bash
# ============================================================================
# interf_batch_analyze.sh — interf 日志批量调度脚本
#
# 在 ZK/调度节点上运行，通过 SSH（root 用户）批量分析多台 Proxy 节点的 interf 日志。
# 按 [gateway_proxies] 中的 Proxy IP 分组：同一 IP 的端口在该节点上一次性分析，
# 不会跨节点执行不属于该 IP 的端口分析。
#
# 工作流程:
#   1. 读取 tdsql_env.conf 的 [gateway_proxies] 配置
#   2. 按 Proxy IP 分组（同一 IP 的多个端口归为一组）
#   3. 对每个唯一 IP:
#      a. SCP 上传 interf_deep_analysis.py + tdsql_env.conf 到 /tmp/
#      b. SSH 远程执行分析（只分析属于该 IP 的端口）
#      c. SCP 回收结果文件（tar 打包回传）
#   4. 汇总到本地 output/{日期}/ 目录
#   5. 清空远端敏感配置文件
#
# 用法:
#   bash interf_batch_analyze.sh --dates 2026-04-01
#   bash interf_batch_analyze.sh --dates 2026-04-01 --time-range 14:00-16:00
#   bash interf_batch_analyze.sh --dates 2026-03-30 2026-03-31
#   bash interf_batch_analyze.sh --dates 2026-04-01 --ssh-port 36000
#   bash interf_batch_analyze.sh --dates 2026-04-01 --timeout 1200
#   bash interf_batch_analyze.sh --dates 2026-04-01 --dry-run
#   bash interf_batch_analyze.sh --dates 2026-04-01 --instances 合约管理:15001
#   bash interf_batch_analyze.sh --dates 2026-04-01 --instances 10.0.1.21:15001 10.0.1.22:15001
#   bash interf_batch_analyze.sh --dates 2026-04-01 --instances 合约管理   # 该业务名下所有端口
#   bash interf_batch_analyze.sh --dates 2026-04-01 --instances #1 #3      # 按序号
#
# 作者: boogqwang
# 版本: v1.0
# ============================================================================

set -euo pipefail

VERSION="1.0"
SCRIPT_NAME=$(basename "$0")
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
TOOLKIT_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)

# ============================================================================
# 默认参数
# ============================================================================
DATES=()
TIME_RANGE=""
CONFIG_FILE=""
OUTPUT_DIR=""
SSH_USER="root"
SSH_PORT="22"
SSH_PORT_FROM_CLI=0
SSH_TIMEOUT="10"
REMOTE_TIMEOUT="600"
REMOTE_WORKDIR="/tmp"
DRY_RUN=0
CLEANUP=1
KEEP_REMOTE=0
INSTANCE_FILTERS=()

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ============================================================================
# 工具函数
# ============================================================================

log_info()  { printf "${CYAN}[INFO]${NC} %s\n" "$*" >&2; }
log_ok()    { printf "${GREEN}[OK]${NC} %s\n" "$*" >&2; }
log_warn()  { printf "${YELLOW}[WARN]${NC} %s\n" "$*" >&2; }
log_error() { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }
log_step()  { printf "\n${BOLD}════════════════════════════════════════════════════════════${NC}\n" >&2
              printf "${BOLD}  %s${NC}\n" "$*" >&2
              printf "${BOLD}════════════════════════════════════════════════════════════${NC}\n" >&2; }

usage() {
    cat <<EOF
interf 日志批量调度脚本 v${VERSION}

从 ZK/调度节点通过 SSH 批量分析多台 Proxy 节点的 interf 日志。

用法:
  $SCRIPT_NAME --dates <日期> [选项]

必填参数:
  --dates DATE [DATE...]    分析日期，格式 YYYY-MM-DD，可指定多个

可选参数:
  --time-range HH:MM-HH:MM  只分析指定时间段（如 14:00-16:00）
  --config-file FILE        配置文件路径（默认自动查找 tdsql_env.conf）
  -o, --output-dir DIR      本地输出目录（默认: output/{日期}）
  --ssh-user USER           SSH 用户名（默认: root）
  --ssh-port PORT           SSH 端口号（默认: 22）
  --ssh-timeout SEC         SSH 连接超时（默认: 10 秒）
  --timeout SEC             远程分析命令超时（默认: 600 秒）
  --instances SPEC [SPEC..] 只分析指定实例（默认全部）。SPEC 可以是:
                              N 或 #N            按 proxies_N 序号（如 #1 #3）
                              IP:PORT            指定 Proxy IP + 端口
                              业务名称:PORT       指定业务名 + 端口
                              业务名称            该业务下的所有端口
                              IP                 该 IP 下的所有端口
                            多个 SPEC 之间空格分隔，取并集
  --keep-remote             分析完成后保留远端文件（默认清理配置文件）
  --dry-run                 预览模式，只打印将执行的操作，不实际执行
  -v, --version             显示版本号
  -h, --help                显示帮助

示例:
  # 分析今天的日志
  $SCRIPT_NAME --dates \$(date +%Y-%m-%d)

  # 分析指定时间段
  $SCRIPT_NAME --dates 2026-04-01 --time-range 14:00-16:00

  # 分析多天
  $SCRIPT_NAME --dates 2026-03-30 2026-03-31

  # 只分析某个实例（业务名+端口）
  $SCRIPT_NAME --dates 2026-04-01 --instances 合约管理:15001

  # 只分析某个 Proxy 节点下的某个端口
  $SCRIPT_NAME --dates 2026-04-01 --instances 10.0.1.21:15001

  # 只分析某个业务下的所有端口
  $SCRIPT_NAME --dates 2026-04-01 --instances 合约管理

  # 按 proxies_N 序号指定多个实例
  $SCRIPT_NAME --dates 2026-04-01 --instances #1 #3

  # 预览模式
  $SCRIPT_NAME --dates 2026-04-01 --dry-run
EOF
    exit 0
}

# ============================================================================
# 参数解析
# ============================================================================

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --dates)
                shift
                while [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; do
                    DATES+=("$1")
                    shift
                done
                ;;
            --time-range)
                TIME_RANGE="$2"; shift 2 ;;
            --config-file)
                CONFIG_FILE="$2"; shift 2 ;;
            -o|--output-dir)
                OUTPUT_DIR="$2"; shift 2 ;;
            --ssh-user)
                SSH_USER="$2"; shift 2 ;;
            --ssh-port)
                SSH_PORT="$2"; SSH_PORT_FROM_CLI=1; shift 2 ;;
            --ssh-timeout)
                SSH_TIMEOUT="$2"; shift 2 ;;
            --timeout)
                REMOTE_TIMEOUT="$2"; shift 2 ;;
            --instances)
                shift
                while [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; do
                    INSTANCE_FILTERS+=("$1")
                    shift
                done
                ;;
            --keep-remote)
                KEEP_REMOTE=1; shift ;;
            --dry-run)
                DRY_RUN=1; shift ;;
            -v|--version)
                echo "$SCRIPT_NAME v${VERSION}"; exit 0 ;;
            -h|--help)
                usage ;;
            *)
                log_error "未知参数: $1"
                echo "使用 $SCRIPT_NAME --help 查看帮助" >&2
                exit 1 ;;
        esac
    done

    if [ ${#DATES[@]} -eq 0 ]; then
        log_error "必须指定 --dates 参数"
        echo "使用 $SCRIPT_NAME --help 查看帮助" >&2
        exit 1
    fi
}

# ============================================================================
# 配置文件查找和解析
# ============================================================================

find_config() {
    if [ -n "$CONFIG_FILE" ]; then
        if [ ! -f "$CONFIG_FILE" ]; then
            log_error "配置文件不存在: $CONFIG_FILE"
            exit 1
        fi
        return
    fi
    # 自动查找
    for candidate in \
        "${TOOLKIT_DIR}/tdsql_env.conf" \
        "${SCRIPT_DIR}/tdsql_env.conf" \
        "${SCRIPT_DIR}/../tdsql_env.conf" \
        "./tdsql_env.conf"
    do
        if [ -f "$candidate" ]; then
            CONFIG_FILE=$(cd "$(dirname "$candidate")" && pwd)/$(basename "$candidate")
            log_info "自动找到配置文件: $CONFIG_FILE"
            return
        fi
    done
    log_error "未找到 tdsql_env.conf，请通过 --config-file 指定路径"
    exit 1
}

# 解析 [gateway_proxies] 配置
# 返回格式: proxies_N|业务名称|ProxyIP|端口号|数据库用户名|数据库密码
parse_gateway_proxies() {
    local config_file="$1"
    awk '
    /^\[gateway_proxies\]/ { in_sec=1; next }
    /^\[/                  { in_sec=0 }
    in_sec && /^proxies_[0-9]+=/ {
        sub(/^proxies_/, "")
        split($0, kv, "=")
        idx = kv[1]
        val = substr($0, index($0, "=")+1)
        # 分割为 name,ip,port,user,pass（密码可能含逗号，取前4段后剩余全部作为密码）
        n = split(val, parts, ",")
        if (n >= 5) {
            # 重组密码（第5个字段到最后）
            pass = parts[5]
            for (i = 6; i <= n; i++) pass = pass "," parts[i]
            printf "%s|%s|%s|%s|%s\n", parts[1], parts[2], parts[3], parts[4], pass
        } else if (n >= 3) {
            printf "%s|%s|%s||\n", parts[1], parts[2], parts[3]
        }
    }
    ' "$config_file"
}

# 解析 [ssh] 段获取密码（用于 sshpass）
parse_ssh_password() {
    local config_file="$1"
    awk '
    /^\[ssh\]/ { in_sec=1; next }
    /^\[/      { in_sec=0 }
    in_sec && /^password=/ {
        sub(/^password=/, "")
        print
        exit
    }
    ' "$config_file"
}

# 解析 [ssh] 段获取端口号
parse_ssh_port() {
    local config_file="$1"
    awk '
    /^\[ssh\]/ { in_sec=1; next }
    /^\[/      { in_sec=0 }
    in_sec && /^port=/ {
        sub(/^port=/, "")
        gsub(/[^0-9]/, "")
        print
        exit
    }
    ' "$config_file"
}

# ============================================================================
# 实例过滤器：判断 (idx, name, ip, port) 是否匹配 INSTANCE_FILTERS
# 若未指定过滤器，则全部匹配
# 返回: 0 匹配，1 不匹配
# ============================================================================
match_instance() {
    local _idx="$1" _name="$2" _ip="$3" _port="$4"

    # 未配置过滤器 → 全部通过
    if [ ${#INSTANCE_FILTERS[@]} -eq 0 ]; then
        return 0
    fi

    local spec
    for spec in "${INSTANCE_FILTERS[@]}"; do
        # 1) 纯数字或 #N → 按序号
        local num_spec="${spec#\#}"
        if [[ "$num_spec" =~ ^[0-9]+$ ]]; then
            if [ "$num_spec" = "$_idx" ]; then
                return 0
            fi
            continue
        fi

        # 2) 含冒号 → host:port 或 name:port
        if [[ "$spec" == *:* ]]; then
            local s_left="${spec%:*}"
            local s_right="${spec##*:}"
            if [ "$s_right" = "$_port" ] && { [ "$s_left" = "$_ip" ] || [ "$s_left" = "$_name" ]; }; then
                return 0
            fi
            continue
        fi

        # 3) 无冒号 → 匹配业务名或 IP（全部端口）
        if [ "$spec" = "$_name" ] || [ "$spec" = "$_ip" ]; then
            return 0
        fi
    done

    return 1
}

# ============================================================================
# SSH 封装（支持 sshpass 密码认证 + 密钥认证自动降级）
# ============================================================================

_ssh_cmd() {
    local host="$1"
    shift
    local ssh_opts="-o StrictHostKeyChecking=no -o ConnectTimeout=${SSH_TIMEOUT} -o ServerAliveInterval=30 -p ${SSH_PORT}"
    local _stderr_file
    _stderr_file=$(mktemp /tmp/.ssh_err_XXXXXX 2>/dev/null || echo "/tmp/.ssh_err_$$")
    local _rc=0

    if [ -n "${SSH_PASS:-}" ]; then
        sshpass -p "$SSH_PASS" ssh $ssh_opts "${SSH_USER}@${host}" "$@" 2>"$_stderr_file" || _rc=$?
    else
        ssh $ssh_opts "${SSH_USER}@${host}" "$@" 2>"$_stderr_file" || _rc=$?
    fi

    if [ $_rc -ne 0 ] && [ -s "$_stderr_file" ]; then
        log_error "SSH 连接 ${host} 失败 (exit=${_rc}), 原因: $(cat "$_stderr_file")"
    fi
    rm -f "$_stderr_file"
    return $_rc
}

_scp_to() {
    local host="$1" local_file="$2" remote_file="$3"
    local scp_opts="-o StrictHostKeyChecking=no -o ConnectTimeout=${SSH_TIMEOUT} -P ${SSH_PORT}"
    local _stderr_file
    _stderr_file=$(mktemp /tmp/.scp_err_XXXXXX 2>/dev/null || echo "/tmp/.scp_err_$$")
    local _rc=0

    if [ -n "${SSH_PASS:-}" ]; then
        sshpass -p "$SSH_PASS" scp $scp_opts "$local_file" "${SSH_USER}@${host}:${remote_file}" 2>"$_stderr_file" || _rc=$?
    else
        scp $scp_opts "$local_file" "${SSH_USER}@${host}:${remote_file}" 2>"$_stderr_file" || _rc=$?
    fi

    if [ $_rc -ne 0 ] && [ -s "$_stderr_file" ]; then
        log_error "SCP 上传到 ${host}:${remote_file} 失败 (exit=${_rc}), 原因: $(cat "$_stderr_file")"
    fi
    rm -f "$_stderr_file"
    return $_rc
}

_scp_from() {
    local host="$1" remote_file="$2" local_file="$3"
    local scp_opts="-o StrictHostKeyChecking=no -o ConnectTimeout=${SSH_TIMEOUT} -P ${SSH_PORT}"
    local _stderr_file
    _stderr_file=$(mktemp /tmp/.scp_err_XXXXXX 2>/dev/null || echo "/tmp/.scp_err_$$")
    local _rc=0

    if [ -n "${SSH_PASS:-}" ]; then
        sshpass -p "$SSH_PASS" scp $scp_opts "${SSH_USER}@${host}:${remote_file}" "$local_file" 2>"$_stderr_file" || _rc=$?
    else
        scp $scp_opts "${SSH_USER}@${host}:${remote_file}" "$local_file" 2>"$_stderr_file" || _rc=$?
    fi

    if [ $_rc -ne 0 ] && [ -s "$_stderr_file" ]; then
        log_error "SCP 下载 ${host}:${remote_file} 失败 (exit=${_rc}), 原因: $(cat "$_stderr_file")"
    fi
    rm -f "$_stderr_file"
    return $_rc
}

# ============================================================================
# 核心逻辑
# ============================================================================

main() {
    parse_args "$@"
    find_config

    local dates_str="${DATES[*]}"
    local first_date="${DATES[0]}"

    # ── 解析配置 ──
    local raw_proxies
    raw_proxies=$(parse_gateway_proxies "$CONFIG_FILE")
    if [ -z "$raw_proxies" ]; then
        log_error "[gateway_proxies] 中没有有效配置"
        exit 1
    fi

    # 获取 SSH 密码
    SSH_PASS=$(parse_ssh_password "$CONFIG_FILE")

    # 如果命令行未显式指定 SSH 端口，则从配置文件 [ssh] 段读取
    if [ "$SSH_PORT_FROM_CLI" -eq 0 ]; then
        local cfg_port
        cfg_port=$(parse_ssh_port "$CONFIG_FILE")
        if [ -n "$cfg_port" ]; then
            SSH_PORT="$cfg_port"
        fi
    fi

    log_step "interf 日志批量调度 v${VERSION}"
    log_info "日期: ${dates_str}"
    [ -n "$TIME_RANGE" ] && log_info "时间段: $TIME_RANGE"
    log_info "配置文件: $CONFIG_FILE"
    log_info "SSH: ${SSH_USER}@<proxy>:${SSH_PORT}"
    log_info "远程超时: ${REMOTE_TIMEOUT}s"
    if [ ${#INSTANCE_FILTERS[@]} -gt 0 ]; then
        log_info "实例过滤器: ${INSTANCE_FILTERS[*]}"
    fi

    # ── 按 IP 分组 ──
    # unique_ips: 去重后的 IP 列表
    # ip_configs_<ip_hash>: 每个 IP 下的配置列表
    declare -A ip_configs      # ip -> "idx1:name1:port1 idx2:name2:port2 ..."
    declare -a unique_ips=()
    local idx=0
    local matched_count=0
    while IFS='|' read -r name proxy_ip port db_user db_pass; do
        idx=$((idx + 1))
        # 按 --instances 过滤
        if ! match_instance "$idx" "$name" "$proxy_ip" "$port"; then
            continue
        fi
        matched_count=$((matched_count + 1))
        if [ -z "${ip_configs[$proxy_ip]+x}" ]; then
            unique_ips+=("$proxy_ip")
            ip_configs[$proxy_ip]=""
        fi
        ip_configs[$proxy_ip]+="${idx}|${name}|${port}|${db_user}|${db_pass} "
    done <<< "$raw_proxies"

    if [ ${#INSTANCE_FILTERS[@]} -gt 0 ] && [ "$matched_count" -eq 0 ]; then
        log_error "--instances 没有匹配到任何实例: ${INSTANCE_FILTERS[*]}"
        log_info "可用实例列表（proxies_N=业务名称,IP,端口）:"
        idx=0
        while IFS='|' read -r name proxy_ip port db_user db_pass; do
            idx=$((idx + 1))
            printf "  #%s  %s  %s:%s\n" "$idx" "$name" "$proxy_ip" "$port" >&2
        done <<< "$raw_proxies"
        exit 1
    fi

    # ── 打印分组信息 ──
    log_info "共 ${#unique_ips[@]} 台 Proxy 节点:"
    for ip in "${unique_ips[@]}"; do
        local ports_display=""
        for entry in ${ip_configs[$ip]}; do
            IFS='|' read -r _idx _name _port _user _pass <<< "$entry"
            ports_display+="${_name}:${_port} "
        done
        log_info "  ${ip} → ${ports_display}"
    done

    if [ "$DRY_RUN" -eq 1 ]; then
        log_warn "预览模式，以下是将执行的操作:"
        for ip in "${unique_ips[@]}"; do
            echo ""
            echo "  ── ${ip} ──"
            echo "  1. scp interf_deep_analysis.py + tdsql_env.conf → ${ip}:/tmp/"
            for entry in ${ip_configs[$ip]}; do
                IFS='|' read -r _idx _name _port _user _pass <<< "$entry"
                local cmd="python3 /tmp/interf_deep_analysis.py --dates ${dates_str} --config-index ${_idx} --config-file /tmp/tdsql_env.conf -o /tmp/interf_output/"
                [ -n "$TIME_RANGE" ] && cmd+=" --time-range $TIME_RANGE"
                echo "  2. ssh ${SSH_USER}@${ip} \"${cmd}\""
            done
            echo "  3. scp ${ip}:/tmp/interf_results_*.tar.gz → 本地"
            echo "  4. 清空 ${ip}:/tmp/tdsql_env.conf"
        done
        echo ""
        log_warn "退出预览模式"
        exit 0
    fi

    # ── 本地输出目录 ──
    local local_output="${OUTPUT_DIR:-${SCRIPT_DIR}/output/${first_date}}"
    mkdir -p "$local_output"
    log_info "本地输出目录: $local_output"

    # ── 分析脚本路径 ──
    local analysis_script="${SCRIPT_DIR}/interf_deep_analysis.py"
    if [ ! -f "$analysis_script" ]; then
        log_error "找不到分析脚本: $analysis_script"
        exit 1
    fi

    # ── 逐 IP 执行 ──
    local total_ips=${#unique_ips[@]}
    local ip_idx=0
    local total_success=0
    local total_fail=0
    local total_instances=0

    for ip in "${unique_ips[@]}"; do
        ip_idx=$((ip_idx + 1))
        log_step "[${ip_idx}/${total_ips}] Proxy 节点: ${ip}"

        # ── 步骤 1: 上传脚本和配置 ──
        log_info "上传脚本和配置文件到 ${ip}:/tmp/ ..."
        if ! _scp_to "$ip" "$analysis_script" "/tmp/interf_deep_analysis.py"; then
            log_error "上传脚本失败: ${ip}"
            total_fail=$((total_fail + 1))
            continue
        fi
        if ! _scp_to "$ip" "$CONFIG_FILE" "/tmp/tdsql_env.conf"; then
            log_error "上传配置文件失败: ${ip}"
            total_fail=$((total_fail + 1))
            continue
        fi
        log_ok "上传完成"

        # ── 步骤 2: 清空并重建远程输出目录（避免残留旧文件混入回收结果）──
        _ssh_cmd "$ip" "rm -rf /tmp/interf_output && mkdir -p /tmp/interf_output" || true

        # ── 步骤 3: 逐个实例分析 ──
        local ip_success=0
        local ip_fail=0
        for entry in ${ip_configs[$ip]}; do
            IFS='|' read -r _idx _name _port _user _pass <<< "$entry"
            total_instances=$((total_instances + 1))
            log_info "分析 ${_name} (${ip}:${_port}) [config-index=${_idx}] ..."

            # 构造远程命令
            local remote_cmd="timeout ${REMOTE_TIMEOUT} python3 /tmp/interf_deep_analysis.py"
            remote_cmd+=" --dates ${dates_str}"
            remote_cmd+=" --config-index ${_idx}"
            remote_cmd+=" --config-file /tmp/tdsql_env.conf"
            remote_cmd+=" -o /tmp/interf_output/"
            [ -n "$TIME_RANGE" ] && remote_cmd+=" --time-range $TIME_RANGE"

            if _ssh_cmd "$ip" "$remote_cmd" 2>&1; then
                ip_success=$((ip_success + 1))
                log_ok "${_name} (${ip}:${_port}) 分析完成"
            else
                ip_fail=$((ip_fail + 1))
                log_error "${_name} (${ip}:${_port}) 分析失败"
            fi
        done

        # ── 步骤 4: 回收结果 ──
        log_info "回收 ${ip} 的分析结果 ..."
        local remote_tar="/tmp/interf_results_${ip//\./_}.tar.gz"
        local local_tar="/tmp/interf_results_${ip//\./_}.tar.gz"

        # 远程打包
        _ssh_cmd "$ip" "cd /tmp/interf_output && ls *.csv 2>/dev/null | head -1 >/dev/null 2>&1 && tar czf ${remote_tar} *.csv 2>/dev/null || echo 'NO_FILES'"

        # 下载
        if _scp_from "$ip" "$remote_tar" "$local_tar"; then
            # 解压到本地
            if tar xzf "$local_tar" -C "$local_output/" 2>/dev/null; then
                local file_count
                file_count=$(tar tzf "$local_tar" 2>/dev/null | wc -l | tr -d ' ')
                log_ok "回收完成: ${file_count} 个文件"
            else
                log_warn "解压失败，可能远端没有输出文件"
            fi
            rm -f "$local_tar"
        else
            log_warn "回收失败，${ip} 可能没有输出文件"
        fi

        # ── 步骤 5: 清理远端敏感文件 ──
        if [ "$KEEP_REMOTE" -eq 0 ]; then
            log_info "清空远端配置文件（含敏感信息）..."
            _ssh_cmd "$ip" "echo > /tmp/tdsql_env.conf; rm -f ${remote_tar}" || true
            # 保留脚本和输出结果，只清空配置
        fi

        total_success=$((total_success + ip_success))
        total_fail=$((total_fail + ip_fail))

        log_ok "${ip}: ${ip_success} 成功, ${ip_fail} 失败"
    done

    # ── 最终汇总 ──
    log_step "批量分析完成"
    log_info "Proxy 节点数: ${total_ips}"
    log_info "实例总数: ${total_instances}"
    log_ok "成功: ${total_success}, 失败: ${total_fail}"
    log_info "输出目录: ${local_output}"
    echo ""

    # 列出输出文件
    if [ -d "$local_output" ]; then
        local count
        count=$(find "$local_output" -maxdepth 1 -type f \( -name "*.html" -o -name "*.csv" \) 2>/dev/null | wc -l | tr -d ' ')
        if [ "$count" -gt 0 ]; then
            log_info "输出文件 (${count} 个):"
            find "$local_output" -maxdepth 1 -type f \( -name "*.html" -o -name "*.csv" \) -exec basename {} \; | sort | while read -r fname; do
                echo "  ${fname}"
            done
        fi
    fi
}

main "$@"