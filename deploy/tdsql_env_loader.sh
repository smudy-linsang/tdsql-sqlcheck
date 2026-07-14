#!/bin/bash
################################################################################
# tdsql_env_loader.sh — TDSQL Toolkit 通用配置文件加载器
#
# 提供 tdsql_env.conf 的解析和加载函数，各模块脚本 source 引用即可。
#
# 使用方法:
#   source "${TOOLKIT_ROOT}/tdsql_env_loader.sh"
#
#   # 加载监控库配置 → 设置 DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_NAME
#   load_monitor_db "/path/to/tdsql_env.conf"
#
#   # 加载 SSH 配置 → 设置 SSH_HOST, SSH_USER, SSH_PASS
#   load_ssh_config "/path/to/tdsql_env.conf"
#
#   # 提取实例列表（每行一个值，按 key 排序）
#   tdsql_parse_values "instances" < tdsql_env.conf
#   tdsql_parse_values "db_instances" < tdsql_env.conf
#
# 版本: 1.3
#
# 兼容性说明:
#   本文件及使用本文件的脚本必须以 bash 执行（#!/bin/bash），
#   不可使用 sh 执行（sh 不支持 process substitution 等 bash 特性）。
################################################################################

# 防止重复加载
[[ -n "${_TDSQL_ENV_LOADER_LOADED:-}" ]] && return 0
_TDSQL_ENV_LOADER_LOADED=1

# ============================================================================
# 核心解析函数
# ============================================================================

# 解析 tdsql_env.conf 中指定 section 的 key=value 配置
# 用法: tdsql_parse_section "section_name" < config_file
# 输出: 每行一个 key=value（保留原始 key 和 value）
tdsql_parse_section() {
    local target_section="$1"
    local in_section=0
    
    while IFS= read -r line || [[ -n "$line" ]]; do
        # 去除 Windows 回车符
        line=$(echo "$line" | tr -d '\r')
        # 去除首尾空格
        line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        
        # 跳过空行
        [[ -z "$line" ]] && continue
        # 跳过注释
        [[ "$line" =~ ^# ]] && continue
        
        # 检测 section 头
        if [[ "$line" =~ ^\[(.+)\]$ ]]; then
            if [[ "${BASH_REMATCH[1]}" == "$target_section" ]]; then
                in_section=1
            else
                # 进入了其他 section，如果之前在目标 section 则退出
                [[ $in_section -eq 1 ]] && break
            fi
            continue
        fi
        
        # 如果在目标 section 内，输出 key=value 行
        if [[ $in_section -eq 1 ]] && [[ "$line" =~ ^[a-zA-Z_][a-zA-Z0-9_]*= ]]; then
            echo "$line"
        fi
    done
}

# 解析 tdsql_env.conf 中指定 section 的值列表
# 提取所有 key=value 中的 value 部分，按 key 名称排序输出
# 用法: tdsql_parse_values "section_name" < config_file
# 输出: 每行一个 value（不含 key= 前缀），按 key 字典序排序
# 适用于: [instances]、[slow_query_instances]、[db_instances]、[gateway_servers] 等列表段
tdsql_parse_values() {
    local target_section="$1"
    
    # 先用 tdsql_parse_section 提取 key=value 行，再按 key 排序，最后去掉 key= 前缀
    tdsql_parse_section "$target_section" | sort -t'=' -k1,1V | while IFS= read -r line; do
        # 去掉 key= 前缀（保留第一个等号后面的所有内容）
        echo "${line#*=}"
    done
}

# ============================================================================
# 便捷加载函数
# ============================================================================

# 自动查找 tdsql_env.conf 文件
# 参数: $1 = 脚本所在目录 (SCRIPT_DIR)
# 输出: 配置文件的绝对路径（找不到返回空字符串）
tdsql_find_env_conf() {
    local script_dir="$1"
    local toolkit_root
    toolkit_root="$(cd "${script_dir}/.." 2>/dev/null && pwd)"
    
    # 查找顺序：项目根目录 > 脚本同目录
    if [ -f "${toolkit_root}/tdsql_env.conf" ]; then
        echo "${toolkit_root}/tdsql_env.conf"
    elif [ -f "${script_dir}/tdsql_env.conf" ]; then
        echo "${script_dir}/tdsql_env.conf"
    fi
}

# 加载 [monitor_db] 段落，设置 DB_HOST/DB_PORT/DB_USER/DB_PASS/DB_NAME 变量
# 参数: $1 = 配置文件路径
# 返回: 0=成功, 1=失败
load_monitor_db() {
    local conf_file="$1"
    
    if [ ! -f "$conf_file" ]; then
        echo "[ERROR] 通用配置文件不存在: ${conf_file}" >&2
        echo "[ERROR] 请复制模板创建: cp tdsql_env.conf.example tdsql_env.conf" >&2
        return 1
    fi
    
    # 先将段落内容提取到临时变量，再逐行解析（避免 process substitution）
    local _section_data
    _section_data=$(tdsql_parse_section "monitor_db" < "$conf_file")
    
    local line key value
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        key="${line%%=*}"
        value="${line#*=}"
        case "$key" in
            host)     DB_HOST="$value" ;;
            port)     DB_PORT="$value" ;;
            user)     DB_USER="$value" ;;
            password) DB_PASS="$value" ;;
            database) DB_NAME="$value" ;;
        esac
    done <<EOF
${_section_data}
EOF
    
    # 校验必填项
    if [ -z "${DB_HOST:-}" ] || [ -z "${DB_PORT:-}" ] || [ -z "${DB_USER:-}" ] || [ -z "${DB_PASS:-}" ]; then
        echo "[ERROR] 配置文件 [monitor_db] 段落缺少必填项 (host/port/user/password)" >&2
        return 1
    fi
    
    # 设置默认数据库名
    DB_NAME="${DB_NAME:-tdsqlpcloud_monitor}"
    
    return 0
}

# 加载 [ssh] 段落，设置 SSH_HOST/SSH_USER/SSH_PASS 变量
# 参数: $1 = 配置文件路径
# 返回: 0=成功, 1=失败
load_ssh_config() {
    local conf_file="$1"
    
    if [ ! -f "$conf_file" ]; then
        echo "[ERROR] 通用配置文件不存在: ${conf_file}" >&2
        return 1
    fi
    
    # 先将段落内容提取到临时变量，再逐行解析（避免 process substitution）
    local _section_data
    _section_data=$(tdsql_parse_section "ssh" < "$conf_file")
    
    local line key value
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        key="${line%%=*}"
        value="${line#*=}"
        case "$key" in
            host)     SSH_HOST="$value" ;;
            user)     SSH_USER="$value" ;;
            password) SSH_PASS="$value" ;;
        esac
    done <<EOF
${_section_data}
EOF
    
    if [ -z "${SSH_HOST:-}" ] || [ -z "${SSH_USER:-}" ]; then
        echo "[ERROR] 配置文件 [ssh] 段落缺少必填项 (host/user)" >&2
        return 1
    fi
    
    return 0
}

# 加载 [oss_decrypt] 段落，用于 TDSQL v6+ tdsqlsys_normal 密文密码回退解密
# 参数: $1 = 配置文件路径
# 返回: 0=段落存在且有效, 1=段落缺失或无 host（视为未配置，静默失败）
# 输出变量:
#   OSS_DECRYPT_HOST      OSS/gateway 节点 IP
#   OSS_DECRYPT_PORT      SSH 端口（默认 36000）
#   OSS_DECRYPT_USER      SSH 用户名（默认 root）
#   OSS_DECRYPT_PASSWORD  SSH 密码（可空）
#   OSS_DECRYPT_BIN       manual_set 绝对路径
#   OSS_DECRYPT_SSH_KEY   SSH 私钥路径（可空）
# 无 host 时不报错，仅返回 1，让上层脚本决定如何处理（本机可能有 manual_set）
load_oss_decrypt() {
    local conf_file="$1"
    
    OSS_DECRYPT_HOST=""
    OSS_DECRYPT_PORT=""
    OSS_DECRYPT_USER=""
    OSS_DECRYPT_PASSWORD=""
    OSS_DECRYPT_BIN=""
    OSS_DECRYPT_SSH_KEY=""
    
    if [ ! -f "$conf_file" ]; then
        return 1
    fi
    
    local _section_data
    _section_data=$(tdsql_parse_section "oss_decrypt" < "$conf_file")
    [ -z "${_section_data}" ] && return 1
    
    local line key value
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        key="${line%%=*}"
        value="${line#*=}"
        case "$key" in
            host)     OSS_DECRYPT_HOST="$value" ;;
            port)     OSS_DECRYPT_PORT="$value" ;;
            user)     OSS_DECRYPT_USER="$value" ;;
            password) OSS_DECRYPT_PASSWORD="$value" ;;
            bin)      OSS_DECRYPT_BIN="$value" ;;
            ssh_key)  OSS_DECRYPT_SSH_KEY="$value" ;;
        esac
    done <<EOF
${_section_data}
EOF
    
    # host 是否配置作为该段是否启用的判据
    if [ -z "${OSS_DECRYPT_HOST}" ]; then
        return 1
    fi
    
    # 默认值补齐
    OSS_DECRYPT_PORT="${OSS_DECRYPT_PORT:-36000}"
    OSS_DECRYPT_USER="${OSS_DECRYPT_USER:-root}"
    
    export OSS_DECRYPT_HOST OSS_DECRYPT_PORT OSS_DECRYPT_USER \
           OSS_DECRYPT_PASSWORD OSS_DECRYPT_BIN OSS_DECRYPT_SSH_KEY
    return 0
}