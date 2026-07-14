#!/bin/bash
################################################################################
# setup_ssh_keys.sh — TDSQL 批量 SSH 免密配置工具
#
# 功能:
#   从 tdsql_hosts 文件中读取所有节点 IP，使用指定的 SSH 密码，
#   自动完成 SSH 密钥生成 + 公钥分发 + 免密验证，一键配置所有节点免密登录。
#
# 用法:
#   ./setup_ssh_keys.sh --password <SSH密码> [选项]
#
# 选项:
#   -c <hosts文件>     指定 tdsql_hosts 文件 (默认: tdsql_hosts)
#   --password <密码>  SSH 密码 (非 --verify-only 模式下必填)
#   -u <用户名>        SSH 用户名 (默认: root)
#   -p <端口>          SSH 端口 (默认: 36000)
#   -k <密钥路径>      SSH 密钥路径 (默认: ~/.ssh/id_rsa)
#   -b <密钥位数>      RSA 密钥位数 (默认: 2048)
#   --force            强制重新生成密钥对（即使已存在）
#   --verify-only      仅验证免密状态，不做任何修改
#   --dry-run          模拟运行，仅显示将要执行的操作
#   -h                 显示帮助
#
# 版本: 1.0
################################################################################

set -euo pipefail

# ============================================================================
# 全局变量
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLKIT_ROOT="$(cd "${SCRIPT_DIR}/.." 2>/dev/null && pwd)"

# 默认参数
HOSTS_FILE="${SCRIPT_DIR}/tdsql_hosts"
SSH_PORT="36000"
SSH_USER=""
SSH_PASS=""
KEY_PATH="${HOME}/.ssh/id_rsa"
KEY_BITS="2048"
FORCE_KEYGEN=0
VERIFY_ONLY=0
DRY_RUN=0

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# 统计计数
TOTAL_HOSTS=0
SUCCESS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
ALREADY_OK_COUNT=0

# 结果记录数组
declare -a RESULT_IPS=()
declare -a RESULT_ALIASES=()
declare -a RESULT_STATUSES=()
declare -a RESULT_MESSAGES=()

# ============================================================================
# 工具函数
# ============================================================================
log_info()    { echo -e "  ${GREEN}✔${NC}  $*"; }
log_warn()    { echo -e "  ${YELLOW}⚠${NC}  $*"; }
log_error()   { echo -e "  ${RED}✘${NC}  $*"; }
log_step()    { echo -e "\n${CYAN}▶${NC} ${BOLD}$*${NC}"; }
log_substep() { echo -e "  ${DIM}→${NC} $*"; }

print_banner() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}       ${BOLD}TDSQL 批量 SSH 免密配置工具${NC}  ${DIM}v1.0${NC}                       ${BLUE}║${NC}"
    echo -e "${BLUE}╠══════════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${BLUE}║${NC}  ${DIM}从 tdsql_hosts 读取节点 → 自动分发公钥 → 验证免密登录${NC}      ${BLUE}║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

usage() {
    cat <<EOF
用法: $0 --password <SSH密码> [选项]

选项:
  -c <hosts文件>     指定 tdsql_hosts 文件 (默认: tdsql_hosts)
  --password <密码>  SSH 密码 (非 --verify-only 模式下必填)
  -u <用户名>        SSH 用户名 (默认: root)
  -p <端口>          SSH 端口 (默认: 36000)
  -k <密钥路径>      SSH 密钥路径 (默认: ~/.ssh/id_rsa)
  -b <密钥位数>      RSA 密钥位数 (默认: 2048)
  --force            强制重新生成密钥对（即使已存在）
  --verify-only      仅验证免密状态，不做任何修改
  --dry-run          模拟运行，仅显示将要执行的操作
  -h                 显示帮助

示例:
  $0 --password 'MySSHPass'                # 使用默认配置，一键配置免密
  $0 --verify-only                         # 仅检查当前免密状态
  $0 --password 'MySSHPass' --force        # 强制重新生成密钥并分发
  $0 --password 'MySSHPass' -p 22 -u root  # 指定 SSH 端口和用户
  $0 --dry-run --password 'MySSHPass'      # 模拟运行，查看将要执行的操作
EOF
    exit 0
}

# ============================================================================
# 参数解析
# ============================================================================
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -c) HOSTS_FILE="$2"; shift 2 ;;
            -u) SSH_USER="$2"; shift 2 ;;
            -p) SSH_PORT="$2"; shift 2 ;;
            --password) SSH_PASS="$2"; shift 2 ;;
            -k) KEY_PATH="$2"; shift 2 ;;
            -b) KEY_BITS="$2"; shift 2 ;;
            --force) FORCE_KEYGEN=1; shift ;;
            --verify-only) VERIFY_ONLY=1; shift ;;
            --dry-run) DRY_RUN=1; shift ;;
            -h|--help) usage ;;
            *) log_error "未知参数: $1"; usage ;;
        esac
    done
}

# ============================================================================
# 配置初始化
# ============================================================================

# 初始化 SSH 配置（使用命令行传入的参数）
init_ssh_config() {
    # 默认用户
    if [ -z "$SSH_USER" ]; then
        SSH_USER="root"
    fi
    return 0
}

# 从 tdsql_hosts 解析 [tdsql_allmacforcheck] 段中的唯一 IP
parse_unique_ips() {
    local inv_file="$1"
    local seen_ips=""
    local current_section=""

    if [ ! -f "$inv_file" ]; then
        log_error "tdsql_hosts 文件不存在: $inv_file"
        return 1
    fi

    while IFS= read -r line || [[ -n "$line" ]]; do
        line=$(echo "$line" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [[ -z "$line" ]] && continue
        [[ "$line" =~ ^# ]] && continue

        # 检测 section 头
        if [[ "$line" =~ ^\[(.+)\]$ ]]; then
            current_section="${BASH_REMATCH[1]}"
            continue
        fi

        # 只从 [tdsql_allmacforcheck] 段提取主机
        [[ "$current_section" != "tdsql_allmacforcheck" ]] && continue

        if [[ "$line" =~ ansible_ssh_host=([0-9.]+) ]]; then
            local ip="${BASH_REMATCH[1]}"
            local alias_name=$(echo "$line" | awk '{print $1}')

            # IP 去重
            if echo "$seen_ips" | grep -qw "$ip"; then
                continue
            fi
            seen_ips="${seen_ips} ${ip}"

            echo "${alias_name},${ip}"
        fi
    done < "$inv_file"
}

# ============================================================================
# 前置检查
# ============================================================================
check_prerequisites() {
    log_step "前置环境检查"

    # 检查 sshpass
    if [ "$VERIFY_ONLY" -eq 0 ]; then
        if command -v sshpass &>/dev/null; then
            log_info "sshpass 已安装: $(which sshpass)"
        else
            log_error "sshpass 未安装，免密配置需要 sshpass 来分发公钥"
            echo -e "    ${DIM}安装命令: yum install -y sshpass  或  apt install -y sshpass${NC}"
            return 1
        fi
    fi

    # 检查 tdsql_hosts 文件
    if [ -f "$HOSTS_FILE" ]; then
        log_info "tdsql_hosts 文件: ${HOSTS_FILE}"
    else
        log_error "tdsql_hosts 文件不存在: ${HOSTS_FILE}"
        return 1
    fi

    # 检查 SSH 配置
    if [ -z "$SSH_PASS" ] && [ "$VERIFY_ONLY" -eq 0 ]; then
        log_error "未指定 SSH 密码，请使用 --password 参数传入"
        return 1
    fi

    # 默认用户
    if [ -z "$SSH_USER" ]; then
        SSH_USER="root"
    fi

    log_info "SSH 用户: ${SSH_USER}"
    log_info "SSH 端口: ${SSH_PORT}"
    log_info "密钥路径: ${KEY_PATH}"

    return 0
}

# ============================================================================
# SSH 密钥管理
# ============================================================================
ensure_ssh_key() {
    log_step "SSH 密钥检查"

    local pub_key="${KEY_PATH}.pub"

    # 确保 .ssh 目录存在
    local ssh_dir=$(dirname "$KEY_PATH")
    if [ ! -d "$ssh_dir" ]; then
        mkdir -p "$ssh_dir"
        chmod 700 "$ssh_dir"
        log_info "创建 SSH 目录: ${ssh_dir}"
    fi

    # 检查密钥是否已存在
    if [ -f "$KEY_PATH" ] && [ -f "$pub_key" ] && [ "$FORCE_KEYGEN" -eq 0 ]; then
        local key_fingerprint
        key_fingerprint=$(ssh-keygen -lf "$pub_key" 2>/dev/null | awk '{print $2}')
        log_info "SSH 密钥已存在 (${key_fingerprint})"
        log_substep "如需重新生成，请使用 --force 参数"
        return 0
    fi

    # 生成新密钥
    if [ "$DRY_RUN" -eq 1 ]; then
        log_warn "[模拟] 将生成 ${KEY_BITS} 位 RSA 密钥: ${KEY_PATH}"
        return 0
    fi

    if [ -f "$KEY_PATH" ]; then
        # 备份旧密钥
        local backup="${KEY_PATH}.bak.$(date +%Y%m%d%H%M%S)"
        cp "$KEY_PATH" "$backup"
        cp "${pub_key}" "${backup}.pub" 2>/dev/null || true
        log_warn "已备份旧密钥: ${backup}"
    fi

    log_substep "正在生成 ${KEY_BITS} 位 RSA 密钥..."
    ssh-keygen -t rsa -b "$KEY_BITS" -f "$KEY_PATH" -N "" -q
    chmod 600 "$KEY_PATH"
    chmod 644 "${pub_key}"

    local new_fingerprint
    new_fingerprint=$(ssh-keygen -lf "$pub_key" 2>/dev/null | awk '{print $2}')
    log_info "密钥生成成功 (${new_fingerprint})"

    return 0
}

# ============================================================================
# 公钥分发 & 验证
# ============================================================================

# 检查某台主机是否已经免密
check_passwordless() {
    local ip="$1"
    local port="$2"
    local user="$3"

    timeout 10 ssh -o StrictHostKeyChecking=no \
        -o ConnectTimeout=5 \
        -o BatchMode=yes \
        -o PasswordAuthentication=no \
        -o ServerAliveInterval=3 \
        -o ServerAliveCountMax=2 \
        -p "$port" \
        -i "$KEY_PATH" \
        "${user}@${ip}" "echo OK" 2>/dev/null
}

# 使用 sshpass + ssh-copy-id 分发公钥
distribute_key() {
    local ip="$1"
    local port="$2"
    local user="$3"
    local password="$4"

    timeout 30 sshpass -p "$password" ssh-copy-id \
        -i "${KEY_PATH}.pub" \
        -o StrictHostKeyChecking=no \
        -o ConnectTimeout=10 \
        -o PreferredAuthentications=password \
        -p "$port" \
        "${user}@${ip}" 2>&1
}

# 处理单台主机
process_host() {
    local alias_name="$1"
    local ip="$2"
    local index="$3"
    local total="$4"

    local progress="[${index}/${total}]"
    echo -e "  ${DIM}${progress}${NC} ${BOLD}${alias_name}${NC} ${DIM}(${ip}:${SSH_PORT})${NC}"

    # 先检查是否已经免密
    local check_result
    check_result=$(check_passwordless "$ip" "$SSH_PORT" "$SSH_USER" 2>&1) || true

    if [[ "$check_result" == *"OK"* ]]; then
        log_info "已免密 — 无需操作"
        RESULT_IPS+=("$ip")
        RESULT_ALIASES+=("$alias_name")
        RESULT_STATUSES+=("ALREADY_OK")
        RESULT_MESSAGES+=("已配置免密")
        ((ALREADY_OK_COUNT++)) || true
        return 0
    fi

    # 需要配置免密
    if [ "$VERIFY_ONLY" -eq 1 ]; then
        log_warn "未免密"
        RESULT_IPS+=("$ip")
        RESULT_ALIASES+=("$alias_name")
        RESULT_STATUSES+=("NOT_CONFIGURED")
        RESULT_MESSAGES+=("未配置免密")
        ((FAIL_COUNT++)) || true
        return 1
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        log_warn "[模拟] 将分发公钥到此主机"
        RESULT_IPS+=("$ip")
        RESULT_ALIASES+=("$alias_name")
        RESULT_STATUSES+=("DRY_RUN")
        RESULT_MESSAGES+=("模拟运行")
        ((SKIP_COUNT++)) || true
        return 0
    fi

    # 先检查网络连通性（快速 ping 或 TCP 端口检测）
    if ! timeout 5 bash -c "echo >/dev/tcp/${ip}/${SSH_PORT}" 2>/dev/null; then
        log_error "网络不可达 (${ip}:${SSH_PORT})"
        RESULT_IPS+=("$ip")
        RESULT_ALIASES+=("$alias_name")
        RESULT_STATUSES+=("FAILED")
        RESULT_MESSAGES+=("网络不可达: ${ip}:${SSH_PORT}")
        ((FAIL_COUNT++)) || true
        return 1
    fi

    # 分发公钥
    log_substep "正在分发公钥..."
    local dist_result
    local dist_exit=0
    dist_result=$(distribute_key "$ip" "$SSH_PORT" "$SSH_USER" "$SSH_PASS" 2>&1) || dist_exit=$?

    if [ $dist_exit -ne 0 ]; then
        # 根据退出码判断原因
        local fail_reason="未知错误"
        case $dist_exit in
            5)   fail_reason="密码认证失败 (密码错误)" ;;
            6)   fail_reason="主机公钥验证失败" ;;
            124) fail_reason="操作超时 (30秒)" ;;
            255) fail_reason="SSH 连接被拒绝" ;;
            *)   fail_reason="ssh-copy-id 失败 (退出码: ${dist_exit})" ;;
        esac
        log_error "公钥分发失败: ${fail_reason}"
        if [ -n "$dist_result" ]; then
            log_substep "${DIM}${dist_result}${NC}"
        fi
    fi

    # 验证分发结果
    sleep 1
    local verify_result
    verify_result=$(check_passwordless "$ip" "$SSH_PORT" "$SSH_USER" 2>&1) || true

    if [[ "$verify_result" == *"OK"* ]]; then
        log_info "免密配置成功 ✓"
        RESULT_IPS+=("$ip")
        RESULT_ALIASES+=("$alias_name")
        RESULT_STATUSES+=("SUCCESS")
        RESULT_MESSAGES+=("免密配置成功")
        ((SUCCESS_COUNT++)) || true
        return 0
    else
        log_error "免密配置失败"
        log_substep "${DIM}可能原因: 密码错误 / 网络不通 / 目标主机 SSH 配置限制${NC}"
        RESULT_IPS+=("$ip")
        RESULT_ALIASES+=("$alias_name")
        RESULT_STATUSES+=("FAILED")
        RESULT_MESSAGES+=("配置失败: 请检查密码或网络")
        ((FAIL_COUNT++)) || true
        return 1
    fi
}

# ============================================================================
# 结果报告
# ============================================================================
print_report() {
    local total="$1"

    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}                      ${BOLD}执 行 结 果 报 告${NC}                          ${BLUE}║${NC}"
    echo -e "${BLUE}╠══════════════════════════════════════════════════════════════════╣${NC}"

    # 表头
    printf "${BLUE}║${NC}  %-18s %-17s %-10s %-14s ${BLUE}║${NC}\n" "主机别名" "IP 地址" "状态" "说明"
    echo -e "${BLUE}╠══════════════════════════════════════════════════════════════════╣${NC}"

    # 表体
    for i in "${!RESULT_IPS[@]}"; do
        local alias="${RESULT_ALIASES[$i]}"
        local ip="${RESULT_IPS[$i]}"
        local status="${RESULT_STATUSES[$i]}"
        local msg="${RESULT_MESSAGES[$i]}"

        local status_icon=""
        local status_color=""
        case "$status" in
            ALREADY_OK)
                status_icon="● 已免密"
                status_color="${GREEN}"
                ;;
            SUCCESS)
                status_icon="✔ 成功"
                status_color="${GREEN}"
                ;;
            FAILED|NOT_CONFIGURED)
                status_icon="✘ 失败"
                status_color="${RED}"
                ;;
            DRY_RUN)
                status_icon="◎ 模拟"
                status_color="${YELLOW}"
                ;;
            *)
                status_icon="? 未知"
                status_color="${DIM}"
                ;;
        esac

        printf "${BLUE}║${NC}  %-18s %-17s ${status_color}%-10s${NC} %-14s ${BLUE}║${NC}\n" \
            "$alias" "$ip" "$status_icon" "$msg"
    done

    echo -e "${BLUE}╠══════════════════════════════════════════════════════════════════╣${NC}"

    # 统计汇总
    local ok_total=$((ALREADY_OK_COUNT + SUCCESS_COUNT))
    echo -e "${BLUE}║${NC}  ${BOLD}汇总:${NC} 共 ${total} 台主机                                         ${BLUE}║${NC}"

    if [ "$VERIFY_ONLY" -eq 1 ]; then
        echo -e "${BLUE}║${NC}    ${GREEN}● 已免密: ${ALREADY_OK_COUNT}${NC}    ${RED}✘ 未免密: ${FAIL_COUNT}${NC}                              ${BLUE}║${NC}"
    elif [ "$DRY_RUN" -eq 1 ]; then
        echo -e "${BLUE}║${NC}    ${GREEN}● 已免密: ${ALREADY_OK_COUNT}${NC}    ${YELLOW}◎ 待配置: ${SKIP_COUNT}${NC}                              ${BLUE}║${NC}"
    else
        echo -e "${BLUE}║${NC}    ${GREEN}● 已免密: ${ALREADY_OK_COUNT}${NC}  ${GREEN}✔ 新配置: ${SUCCESS_COUNT}${NC}  ${RED}✘ 失败: ${FAIL_COUNT}${NC}                  ${BLUE}║${NC}"
    fi

    echo -e "${BLUE}╚══════════════════════════════════════════════════════════════════╝${NC}"

    # 最终状态
    echo ""
    if [ "$FAIL_COUNT" -eq 0 ]; then
        if [ "$VERIFY_ONLY" -eq 1 ]; then
            echo -e "  ${GREEN}${BOLD}✔ 所有主机均已配置免密登录${NC}"
        elif [ "$DRY_RUN" -eq 1 ]; then
            echo -e "  ${YELLOW}${BOLD}◎ 模拟运行完成，未做任何修改${NC}"
        else
            echo -e "  ${GREEN}${BOLD}✔ 所有主机免密配置完成！${NC}"
        fi
    else
        if [ "$VERIFY_ONLY" -eq 1 ]; then
            echo -e "  ${YELLOW}${BOLD}⚠ 有 ${FAIL_COUNT} 台主机尚未配置免密，请运行本脚本（不带 --verify-only）进行配置${NC}"
        else
            echo -e "  ${RED}${BOLD}✘ 有 ${FAIL_COUNT} 台主机免密配置失败，请检查网络和密码后重试${NC}"
        fi
    fi
    echo ""
}

# ============================================================================
# 主流程
# ============================================================================
main() {
    parse_args "$@"
    print_banner

    # 初始化 SSH 配置
    init_ssh_config

    # 前置检查
    check_prerequisites || exit 1

    # 解析主机列表
    log_step "解析 tdsql_hosts 主机列表"
    local hosts_data
    hosts_data=$(parse_unique_ips "$HOSTS_FILE") || exit 1

    local host_list=()
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        host_list+=("$line")
    done <<< "$hosts_data"

    TOTAL_HOSTS=${#host_list[@]}
    log_info "共发现 ${TOTAL_HOSTS} 台唯一主机（已去重）"

    # 显示主机列表
    for entry in "${host_list[@]}"; do
        local alias_name="${entry%%,*}"
        local ip="${entry#*,}"
        log_substep "${alias_name} → ${ip}"
    done

    # 密钥检查/生成（非 verify-only 模式）
    if [ "$VERIFY_ONLY" -eq 0 ]; then
        ensure_ssh_key || exit 1
    fi

    # 批量处理
    if [ "$VERIFY_ONLY" -eq 1 ]; then
        log_step "验证免密登录状态"
    elif [ "$DRY_RUN" -eq 1 ]; then
        log_step "模拟运行（不做实际修改）"
    else
        log_step "开始批量配置免密登录"
    fi

    local index=0
    for entry in "${host_list[@]}"; do
        local alias_name="${entry%%,*}"
        local ip="${entry#*,}"
        ((index++)) || true

        process_host "$alias_name" "$ip" "$index" "$TOTAL_HOSTS" || true
    done

    # 输出报告
    print_report "$TOTAL_HOSTS"

    # 返回码
    if [ "$FAIL_COUNT" -gt 0 ]; then
        return 1
    fi
    return 0
}

main "$@"
