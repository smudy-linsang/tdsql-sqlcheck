#!/bin/bash
################################################################################
# tdsql_inventory.sh — TDSQL 实例清单自动发现工具
#
# 功能:
#   从 ZooKeeper 的 /tdsqlzk/sets/ 节点自动发现所有 TDSQL 实例，
#   提取 proxy 地址、运维账号、密码、运行状态等元数据，
#   输出与 count_table_rows / index_analysis / table_schema_diff
#   工程使用的 db_config.conf 完全兼容的 CSV 格式。
#
# 输出格式 (默认 6 列, 与 db_config.conf 新格式一致):
#   service_name,host,port,user,password,database
#
# 启用 --with-status 时输出 8 列 (附加状态信息):
#   service_name,host,port,user,password,database,status_code,status_text
#
# 依赖:
#   - bash 4.x
#   - zkCli.sh (默认 /data/application/zookeeper/bin/zkCli.sh)
#   - mysql 客户端 (用于从监控库补全 instance_name 中文名)
#   - python3 (用于解析 setrun JSON)
#
# 优先级 (高 -> 低):
#   命令行参数 > 环境变量 > tdsql_env.conf > 内置默认值
#
# 用法:
#   ./tdsql_inventory.sh                                # 输出到 stdout
#   ./tdsql_inventory.sh --output /tmp/db_config.auto   # 输出到文件
#   ./tdsql_inventory.sh --status-filter 0              # 只要运营中实例
#   ./tdsql_inventory.sh --with-status                  # 附加状态列
#   ./tdsql_inventory.sh --proxy-mode random            # 随机选 proxy (默认)
#   ./tdsql_inventory.sh --proxy-mode first             # 总是取第一个 proxy
#   ./tdsql_inventory.sh --default-database ALL         # 设置默认 database
#
# 作者: With (TDSQL Toolkit)
# 版本: 1.0.0
################################################################################

set -uo pipefail

VERSION="1.0.1"
SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ============================================================================
# 进程互斥锁 + 孤儿清理（防止重复启动叠加导致 ZK 连接抢占/JVM 抖动卡死）
# ============================================================================
_LOCK_FILE="/tmp/tdsql_inventory.lock"
_clean_orphans() {
    # 清理超过 10 分钟仍未退出的同名僵死进程（典型的 zkCli 卡死场景）
    # 注意：跳过当前进程组内的所有进程（避免误伤自己或父进程/兄弟进程）
    local _my_pgid
    _my_pgid=$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')
    local _pid _etime _pgid
    while read -r _pid _etime _pgid _; do
        [ -z "${_pid}" ] && continue
        [ "${_pid}" = "$$" ] && continue
        # 同一进程组内的兄弟/父进程一律不清理
        [ -n "${_my_pgid}" ] && [ "${_pgid}" = "${_my_pgid}" ] && continue
        # etimes 是秒数；超过 600s（10 分钟）的视为僵死
        if [ "${_etime}" -gt 600 ] 2>/dev/null; then
            kill -9 "${_pid}" 2>/dev/null || true
        fi
    done < <(pgrep -af 'tdsql_inventory.sh' 2>/dev/null \
        | awk '{print $1}' \
        | xargs -r -I{} ps -o pid=,etimes=,pgid= -p {} 2>/dev/null)
    # 同步清理可能被这些孤儿持有的 zkCli/ZooKeeperMain（同样跳过自己进程组）
    while read -r _pid _etime _pgid _; do
        [ -z "${_pid}" ] && continue
        [ -n "${_my_pgid}" ] && [ "${_pgid}" = "${_my_pgid}" ] && continue
        if [ "${_etime}" -gt 600 ] 2>/dev/null; then
            kill -9 "${_pid}" 2>/dev/null || true
        fi
    done < <(pgrep -f 'org\.apache\.zookeeper\.ZooKeeperMain.*127\.0\.0\.1:2118' 2>/dev/null \
        | xargs -r -I{} ps -o pid=,etimes=,pgid= -p {} 2>/dev/null)
}
_clean_orphans

# flock 互斥：同一时刻只允许 1 个 inventory 进程在跑
# 注意：不要在 exec 9>... 后面加 2>/dev/null（那会把整个脚本的 stderr 都重定向掉）
{ exec 9>"${_LOCK_FILE}"; } 2>/dev/null || true
if command -v flock >/dev/null 2>&1; then
    if ! flock -n 9; then
        echo "[ERROR] 已有另一个 ${SCRIPT_NAME} 进程在运行（锁文件 ${_LOCK_FILE}）" >&2
        echo "        如确认无残留进程，可执行: rm -f ${_LOCK_FILE}" >&2
        echo "        或强制清理: pkill -9 -f tdsql_inventory.sh && rm -f ${_LOCK_FILE}" >&2
        exit 4
    fi
fi
trap 'rm -f "${_LOCK_FILE}" 2>/dev/null; exit' EXIT INT TERM

# ============================================================================
# 内置默认值（TDSQL 全环境通用）
# ============================================================================

DEFAULT_ZK_SERVER="127.0.0.1:2118"
DEFAULT_ZK_AUTH_USER="tdsqlsys_zk"
DEFAULT_ZK_AUTH_PASSWORD="gK#7S2sAnogZWopa3"
DEFAULT_ZK_ROOT="/tdsqlzk"
DEFAULT_ZKCLI_PATH="/data/application/zookeeper/bin/zkCli.sh"

# ============================================================================
# 参数解析
# ============================================================================

OUTPUT_FILE=""
STATUS_FILTER="0"          # 默认只输出 status=0 (运营中)；可逗号分隔多个；填 "all" 表示不过滤
WITH_STATUS=0              # 是否在 CSV 末尾追加 status_code,status_text 两列
PROXY_MODE="random"        # random / first
DEFAULT_DATABASE="ALL"
ENV_CONF=""
QUIET=0
ZK_SERVER_OVERRIDE=""
ZK_AUTH_OVERRIDE=""
ZK_ROOT_OVERRIDE=""
ZKCLI_OVERRIDE=""

show_help() {
    cat <<EOF
${SCRIPT_NAME} v${VERSION} — TDSQL 实例清单自动发现工具

用法: ${SCRIPT_NAME} [选项]

选项:
  -o, --output FILE          输出到指定文件（默认输出到 stdout）
      --status-filter LIST   只输出指定状态码的实例（默认: 0=运营中）
                              逗号分隔多个，如 "0,1"；填 "all" 不过滤
      --with-status          CSV 末尾追加 status_code,status_text 两列
      --proxy-mode MODE      proxy 选择策略: random(默认) / first
      --default-database DB  database 列默认值 (默认 ALL)
  -e, --env-conf FILE        指定 tdsql_env.conf 文件路径
      --zk-server HOST:PORT  覆盖 ZK 服务地址
      --zk-auth USER:PASS    覆盖 ZK 鉴权
      --zk-root PATH         覆盖 ZK 根路径 (默认 /tdsqlzk)
      --zkcli PATH           覆盖 zkCli.sh 路径
  -q, --quiet                静默模式，只输出 CSV
  -h, --help                 显示帮助
  -V, --version              显示版本号

环境变量 (优先级高于 tdsql_env.conf, 低于命令行参数):
  ZK_SERVER, ZK_AUTH_USER, ZK_AUTH_PASSWORD, ZK_ROOT, ZKCLI_PATH

输出格式:
  默认 6 列 (与 db_config.conf 新格式兼容):
    service_name,host,port,user,password,database
  --with-status 时 8 列:
    service_name,host,port,user,password,database,status_code,status_text

实例运行状态码 (来自 setrun.status):
   0 运营中     1 已隔离     2 未初始化   -1 删除中
   100 垂直扩容中  -100 扩容失败
   101 回档中      -101 回档失败
   102 水平扩容    -102 水平扩容失败
   -103 授权失败

示例:
  ${SCRIPT_NAME}                                    # 输出运营中实例 CSV
  ${SCRIPT_NAME} -o /tmp/db.conf                    # 写入文件
  ${SCRIPT_NAME} --status-filter all --with-status  # 全量并附加状态列
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--output)            OUTPUT_FILE="$2"; shift 2 ;;
        --status-filter)        STATUS_FILTER="$2"; shift 2 ;;
        --with-status)          WITH_STATUS=1; shift ;;
        --proxy-mode)           PROXY_MODE="$2"; shift 2 ;;
        --default-database)     DEFAULT_DATABASE="$2"; shift 2 ;;
        -e|--env-conf)          ENV_CONF="$2"; shift 2 ;;
        --zk-server)            ZK_SERVER_OVERRIDE="$2"; shift 2 ;;
        --zk-auth)              ZK_AUTH_OVERRIDE="$2"; shift 2 ;;
        --zk-root)              ZK_ROOT_OVERRIDE="$2"; shift 2 ;;
        --zkcli)                ZKCLI_OVERRIDE="$2"; shift 2 ;;
        -q|--quiet)             QUIET=1; shift ;;
        -h|--help)              show_help; exit 0 ;;
        -V|--version)           echo "${SCRIPT_NAME} v${VERSION}"; exit 0 ;;
        *)
            echo "[ERROR] 未知参数: $1" >&2
            echo "请使用 -h 查看帮助" >&2
            exit 2
            ;;
    esac
done

# ============================================================================
# 日志（输出到 stderr，避免污染 CSV）
# ============================================================================

log() {
    [ "${QUIET}" -eq 1 ] && return 0
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2
}

err() {
    echo "[ERROR] $*" >&2
}

# ============================================================================
# 1. 加载 tdsql_env.conf 中的 [zookeeper] 段（如果存在）
# ============================================================================

# 尝试加载 tdsql_env_loader.sh（用 tdsql_parse_section）
TOOLKIT_ROOT="$(cd "${SCRIPT_DIR}" && pwd)"
if [ -f "${TOOLKIT_ROOT}/tdsql_env_loader.sh" ]; then
    # shellcheck source=/dev/null
    source "${TOOLKIT_ROOT}/tdsql_env_loader.sh"
fi

# 自动定位 tdsql_env.conf
if [ -z "${ENV_CONF}" ] && declare -F tdsql_find_env_conf >/dev/null; then
    ENV_CONF=$(tdsql_find_env_conf "${SCRIPT_DIR}")
fi

# 从配置文件读取 [zookeeper] 段
ZK_FROM_CONF_SERVER=""
ZK_FROM_CONF_AUTH_USER=""
ZK_FROM_CONF_AUTH_PASSWORD=""
ZK_FROM_CONF_ROOT=""
ZK_FROM_CONF_ZKCLI=""

if [ -n "${ENV_CONF}" ] && [ -f "${ENV_CONF}" ] && declare -F tdsql_parse_section >/dev/null; then
    _zk_section=$(tdsql_parse_section "zookeeper" < "${ENV_CONF}" 2>/dev/null || true)
    if [ -n "${_zk_section}" ]; then
        while IFS= read -r _line; do
            [ -z "${_line}" ] && continue
            _key="${_line%%=*}"
            _val="${_line#*=}"
            case "${_key}" in
                server)         ZK_FROM_CONF_SERVER="${_val}" ;;
                auth_user)      ZK_FROM_CONF_AUTH_USER="${_val}" ;;
                auth_password)  ZK_FROM_CONF_AUTH_PASSWORD="${_val}" ;;
                root)           ZK_FROM_CONF_ROOT="${_val}" ;;
                zkcli)          ZK_FROM_CONF_ZKCLI="${_val}" ;;
            esac
        done <<EOF
${_zk_section}
EOF
    fi
fi

# ============================================================================
# 2. 三级优先级合并: 命令行 > 环境变量 > tdsql_env.conf > 内置默认值
# ============================================================================

ZK_SERVER="${ZK_SERVER_OVERRIDE:-${ZK_SERVER:-${ZK_FROM_CONF_SERVER:-${DEFAULT_ZK_SERVER}}}}"

if [ -n "${ZK_AUTH_OVERRIDE}" ]; then
    ZK_AUTH_USER="${ZK_AUTH_OVERRIDE%%:*}"
    ZK_AUTH_PASSWORD="${ZK_AUTH_OVERRIDE#*:}"
else
    ZK_AUTH_USER="${ZK_AUTH_USER:-${ZK_FROM_CONF_AUTH_USER:-${DEFAULT_ZK_AUTH_USER}}}"
    ZK_AUTH_PASSWORD="${ZK_AUTH_PASSWORD:-${ZK_FROM_CONF_AUTH_PASSWORD:-${DEFAULT_ZK_AUTH_PASSWORD}}}"
fi

ZK_ROOT="${ZK_ROOT_OVERRIDE:-${ZK_ROOT:-${ZK_FROM_CONF_ROOT:-${DEFAULT_ZK_ROOT}}}}"
ZKCLI_PATH="${ZKCLI_OVERRIDE:-${ZKCLI_PATH:-${ZK_FROM_CONF_ZKCLI:-${DEFAULT_ZKCLI_PATH}}}}"

log "ZK 服务地址: ${ZK_SERVER}"
log "ZK 鉴权用户: ${ZK_AUTH_USER}"
log "ZK 根路径:   ${ZK_ROOT}"
log "zkCli 路径:  ${ZKCLI_PATH}"

# ============================================================================
# 3. 检查依赖
# ============================================================================

if [ ! -x "${ZKCLI_PATH}" ]; then
    err "zkCli.sh 不存在或不可执行: ${ZKCLI_PATH}"
    err "请通过 --zkcli 指定路径，或安装 ZooKeeper 客户端"
    exit 3
fi

if ! command -v python3 >/dev/null 2>&1; then
    err "python3 未安装，无法解析 ZK setrun 节点的 JSON 数据"
    exit 3
fi

# ============================================================================
# 4. 调用 zkCli.sh 拉取数据
# ============================================================================

# 通用 ZK 命令执行函数
# 参数: $1 = 多行 ZK 命令（每行一条，addauth 会自动加在前面）
# 内置：超时 60s + 最多 5 次重试（应对 zkCli JVM 冷启动慢/低内存抖动导致超时输出为空）
zk_exec() {
    local cmds="$1"
    local zk_dir
    zk_dir="$(dirname "${ZKCLI_PATH}")"
    local _attempt _output _rc
    local _max_attempts=5
    # 单次超时默认放宽到 180s：JVM 冷启动 + ZK 抖动 + 大量 get 命令堆积时 60s 常常不够。
    # 用户可通过 ZK_EXEC_TIMEOUT 环境变量覆盖。
    local _zk_timeout="${ZK_EXEC_TIMEOUT:-180}"
    # JVM 堆设置：与 zkCli.sh 内置的 CLIENT_JVMFLAGS=-Xmx256m 保持一致以避免重复 -Xmx 造成的解析歧义。
    # 用户可通过 ZKCLI_XMX 环境变量覆盖（低内存机器可调小，如 128m；实例很多时可调大到 512m）
    local _zk_xmx="${ZKCLI_XMX:-256m}"
    local _zk_xms="${ZKCLI_XMS:-64m}"
    for _attempt in $(seq 1 ${_max_attempts}); do
        _output=$(
            cd "${zk_dir}" || exit 1
            # 较小的堆 + 关闭 GC 日志，避免低内存机器被 OOM Killer 干掉（症状: 直接 Killed）
            # timeout 加 --foreground: 保持 zkCli 与父 shell 在同一进程组，避免子进程组
            #   收到 SIGTTIN/SIGTTOU 被内核挂起（STAT=T 卡死症状，交互式终端下必现）
            # </dev/null: 显式脱离控制终端，heredoc 结束后 zkCli.sh 内部 bash 不会再回读 tty
            JVMFLAGS="-Xmx${_zk_xmx} -Xms${_zk_xms} -XX:+ExitOnOutOfMemoryError" \
                timeout --foreground "${_zk_timeout}" ./zkCli.sh -server "${ZK_SERVER}" 2>&1 <<EOF
addauth digest ${ZK_AUTH_USER}:${ZK_AUTH_PASSWORD}
${cmds}
quit
EOF
        )
        _rc=$?
        # 判定是否拿到了 ls 结果（[ 开头的数组行）或 get 结果（{ 开头的 JSON）
        if echo "${_output}" | grep -qE '^\[[^z]|^\{|^"'; then
            # 过滤掉 zkCli 自身的 LOG 干扰行，只回显有效数据
            echo "${_output}" | grep -vE '^(JLine|Welcome|JVM|WATCHER|WatchedEvent|log4j|SLF4J|\[main\]|Connecting|Session|JVMFLAGS|^$)'
            return 0
        fi
        # rc=137 = 被 SIGKILL（典型 OOM Killer）；rc=124 = timeout 超时
        local _hint=""
        case "${_rc}" in
            137) _hint=" (rc=137, 疑似被 OOM Killer 杀死，请减小 ZKCLI_XMX 环境变量, 当前=${_zk_xmx})" ;;
            124) _hint=" (rc=124, 单次 ${_zk_timeout}s 超时)" ;;
            *)   _hint=" (rc=${_rc})" ;;
        esac
        # 仅 addauth/welcome/info 之类，没拿到任何节点回显 → 视为冷启动失败/超时，重试
        if [ "${_attempt}" -lt ${_max_attempts} ]; then
            echo "[WARN] zkCli 第 ${_attempt}/${_max_attempts} 次未取到节点数据${_hint}, ${_attempt}s 后重试..." >&2
            sleep "${_attempt}"
        fi
    done
    # 最终输出（即使为空）让上层用统一的方式判定
    echo "${_output}"
    echo "[ERROR] zkCli 经过 ${_max_attempts} 次重试仍未取到数据 (单次 ${_zk_timeout}s, JVM=${_zk_xmx})" >&2
    echo "[ERROR] 排查: 1) free -m 看可用内存; 2) dmesg -T | grep -i killed; 3) 手动测 /data/application/zookeeper/bin/zkCli.sh -server ${ZK_SERVER}" >&2
    return ${_rc}
}

# ────────────────────────────────────────────────────────────────────────────
# 实例发现策略 (兼容 noshard + groupshard 两类):
#   1) /tdsqlzk/sets/                       -> noshard 实例 (单分片)
#   2) /tdsqlzk/group_xxx/sets/             -> groupshard 实例 (分布式)
#      对分布式实例, 只输出【一行 CSV】(取 group 下第一个 set 作代表), 因为:
#      a) proxy 层 (15002 端口) 是分布式实例的统一入口, 内部路由到所有分片;
#      b) 下游脚本连 proxy 端口即可采到全库全表, 无需逐分片直连;
#      c) tdsqlsys_normal 密码在同一 group 的所有 set 上共用.
# ────────────────────────────────────────────────────────────────────────────

log "正在从 ZK 探测实例分布..."

# 第一步: 列出 /tdsqlzk 根目录下所有 group_ 前缀节点
_root_list_raw=$(zk_exec "ls ${ZK_ROOT}")
_group_ids=$(echo "${_root_list_raw}" \
    | grep -oE '\b(group|gid)_[A-Za-z0-9_]+' \
    | grep -E '^group_' \
    | sort -u)

# 第二步: 列出 /tdsqlzk/sets/ (noshard 实例)
_noshard_list_raw=$(zk_exec "ls ${ZK_ROOT}/sets")
_noshard_set_ids=$(echo "${_noshard_list_raw}" \
    | grep -oE 'set@[A-Za-z0-9_]+' \
    | sed 's/^set@//' \
    | sort -u)

# 第三步: 遍历每个 group, 列出其 sets 节点取第 1 个 set 作代表
_group_repr_set=""   # 每行: "group_id|repr_set_id"
if [ -n "${_group_ids}" ]; then
    _ls_cmds=""
    while IFS= read -r gid; do
        [ -z "${gid}" ] && continue
        _ls_cmds+="ls ${ZK_ROOT}/${gid}/sets"$'\n'
    done <<EOF
${_group_ids}
EOF
    _group_sets_raw=$(zk_exec "${_ls_cmds}")
    # 解析: 把每条 ls 命令的回显 + 下一行结果配对
    _group_repr_set=$(python3 - <<PYINNER
import re, sys
raw = """${_group_sets_raw}"""
lines = raw.splitlines()
i = 0
while i < len(lines):
    # zkCli jline 模式可能把命令行折成两行: "ls /tdsqlzk/g\nroup_xxx/sets"
    # 因此把命令行+续行先拼到一起再匹配
    if "] ls " in lines[i]:
        joined = lines[i]
        j = i + 1
        # 把直到下一个看起来像 "[group_xxx,...]" 或 "[zk:" 的行视为续行
        while j < len(lines) and not lines[j].lstrip().startswith("[") and not lines[j].lstrip().startswith("[zk:"):
            joined += lines[j]
            j += 1
        m = re.search(r"/tdsqlzk/(group_[A-Za-z0-9_]+)/sets", joined)
        if m:
            gid = m.group(1)
            # 找下一行是数组结果 (以 "[" 开头)
            k = j
            while k < len(lines) and not lines[k].lstrip().startswith("["):
                k += 1
            if k < len(lines):
                arr_line = lines[k]
                # 该 group 下可能有多个 set (分布式分片), 取第一个作代表
                set_match = re.search(r"set@([A-Za-z0-9_]+)", arr_line)
                if set_match:
                    print(f"{gid}|{set_match.group(1)}")
        i = j
    else:
        i += 1
PYINNER
)
fi

# 汇总: 形成 (kind, instance_id, parent_path, set_id) 元组列表
# kind: "noshard" 或 "groupshard"
# parent_path: ZK 上 setrun 节点的父路径 (用于 get 命令)
# instance_id: 对外展示的实例标识 (noshard 直接 set_id；groupshard 用 group_id)
_inventory_records=$(mktemp /tmp/tdsql_inv.XXXXXX)
trap 'rm -f "${_inventory_records}"' EXIT

while IFS= read -r sid; do
    [ -z "${sid}" ] && continue
    echo "noshard|${sid}|${ZK_ROOT}/sets/set@${sid}|${sid}" >> "${_inventory_records}"
done <<EOF
${_noshard_set_ids}
EOF

while IFS= read -r entry; do
    [ -z "${entry}" ] && continue
    gid="${entry%|*}"
    repr_sid="${entry#*|}"
    echo "groupshard|${gid}|${ZK_ROOT}/${gid}/sets/set@${repr_sid}|${repr_sid}" >> "${_inventory_records}"
done <<EOF
${_group_repr_set}
EOF

_total_inst=$(wc -l < "${_inventory_records}" | tr -d ' ')
if [ -z "${_total_inst}" ] || [ "${_total_inst}" -eq 0 ]; then
    err "ZK 中未发现任何实例"
    err "请检查 ${ZK_ROOT}/sets 与 ${ZK_ROOT}/group_* 是否存在"
    exit 4
fi
_ns_cnt=$(grep -c '^noshard' "${_inventory_records}" 2>/dev/null || true)
_gs_cnt=$(grep -c '^groupshard' "${_inventory_records}" 2>/dev/null || true)
log "共发现 ${_total_inst} 个实例 (noshard ${_ns_cnt:-0} + groupshard ${_gs_cnt:-0})"

# ============================================================================
# 5. 批量拉取每个实例代表 set 的 setrun 节点
# ============================================================================

_zk_get_cmds=""
while IFS='|' read -r kind iid parent set_id; do
    [ -z "${set_id}" ] && continue
    _zk_get_cmds+="get ${parent}/setrun@${set_id}"$'\n'
done < "${_inventory_records}"

log "正在批量拉取 setrun 节点..."
_setrun_raw=$(zk_exec "${_zk_get_cmds}")

# ============================================================================
# 6. 从监控库拉取实例中文名 (优先级最高)
# ============================================================================
# 来源: m_data_cur 表 f_type=1 AND f_key='instance_name'
#   f_mid = '/tdsqlzk/<instance_id>'   (注意只在顶级有效, 子 set 不算)
#   f_val = 中文名 (可能为空)

_inst_names_file=$(mktemp /tmp/tdsql_inst_names.XXXXXX)
trap 'rm -f "${_inventory_records}" "${_inst_names_file}"' EXIT

# 尝试加载监控库连接信息 (从 [monitor_db] 段)
if [ -n "${ENV_CONF}" ] && declare -F load_monitor_db >/dev/null; then
    if load_monitor_db "${ENV_CONF}" >/dev/null 2>&1; then
        log "正在从监控库拉取实例中文名..."
        mysql -h"${DB_HOST}" -P"${DB_PORT}" -u"${DB_USER}" -p"${DB_PASS}" "${DB_NAME}" \
            -N -s -e "SELECT SUBSTRING_INDEX(SUBSTRING_INDEX(f_mid, '/tdsqlzk/', -1), '/', 1) AS iid, f_val FROM m_data_cur WHERE f_type=1 AND f_key='instance_name' AND f_mid LIKE '/tdsqlzk/%' AND f_mid NOT LIKE '/tdsqlzk/%/%'" \
            2>/dev/null > "${_inst_names_file}" || true
    fi
fi

# ============================================================================
# 6.5 加载 [oss_decrypt] 配置（TDSQL v6+ 密文密码解密回退，可选）
# ============================================================================
# 若集群开启 tdsqlsys_normal_pwd_encrypt="true"，setrun.password 为空，
# 需要通过 OSS 节点的 manual_set 工具解密。此段为可选配置，未配置时下方 Python
# 会自动尝试本机 manual_set；若本机也没有则跳过该实例并给出提示。
if [ -n "${ENV_CONF}" ] && declare -F load_oss_decrypt >/dev/null; then
    load_oss_decrypt "${ENV_CONF}" >/dev/null 2>&1 || true
fi

# 探测本机是否存在 manual_set（本机解密优先级高于远程 SSH）
LOCAL_MANUAL_SET_BIN=""
for _cand in \
    /data/tdsql_run/*/gateway/bin/manual_set \
    /data/application/oss/bin/manual_set \
    /data/application/gateway/bin/manual_set; do
    if [ -x "${_cand}" ]; then
        LOCAL_MANUAL_SET_BIN="${_cand}"
        break
    fi
done
export LOCAL_MANUAL_SET_BIN

# ============================================================================
# 7. 用 Python 解析 ZK 输出（zkCli 输出格式无法用 awk/sed 稳健处理）
# ============================================================================

python3 - <<PYEOF
import json
import os
import random
import re
import subprocess
import sys

# 输入文件路径
INVENTORY_FILE = """${_inventory_records}"""
INST_NAMES_FILE = """${_inst_names_file}"""

# zkCli 的 setrun 批量输出 (多个 JSON 散落其中)
SETRUN_RAW = """${_setrun_raw}"""

# 配置参数
WITH_STATUS = ${WITH_STATUS} == 1
STATUS_FILTER = "${STATUS_FILTER}".strip()
PROXY_MODE = "${PROXY_MODE}".strip()
DEFAULT_DATABASE = """${DEFAULT_DATABASE}""".strip() or "ALL"
OUTPUT_FILE = """${OUTPUT_FILE}""".strip()

# TDSQL v6+ tdsqlsys_normal 密文密码解密回退配置（来自 [oss_decrypt] 段）
LOCAL_MANUAL_SET_BIN = os.environ.get("LOCAL_MANUAL_SET_BIN", "").strip()
OSS_DECRYPT_HOST     = os.environ.get("OSS_DECRYPT_HOST", "").strip()
OSS_DECRYPT_PORT     = os.environ.get("OSS_DECRYPT_PORT", "36000").strip() or "36000"
OSS_DECRYPT_USER     = os.environ.get("OSS_DECRYPT_USER", "root").strip() or "root"
OSS_DECRYPT_PASSWORD = os.environ.get("OSS_DECRYPT_PASSWORD", "")
OSS_DECRYPT_BIN      = os.environ.get("OSS_DECRYPT_BIN", "").strip()
OSS_DECRYPT_SSH_KEY  = os.environ.get("OSS_DECRYPT_SSH_KEY", "").strip()

# ──────────────────────────────────────────────────────────────────────────
# 状态码 -> 中文名 映射 (来源: TDSQL Keeper 代码注释)
STATUS_MAP = {
    "0":    "运营中",
    "1":    "已隔离",
    "2":    "未初始化",
    "-1":   "删除中",
    "100":  "垂直扩容中",
    "-100": "垂直扩容失败",
    "101":  "回档中",
    "-101": "回档失败",
    "102":  "水平扩容中",
    "-102": "水平扩容失败",
    "103":  "建立DCN中",
    "-103": "授权失败",
    "104":  "增加备机中",
    "105":  "删除备机中",
    "106":  "替换备机中",
}

def status_text(code):
    return STATUS_MAP.get(str(code), f"未知({code})")

# ──────────────────────────────────────────────────────────────────────────
# 状态过滤器
if STATUS_FILTER.lower() == "all":
    allowed_status = None
else:
    allowed_status = set(s.strip() for s in STATUS_FILTER.split(",") if s.strip())

# ──────────────────────────────────────────────────────────────────────────
# 解析 zkCli 批量 get 输出: 找到所有顶层 JSON 行
def parse_setrun(raw):
    """返回 dict: { set_id_from_json: parsed_dict }"""
    result = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        sid = (obj.get("set") or obj.get("id") or "").strip()
        if sid:
            result[sid] = obj
    return result

setrun_map = parse_setrun(SETRUN_RAW)
print(f"[INFO] 解析 setrun JSON: {len(setrun_map)} 条", file=sys.stderr)

# ──────────────────────────────────────────────────────────────────────────
# TDSQL v6+ 兼容: tdsqlsys_normal 密文密码解密回退
# 优先级：
#   1) setrun.password 明文（both 模式，绝大多数生产环境默认）
#   2) 本机 manual_set 直接解密（LOCAL_MANUAL_SET_BIN 已由 shell 层探测）
#   3) 远程 SSH 到 [oss_decrypt].host 执行 manual_set（要求安装 sshpass 或配置 SSH 免密）
# 三种都失败时返回空串，调用方记录 WARN 并跳过该实例。
_manual_set_cache = {}   # set_id -> 明文密码，避免重复解密

_MANUAL_SET_PWD_RE = re.compile(
    r"password\s*[:=]\s*['\"]?([^'\"\s]+)['\"]?", re.IGNORECASE)

def _extract_password_from_manual_set(output):
    """从 manual_set 输出里解析明文密码。工具版本可能输出 JSON 或 kv 格式，两种都尝试。"""
    if not output:
        return ""
    # 情况1: 输出内含完整 JSON（末尾往往是 {"...": ..., "password": "xxx", ...}）
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("password"):
                    return str(obj["password"]).strip()
            except json.JSONDecodeError:
                pass
    # 情况2: 输出是 key: value 或 key = value 的行，找 password 那行
    m = _MANUAL_SET_PWD_RE.search(output)
    if m:
        return m.group(1).strip()
    return ""

def _decrypt_local(set_id):
    if not LOCAL_MANUAL_SET_BIN:
        return ""
    try:
        proc = subprocess.run(
            [LOCAL_MANUAL_SET_BIN, "-c", "get_setrun", "-s", set_id],
            capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f"[WARN] 本机 manual_set 调用异常 (set={set_id}): {e}", file=sys.stderr)
        return ""
    if proc.returncode != 0:
        return ""
    return _extract_password_from_manual_set(proc.stdout or proc.stderr or "")

def _decrypt_remote(set_id):
    if not (OSS_DECRYPT_HOST and OSS_DECRYPT_BIN):
        return ""
    remote_cmd = f"{OSS_DECRYPT_BIN} -c get_setrun -s {set_id}"
    ssh_base = [
        "ssh", "-p", str(OSS_DECRYPT_PORT),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes" if not OSS_DECRYPT_PASSWORD else "BatchMode=no",
    ]
    if OSS_DECRYPT_SSH_KEY:
        ssh_base.extend(["-i", OSS_DECRYPT_SSH_KEY])
    ssh_base.append(f"{OSS_DECRYPT_USER}@{OSS_DECRYPT_HOST}")
    ssh_base.append(remote_cmd)
    # 有 SSH 密码时优先用 sshpass；未安装 sshpass 则退回免密
    if OSS_DECRYPT_PASSWORD and not OSS_DECRYPT_SSH_KEY:
        try:
            subprocess.run(["sshpass", "-V"],
                           capture_output=True, timeout=3)
            cmd = ["sshpass", "-p", OSS_DECRYPT_PASSWORD] + ssh_base
        except Exception:
            print("[WARN] 未安装 sshpass 且未配置 ssh_key，无法完成远程解密", file=sys.stderr)
            return ""
    else:
        cmd = ssh_base
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    except Exception as e:
        print(f"[WARN] 远程 manual_set 调用异常 (set={set_id}): {e}", file=sys.stderr)
        return ""
    if proc.returncode != 0:
        _err = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        print(f"[WARN] 远程 manual_set 失败 (set={set_id}, rc={proc.returncode}): {_err[0]}",
              file=sys.stderr)
        return ""
    return _extract_password_from_manual_set(proc.stdout or "")

def resolve_password(setrun, set_id, instance_id):
    """按 明文 → 本机 manual_set → 远程 manual_set 顺序解析 tdsqlsys_normal 密码。"""
    plain = (setrun.get("password") or "").strip()
    if plain:
        return plain, "plain"

    action = (setrun.get("password_encrypt_action") or "").strip().lower()
    encrypted = (setrun.get("password_encrypted") or "").strip()
    if not encrypted and action != "cipher":
        # 明文空且没有 encrypted 字段（老版本），无法处理
        return "", "missing"

    if set_id in _manual_set_cache:
        return _manual_set_cache[set_id], "cache"

    # 2) 本机 manual_set
    pwd = _decrypt_local(set_id)
    if pwd:
        _manual_set_cache[set_id] = pwd
        return pwd, "local"

    # 3) 远程 manual_set
    pwd = _decrypt_remote(set_id)
    if pwd:
        _manual_set_cache[set_id] = pwd
        return pwd, "remote"

    return "", "cipher_fail"

# ──────────────────────────────────────────────────────────────────────────
# 从监控库读中文名: instance_id -> name
inst_name_map = {}
if INST_NAMES_FILE and os.path.exists(INST_NAMES_FILE):
    with open(INST_NAMES_FILE, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n").rstrip("\r")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                iid = parts[0].strip()
                name = parts[1].strip()
                if iid and name:
                    inst_name_map[iid] = name

# ──────────────────────────────────────────────────────────────────────────
# 读 inventory_records (kind|instance_id|parent|set_id) 并构造 CSV 行
rows = []
skipped_status = 0
skipped_no_proxy = 0
skipped_no_setrun = 0

with open(INVENTORY_FILE, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 4:
            continue
        kind, instance_id, _parent, set_id = parts
        setrun = setrun_map.get(set_id)
        if not setrun:
            skipped_no_setrun += 1
            print(f"[WARN] 实例 {instance_id} 缺失 setrun 节点 (set_id={set_id})", file=sys.stderr)
            continue

        # 状态过滤
        status_code = setrun.get("status", 0)
        if allowed_status is not None and str(status_code) not in allowed_status:
            skipped_status += 1
            continue

        # service_name: 监控库中文名优先, 回退 instance_id
        # 对分布式实例, instance_id 是 group_id, 中文名以 group_id 反查
        service_name = inst_name_map.get(instance_id) or instance_id

        # user / password
        user = (setrun.get("user") or "").strip()
        if not user:
            print(f"[WARN] 实例 {instance_id} 缺少 user, 跳过", file=sys.stderr)
            continue
        password, pwd_source = resolve_password(setrun, set_id, instance_id)
        if not password:
            enc_action = (setrun.get("password_encrypt_action") or "").strip() or "unknown"
            print(f"[WARN] 实例 {instance_id} 无法获取 tdsqlsys_normal 密码 "
                  f"(set={set_id}, encrypt_action={enc_action}, 尝试来源={pwd_source})，跳过。"
                  f"如集群已开启密文模式，请在 tdsql_env.conf 配置 [oss_decrypt] 段。",
                  file=sys.stderr)
            continue
        if pwd_source in ("local", "remote"):
            print(f"[INFO] 实例 {instance_id} 通过 {pwd_source} manual_set 解密密码成功",
                  file=sys.stderr)

        # proxy 列表
        proxies = setrun.get("proxy", []) or []
        proxy_names = [p.get("name", "") for p in proxies if isinstance(p, dict) and p.get("name")]
        proxy_names = [n for n in proxy_names if "_" in n]
        if not proxy_names:
            skipped_no_proxy += 1
            print(f"[WARN] 实例 {instance_id} 无可用 proxy, 跳过", file=sys.stderr)
            continue

        if PROXY_MODE == "first":
            chosen = proxy_names[0]
        else:
            chosen = random.choice(proxy_names)

        host, _, port = chosen.rpartition("_")

        if WITH_STATUS:
            row = [service_name, host, port, user, password, DEFAULT_DATABASE,
                   str(status_code), status_text(status_code)]
        else:
            row = [service_name, host, port, user, password, DEFAULT_DATABASE]
        rows.append(row)

if skipped_status:
    print(f"[INFO] 状态过滤跳过 {skipped_status} 个实例", file=sys.stderr)
if skipped_no_setrun:
    print(f"[INFO] 缺少 setrun 跳过 {skipped_no_setrun} 个实例", file=sys.stderr)
if skipped_no_proxy:
    print(f"[INFO] 缺少 proxy 跳过 {skipped_no_proxy} 个实例", file=sys.stderr)
print(f"[INFO] 输出 {len(rows)} 个实例", file=sys.stderr)

# ──────────────────────────────────────────────────────────────────────────
# 输出
def csv_escape(field):
    if any(c in field for c in [",", '"', "\n", "\r"]):
        return '"' + field.replace('"', '""') + '"'
    return field

def emit_rows(fp):
    if WITH_STATUS:
        fp.write("# service_name,host,port,user,password,database,status_code,status_text\n")
    else:
        fp.write("# service_name,host,port,user,password,database\n")
    fp.write("# 自动生成于: " + os.popen("date '+%Y-%m-%d %H:%M:%S'").read())
    fp.write("# 来源: tdsql_inventory.sh (ZK 自动发现)\n")
    fp.write("\n")
    for row in rows:
        fp.write(",".join(csv_escape(c) for c in row) + "\n")

if OUTPUT_FILE:
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        emit_rows(f)
    print(f"[INFO] 已写入: {OUTPUT_FILE}", file=sys.stderr)
else:
    emit_rows(sys.stdout)

PYEOF

_pyrc=$?
if [ "${_pyrc}" -ne 0 ]; then
    err "Python 解析失败 (exit=${_pyrc})"
    exit 5
fi

exit 0