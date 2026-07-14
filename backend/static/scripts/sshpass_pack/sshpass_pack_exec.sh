#!/bin/sh
#
# TDSQL 远程命令批量执行工具
# 基于 scheduler 的 sshpass_pack.sh 封装，按 tdsql_hosts 中的顺序执行远程命令
#
# 作者: boogqwang
#

VERSION="1.4.0"
SCRIPT_NAME=$(basename "$0")
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
TOOLKIT_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." 2>/dev/null && pwd)

HOSTS_FILE="${SCRIPT_DIR}/tdsql_hosts"
TARGET_GROUP="tdsql_allmacforcheck"
QUIET=0
DRY_RUN=0
SHOW_GROUPS=0

SSHPASS_PACK_BIN="/data/application/scheduler/bin/sshpass_pack.sh"
SCHEDULER_XML="/data/application/scheduler/conf/scheduler.xml"
ENV_TOKEN="${SSHPASS_PACK_TOKEN:-}"
ACCESS_TOKEN=""
REMOTE_USER="tdsql"
REMOTE_PORT="8966"
REMOTE_WORKDIR="/data/oc_agent/bin"
REMOTE_FLAG="0"
REMOTE_TIMEOUT="10"
TOKEN_SOURCE=""
USER_ARG_SET=0
PORT_ARG_SET=0
WORKDIR_ARG_SET=0
FLAG_ARG_SET=0
TIMEOUT_ARG_SET=0
SCHEDULER_XML_ARG_SET=0

# tdsql_env.conf 路径（-c/--env-conf 可覆盖；默认在项目根/脚本同目录查找）
ENV_CONF=""
ENV_CONF_ARG_SET=0

log_info() {
    [ "$QUIET" -eq 1 ] && return
    printf '[INFO] %s\n' "$*" >&2
}

log_error() {
    printf '[ERROR] %s\n' "$*" >&2
}

extract_xml_attr() {
    xml_line="$1"
    attr_name="$2"
    printf '%s\n' "$xml_line" | sed -n "s/.*${attr_name}=\"\([^\"]*\)\".*/\1/p"
}

# 自动查找 tdsql_env.conf（项目根 > 脚本同目录）
find_env_conf() {
    if [ -n "$ENV_CONF" ] && [ -f "$ENV_CONF" ]; then
        printf '%s\n' "$ENV_CONF"
        return
    fi
    if [ -n "$TOOLKIT_ROOT" ] && [ -f "${TOOLKIT_ROOT}/tdsql_env.conf" ]; then
        printf '%s\n' "${TOOLKIT_ROOT}/tdsql_env.conf"
        return
    fi
    if [ -f "${SCRIPT_DIR}/tdsql_env.conf" ]; then
        printf '%s\n' "${SCRIPT_DIR}/tdsql_env.conf"
        return
    fi
    printf '\n'
}

# 从 tdsql_env.conf 加载 [sshpass_pack] 段
# 覆盖顺序：CLI > env > tdsql_env.conf > scheduler.xml > 内置默认
# 因此本函数只在对应变量“未被 CLI 显式指定”且“当前无值/需要补齐”时才写入
load_env_conf() {
    conf_file=$(find_env_conf)
    [ -z "$conf_file" ] && return 0
    [ ! -f "$conf_file" ] && return 0

    # 用 awk 提取 [sshpass_pack] 段的 key=value（POSIX sh 兼容，不依赖 bash loader）
    _section_data=$(awk '
        BEGIN { in_section = 0 }
        {
            sub(/\r$/, "", $0)
            line = $0
            sub(/^[[:space:]]+/, "", line)
            sub(/[[:space:]]+$/, "", line)
        }
        line == "" { next }
        line ~ /^#/ { next }
        line ~ /^\[.+\]$/ {
            sect = line
            gsub(/^\[|\]$/, "", sect)
            in_section = (sect == "sshpass_pack") ? 1 : 0
            next
        }
        in_section && line ~ /^[a-zA-Z_][a-zA-Z0-9_]*=/ { print line }
    ' "$conf_file")

    [ -z "$_section_data" ] && {
        ENV_CONF="$conf_file"
        return 0
    }

    ENV_CONF="$conf_file"

    _conf_token=""
    _conf_user=""
    _conf_port=""
    _conf_workdir=""
    _conf_scheduler_xml=""
    _conf_flag=""
    _conf_timeout=""

    IFS_ORIG=$IFS
    IFS='
'
    for _line in $_section_data; do
        _key=$(printf '%s' "$_line" | sed -n 's/^\([^=]*\)=.*/\1/p')
        _val=$(printf '%s' "$_line" | sed -n 's/^[^=]*=\(.*\)$/\1/p')
        case "$_key" in
            token)          _conf_token="$_val" ;;
            user)           _conf_user="$_val" ;;
            port)           _conf_port="$_val" ;;
            oc_dir|workdir) _conf_workdir="$_val" ;;
            scheduler_xml)  _conf_scheduler_xml="$_val" ;;
            flag)           _conf_flag="$_val" ;;
            timeout)        _conf_timeout="$_val" ;;
        esac
    done
    IFS=$IFS_ORIG

    # 若 CLI 未显式指定，则用 conf 里的值覆盖当前默认（此时 scheduler.xml 尚未加载）
    if [ "$USER_ARG_SET" -eq 0 ] && [ -n "$_conf_user" ]; then
        REMOTE_USER="$_conf_user"
    fi
    if [ "$PORT_ARG_SET" -eq 0 ] && [ -n "$_conf_port" ]; then
        REMOTE_PORT="$_conf_port"
    fi
    if [ "$WORKDIR_ARG_SET" -eq 0 ] && [ -n "$_conf_workdir" ]; then
        REMOTE_WORKDIR="$_conf_workdir"
    fi
    if [ "$FLAG_ARG_SET" -eq 0 ] && [ -n "$_conf_flag" ]; then
        REMOTE_FLAG="$_conf_flag"
    fi
    if [ "$TIMEOUT_ARG_SET" -eq 0 ] && [ -n "$_conf_timeout" ]; then
        REMOTE_TIMEOUT="$_conf_timeout"
    fi
    if [ "$SCHEDULER_XML_ARG_SET" -eq 0 ] && [ -n "$_conf_scheduler_xml" ]; then
        SCHEDULER_XML="$_conf_scheduler_xml"
    fi

    # token 优先级：CLI > env > env.conf > scheduler.xml
    # CLI 已在 parse_args 里赋 ACCESS_TOKEN，此处只在 CLI 未给且 env 未给时用 conf 值
    if [ -z "$ACCESS_TOKEN" ] && [ -z "$ENV_TOKEN" ] && [ -n "$_conf_token" ]; then
        ACCESS_TOKEN="$_conf_token"
        TOKEN_SOURCE="tdsql_env.conf([sshpass_pack])"
    fi
}

load_scheduler_config() {
    if [ -z "$ACCESS_TOKEN" ] && [ -n "$ENV_TOKEN" ]; then
        _env_token_pending=1
    else
        _env_token_pending=0
    fi

    if [ ! -f "$SCHEDULER_XML" ]; then
        if [ "$_env_token_pending" -eq 1 ]; then
            ACCESS_TOKEN="$ENV_TOKEN"
            TOKEN_SOURCE="env(SSHPASS_PACK_TOKEN)"
        fi
        return 0
    fi

    scheduler_line=$(awk -v target_user="$REMOTE_USER" '
        /<sshpass[[:space:]]/ {
            if (first == "") {
                first = $0
            }
            if (index($0, "user=\"" target_user "\"") > 0) {
                print $0
                matched = 1
                exit
            }
        }
        END {
            if (matched == 0 && first != "") {
                print first
            }
        }
    ' "$SCHEDULER_XML" | sed -n '1p')

    [ -n "$scheduler_line" ] || {
        # scheduler.xml 存在但没匹配到 <sshpass> 行；仍需处理 env 兜底
        if [ -z "$ACCESS_TOKEN" ] && [ -n "$ENV_TOKEN" ]; then
            ACCESS_TOKEN="$ENV_TOKEN"
            TOKEN_SOURCE="env(SSHPASS_PACK_TOKEN)"
        fi
        return 0
    }

    scheduler_user=$(extract_xml_attr "$scheduler_line" "user")
    scheduler_token=$(extract_xml_attr "$scheduler_line" "password_encrypt")
    scheduler_port=$(extract_xml_attr "$scheduler_line" "port")
    scheduler_workdir=$(extract_xml_attr "$scheduler_line" "oc_dir")

    # scheduler.xml 中的字段仅在 CLI 与 env.conf 都未指定时使用
    if [ "$USER_ARG_SET" -eq 0 ] && [ -n "$scheduler_user" ] && [ "$REMOTE_USER" = "tdsql" ]; then
        REMOTE_USER="$scheduler_user"
    fi

    # env 优先于 scheduler.xml
    if [ -z "$ACCESS_TOKEN" ] && [ -n "$ENV_TOKEN" ]; then
        ACCESS_TOKEN="$ENV_TOKEN"
        TOKEN_SOURCE="env(SSHPASS_PACK_TOKEN)"
    fi

    if [ -z "$ACCESS_TOKEN" ] && [ -n "$scheduler_token" ]; then
        ACCESS_TOKEN="$scheduler_token"
        TOKEN_SOURCE="scheduler.xml"
    fi

    if [ "$PORT_ARG_SET" -eq 0 ] && [ -n "$scheduler_port" ] && [ "$REMOTE_PORT" = "8966" ]; then
        REMOTE_PORT="$scheduler_port"
    fi

    if [ "$WORKDIR_ARG_SET" -eq 0 ] && [ -n "$scheduler_workdir" ] && [ "$REMOTE_WORKDIR" = "/data/oc_agent/bin" ]; then
        REMOTE_WORKDIR="$scheduler_workdir"
    fi
}

show_help() {
    cat <<EOF
用法:
  sh ${SCRIPT_NAME} [选项] -- "远程命令"
  sh ${SCRIPT_NAME} [选项] "远程命令"

说明:
  基于本机 scheduler 的 sshpass_pack.sh，按 tdsql_hosts 中指定分组的主机顺序，
  逐台执行远程 shell 命令，并输出"IP + 结果"。

  多集群管理:
    同一台管理节点上部署多份 tdsql-toolkit 工程时，各自的
    tdsql_env.conf [sshpass_pack] 段可分别配置该集群的 token，
    避免共用本机 scheduler.xml 出现 token 错乱。

  token 获取优先级:
    1. -t / --token
    2. 环境变量 SSHPASS_PACK_TOKEN
    3. tdsql_env.conf 的 [sshpass_pack] 段 token=
    4. scheduler.xml 中 <sshpass ... password_encrypt="..." />

选项:
  -t, --token TOKEN      访问 token（可选，默认按上述优先级读取）
  -c, --env-conf FILE    指定 tdsql_env.conf 路径
                         （默认查找项目根 > 脚本同目录）
  -H, --hosts FILE       主机清单路径（默认: ./tdsql_hosts）
  -g, --group NAME       主机分组名（默认: tdsql_allmacforcheck）
      --bin FILE         底层 sshpass_pack.sh 路径
      --scheduler-xml FILE
                         scheduler.xml 路径
                         （默认: /data/application/scheduler/conf/scheduler.xml，
                          可被 [sshpass_pack].scheduler_xml 覆盖）
      --user USER        远程用户（默认按 CLI > conf > scheduler.xml > tdsql）
      --port PORT        远程端口（默认按 CLI > conf > scheduler.xml > 8966）
      --workdir DIR      远程工作目录
                         （默认按 CLI > conf > scheduler.xml > /data/oc_agent/bin）
      --flag VALUE       底层固定参数（默认: 0）
      --timeout SEC      底层超时参数（默认: 10）
      --list-groups      列出 tdsql_hosts 中所有分组并退出
      --dry-run          只打印将要执行的底层命令，不实际执行
  -q, --quiet            静默模式，减少 stderr 提示信息
  -h, --help             显示帮助信息
  -V, --version          显示版本号

主机清单格式:
  复用 tdsql_hosts 中已有格式，例如:

  [tdsql_db]
  tdsql_db1 ansible_ssh_host=10.206.0.15
  tdsql_db2 ansible_ssh_host=10.206.0.16

如何获取 token（更新 [sshpass_pack] token= 时使用）:
  # 方法A：从当前节点或该集群 scheduler 节点读取 password_encrypt
  grep 'password_encrypt=' /data/application/scheduler/conf/scheduler.xml | head -n1

  # 方法B：跨集群管理时 SSH 到该集群 scheduler 节点抓取
  ssh <该集群 scheduler 节点> "grep password_encrypt \\
      /data/application/scheduler/conf/scheduler.xml | head -n1"

  拿到 password_encrypt="xxxxxxx" 中的 xxxxxxx，写入
  tdsql_env.conf 的 [sshpass_pack] token= 即可。集群升级 / 密码轮换后
  若批量报 "Password vertify failed"，重新取一次并更新 conf 即可。

使用示例:
  # 查看所有分组
  sh ${SCRIPT_NAME} --list-groups

  # 方式1：多集群工程，直接读各自的 tdsql_env.conf [sshpass_pack]
  sh ${SCRIPT_NAME} -g tdsql_db -- "df -h | grep dev"

  # 方式2：命令行传 token
  sh ${SCRIPT_NAME} -t 'your_token' -g tdsql_db -- "df -h | grep dev"

  # 方式3：环境变量传 token
  export SSHPASS_PACK_TOKEN='your_token'
  sh ${SCRIPT_NAME} -g tdsql_db -- "df -h | grep dev"

  # 方式4：显式指定另一集群的 conf 文件
  sh ${SCRIPT_NAME} -c /data/cluster-b/tdsql-toolkit/tdsql_env.conf -g tdsql_db "hostname"

  # 预览底层实际调用（token 会被打码）
  sh ${SCRIPT_NAME} -g tdsql_proxy --dry-run "hostname"

注意:
  1. 远程命令建议整体加双引号，避免被本地 shell 提前解释。
  2. 脚本按 tdsql_hosts 文件中的顺序逐台执行，便于顺序排查。
  3. dry-run 输出会隐藏 token，避免敏感信息直接回显。
EOF
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            -t|--token)
                ACCESS_TOKEN="$2"
                TOKEN_SOURCE="cli"
                shift 2
                ;;
            -c|--env-conf)
                ENV_CONF="$2"
                ENV_CONF_ARG_SET=1
                shift 2
                ;;
            -H|--hosts)
                HOSTS_FILE="$2"
                shift 2
                ;;
            -g|--group)
                TARGET_GROUP="$2"
                shift 2
                ;;
            --bin)
                SSHPASS_PACK_BIN="$2"
                shift 2
                ;;
            --scheduler-xml)
                SCHEDULER_XML="$2"
                SCHEDULER_XML_ARG_SET=1
                shift 2
                ;;
            --user)
                REMOTE_USER="$2"
                USER_ARG_SET=1
                shift 2
                ;;
            --port)
                REMOTE_PORT="$2"
                PORT_ARG_SET=1
                shift 2
                ;;
            --workdir)
                REMOTE_WORKDIR="$2"
                WORKDIR_ARG_SET=1
                shift 2
                ;;
            --flag)
                REMOTE_FLAG="$2"
                FLAG_ARG_SET=1
                shift 2
                ;;
            --timeout)
                REMOTE_TIMEOUT="$2"
                TIMEOUT_ARG_SET=1
                shift 2
                ;;
            --list-groups)
                SHOW_GROUPS=1
                shift
                ;;
            --dry-run)
                DRY_RUN=1
                shift
                ;;
            -q|--quiet)
                QUIET=1
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            -V|--version)
                printf '%s v%s\n' "$SCRIPT_NAME" "$VERSION"
                exit 0
                ;;
            --)
                shift
                break
                ;;
            -*)
                log_error "未知参数: $1"
                log_error "使用 -h 查看帮助信息"
                exit 1
                ;;
            *)
                break
                ;;
        esac
    done

    REMOTE_CMD="$*"
}

list_groups() {
    if [ ! -f "$HOSTS_FILE" ]; then
        log_error "主机清单不存在: $HOSTS_FILE"
        exit 1
    fi

    awk '
        { sub(/\r$/, "", $0) }
        /^[[:space:]]*\[/ {
            line = $0
            gsub(/^[[:space:]]*\[/, "", line)
            gsub(/\][[:space:]]*$/, "", line)
            print line
        }
    ' "$HOSTS_FILE"
}

load_targets() {
    if [ ! -f "$HOSTS_FILE" ]; then
        log_error "主机清单不存在: $HOSTS_FILE"
        exit 1
    fi

    TARGETS_FILE=$(mktemp "${TMPDIR:-/tmp}/sshpass_pack_targets.XXXXXX")
    trap 'rm -f "$TARGETS_FILE"' EXIT INT TERM HUP

    awk -v target_group="$TARGET_GROUP" '
        BEGIN { in_group = 0 }
        { sub(/\r$/, "", $0) }
        /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
        /^[[:space:]]*\[/ {
            group_name = $0
            gsub(/^[[:space:]]*\[/, "", group_name)
            gsub(/\][[:space:]]*$/, "", group_name)
            in_group = (group_name == target_group)
            next
        }
        in_group {
            host_alias = $1
            host_ip = ""
            for (i = 1; i <= NF; i++) {
                if ($i ~ /^ansible_ssh_host=/) {
                    split($i, arr, "=")
                    host_ip = arr[2]
                }
            }
            if (host_alias != "" && host_ip != "") {
                print host_alias "|" host_ip
            }
        }
    ' "$HOSTS_FILE" > "$TARGETS_FILE"

    TARGET_COUNT=$(grep -c '.' "$TARGETS_FILE" 2>/dev/null)
    if [ -z "$TARGET_COUNT" ] || [ "$TARGET_COUNT" -eq 0 ]; then
        log_error "分组 $TARGET_GROUP 中没有解析到有效主机"
        exit 1
    fi
}

validate_runtime() {
    if [ "$SHOW_GROUPS" -eq 1 ]; then
        return 0
    fi

    load_env_conf
    load_scheduler_config

    if [ -z "$ACCESS_TOKEN" ]; then
        log_error "缺少 token，已尝试按优先级读取 CLI/-t、env(SSHPASS_PACK_TOKEN)、"
        log_error "  tdsql_env.conf [sshpass_pack].token、${SCHEDULER_XML} 均未取到"
        log_error "获取方式: grep 'password_encrypt=' ${SCHEDULER_XML} | head -n1"
        log_error "然后写入 tdsql_env.conf 的 [sshpass_pack] token= 或使用 -t 传入"
        exit 1
    fi

    if [ "$DRY_RUN" -eq 0 ] && [ ! -x "$SSHPASS_PACK_BIN" ]; then
        log_error "底层工具不存在或不可执行: $SSHPASS_PACK_BIN"
        exit 1
    fi
}

run_one_host() {
    host_alias="$1"
    host_ip="$2"
    index="$3"

    printf '============================================================\n'
    printf '[%s/%s] %s (%s)\n' "$index" "$TARGET_COUNT" "$host_ip" "$host_alias"
    printf '%s\n' '------------------------------------------------------------'

    if [ "$DRY_RUN" -eq 1 ]; then
        printf '%s\n' "${SSHPASS_PACK_BIN} [MASKED_TOKEN] ${REMOTE_USER} ${host_ip} ${REMOTE_PORT} ${REMOTE_WORKDIR} ${REMOTE_FLAG} ${REMOTE_TIMEOUT} \"${REMOTE_CMD}\""
        printf '\n'
        return 0
    fi

    result=$("$SSHPASS_PACK_BIN" "$ACCESS_TOKEN" "$REMOTE_USER" "$host_ip" "$REMOTE_PORT" "$REMOTE_WORKDIR" "$REMOTE_FLAG" "$REMOTE_TIMEOUT" "$REMOTE_CMD" < /dev/null 2>&1)
    status=$?

    if [ "$status" -eq 0 ]; then
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        printf '状态: 成功\n'
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        printf '状态: 失败 (exit=%s)\n' "$status"
    fi

    if [ -n "$result" ]; then
        printf '%s\n' "$result"
    else
        printf '(无输出)\n'
    fi

    printf '\n'
}

main() {
    parse_args "$@"

    if [ "$SHOW_GROUPS" -eq 1 ]; then
        list_groups
        exit 0
    fi

    if [ -z "$REMOTE_CMD" ]; then
        log_error "缺少远程命令"
        log_error "示例: sh ${SCRIPT_NAME} -g tdsql_db -- \"df -h | grep dev\""
        exit 1
    fi

    validate_runtime
    load_targets

    log_info "目标分组: $TARGET_GROUP"
    log_info "主机数量: $TARGET_COUNT"
    log_info "执行命令: $REMOTE_CMD"
    if [ -n "$ENV_CONF" ]; then
        log_info "配置文件: $ENV_CONF"
    fi
    case "$TOKEN_SOURCE" in
        scheduler.xml)
            log_info "token 来源: ${SCHEDULER_XML}"
            ;;
        env*)
            log_info "token 来源: 环境变量 SSHPASS_PACK_TOKEN"
            ;;
        cli)
            log_info "token 来源: 命令行 -t"
            ;;
        tdsql_env.conf*)
            log_info "token 来源: ${TOKEN_SOURCE}"
            ;;
    esac

    SUCCESS_COUNT=0
    FAIL_COUNT=0
    CURRENT_INDEX=0

    while IFS='|' read -r host_alias host_ip; do
        [ -z "$host_ip" ] && continue
        CURRENT_INDEX=$((CURRENT_INDEX + 1))
        run_one_host "$host_alias" "$host_ip" "$CURRENT_INDEX"
    done < "$TARGETS_FILE"

    log_info "执行完成: 成功 ${SUCCESS_COUNT} 台，失败 ${FAIL_COUNT} 台"
}

main "$@"