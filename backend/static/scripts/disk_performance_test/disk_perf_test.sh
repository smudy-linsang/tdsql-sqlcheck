#!/bin/bash
################################################################################
# disk_perf_test.sh — TDSQL 批量磁盘性能测试工具
#
# 功能:
#   批量对 TDSQL 所有节点服务器执行磁盘性能测试，包括:
#   1. dd 顺序写入测试 (bs=16K)
#   2. fio 随机读测试 (randread, bs=16k, iodepth=32, numjobs=8)
#   3. fio 随机写测试 (randwrite, bs=16k, iodepth=32, numjobs=8)
#
# 测试路径:
#   /data1 — 数据盘
#   /data  — 安装目录盘
#
# 用法:
#   ./disk_perf_test.sh [选项]
#
# 选项:
#   -c <配置文件>        指定 tdsql_hosts 文件 (默认: tdsql_hosts)
#   -p, --password <密码> SSH 密码 (可选，已配置免密时无需指定)
#   --port <端口>        SSH 端口 (默认: 36000，仅端口非默认时需指定)
#   --paths <路径列表>   指定测试路径，逗号分隔 (默认: /data,/data1)
#   -s <fio大小>         fio 测试文件大小 (默认: 10G)
#   -r <fio时长>         fio 运行时长秒数 (默认: 120)
#   -d <dd count>        dd 测试 count 值 (默认: 65536)
#   -t <测试类型>        测试类型: all|dd|fio_read|fio_write (默认: all)
#   -o <输出目录>        结果输出目录 (默认: results)
#   -j <并发数>          同时测试的主机数 (默认: 0，即全部并行)
#   --skip-cleanup       测试后不清理测试文件
#   -h                   显示帮助
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
HOSTS_CONF="${SCRIPT_DIR}/tdsql_hosts"
TEST_PATHS="/data,/data1"
FIO_SIZE="10G"
FIO_RUNTIME="120"
DD_COUNT="65536"
TEST_TYPE="all"
OUTPUT_DIR="${SCRIPT_DIR}/results"
PARALLEL_JOBS=0
SKIP_CLEANUP=0
SSH_PASSWORD=""
SSH_PORT=""
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
RESULT_DIR=""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# ============================================================================
# 工具函数
# ============================================================================
log_info()  { echo -e "${GREEN}[INFO]${NC}  $(date '+%H:%M:%S') $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date '+%H:%M:%S') $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*" >&2; }
log_step()  { echo -e "${CYAN}[STEP]${NC}  $(date '+%H:%M:%S') $*"; }

print_banner() {
    echo -e "${BLUE}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║          TDSQL 磁盘性能批量测试工具 v1.0                   ║"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║  测试项目: dd顺序写 / fio随机读 / fio随机写               ║"
    echo "║  测试路径: /data (安装盘) / /data1 (数据盘)                ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

usage() {
    cat <<EOF
用法: $0 [选项]

选项:
  -c <配置文件>        指定 tdsql_hosts 文件 (默认: tdsql_hosts)
  -p, --password <密码> SSH 密码 (可选，已配置免密时无需指定)
  --port <端口>        SSH 端口 (默认: 36000，仅端口非默认时需指定)
  --paths <路径列表>   指定测试路径，逗号分隔 (默认: /data,/data1)
  -s <fio大小>         fio 测试文件大小 (默认: 10G)
  -r <fio时长>         fio 运行时长秒数 (默认: 120)
  -d <dd count>        dd 测试 count 值 (默认: 65536)
  -t <测试类型>        测试类型: all|dd|fio_read|fio_write (默认: all)
  -o <输出目录>        结果输出目录 (默认: results)
  -j <并发数>          同时测试的主机数 (默认: 0，即全部并行; 1=串行)
  --skip-cleanup       测试后不清理测试文件
  -h                   显示帮助

配置文件格式 (tdsql_hosts — Ansible inventory 格式):
  [tdsql_allmacforcheck]
  tdsql_mac1 ansible_ssh_host=10.0.1.10
  tdsql_mac2 ansible_ssh_host=10.0.1.11

示例:
  $0                                        # 免密模式运行（推荐，默认端口36000）
  $0 -s 1G -r 30                            # 快速测试（缩小fio文件和时长）
  $0 --paths /data                          # 只测试 /data 盘
  $0 -t dd                                  # 只执行 dd 测试
  $0 -j 1                                   # 串行测试（一台一台执行）
  $0 -j 3                                   # 最多3台主机并行测试
  $0 --port 22                              # SSH端口非36000时指定
  $0 -p 'MySSHPass'                         # 未配置免密时用密码模式
EOF
    exit 0
}

# ============================================================================
# 参数解析
# ============================================================================
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -c) HOSTS_CONF="$2"; shift 2 ;;
            -p|--password) SSH_PASSWORD="$2"; shift 2 ;;
            --port) SSH_PORT="$2"; shift 2 ;;
            --paths) TEST_PATHS="$2"; shift 2 ;;
            -s) FIO_SIZE="$2"; shift 2 ;;
            -r) FIO_RUNTIME="$2"; shift 2 ;;
            -d) DD_COUNT="$2"; shift 2 ;;
            -t) TEST_TYPE="$2"; shift 2 ;;
            -o) OUTPUT_DIR="$2"; shift 2 ;;
            -j) PARALLEL_JOBS="$2"; shift 2 ;;
            --skip-cleanup) SKIP_CLEANUP=1; shift ;;
            -h|--help) usage ;;
            *) log_error "未知参数: $1"; usage ;;
        esac
    done

    # SSH 密码为可选参数，未指定时使用免密模式
    if [ -z "$SSH_PASSWORD" ]; then
        log_info "未指定 SSH 密码，将使用免密（SSH Key）模式"
    fi
}

# ============================================================================
# 配置文件解析
# ============================================================================

# SSH 全局认证配置
SSH_DEFAULT_PORT="36000"
SSH_DEFAULT_USER="root"
SSH_DEFAULT_AUTH=""

# 初始化 SSH 全局认证配置（使用命令行传入的密码和端口）
init_ssh_global_config() {
    if [ -n "$SSH_PASSWORD" ]; then
        SSH_DEFAULT_AUTH="pass:${SSH_PASSWORD}"
    else
        # 免密模式：使用 SSH Key 认证
        SSH_DEFAULT_AUTH="key_auth"
    fi
    # 如果命令行指定了端口，覆盖默认值
    if [ -n "$SSH_PORT" ]; then
        SSH_DEFAULT_PORT="$SSH_PORT"
    fi
}

# 解析 Ansible inventory 格式的 tdsql_hosts 文件
# 从 [tdsql_allmacforcheck] 段提取所有唯一 IP 地址
# 同时扫描所有角色 section，为每个 IP 标注所属角色
# 输出格式: 每行一个 "别名,IP,端口,用户,认证方式,角色1|角色2|..."
parse_ansible_inventory() {
    local inv_file="$1"
    local seen_ips=""
    local current_section=""
    local host_index=0
    
    if [ ! -f "$inv_file" ]; then
        log_error "tdsql_hosts 文件不存在: $inv_file"
        return 1
    fi
    
    # 初始化 SSH 配置
    init_ssh_global_config
    
    # ---- 第一遍：扫描所有角色 section，建立 IP → 角色列表映射 ----
    # 关注的角色 section（排除 tdsql_allmacforcheck 和 tdsql_ansible_test）
    declare -A ip_roles_map
    local scan_section=""
    
    while IFS= read -r line || [[ -n "$line" ]]; do
        line=$(echo "$line" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [[ -z "$line" ]] && continue
        [[ "$line" =~ ^# ]] && continue
        
        if [[ "$line" =~ ^\[(.+)\]$ ]]; then
            scan_section="${BASH_REMATCH[1]}"
            continue
        fi
        
        # 跳过 allmacforcheck 和 ansible_test，这些不是角色
        [[ "$scan_section" == "tdsql_allmacforcheck" ]] && continue
        [[ "$scan_section" == "tdsql_ansible_test" ]] && continue
        [[ -z "$scan_section" ]] && continue
        
        if [[ "$line" =~ ansible_ssh_host=([0-9.]+) ]]; then
            local scan_ip="${BASH_REMATCH[1]}"
            # 将角色追加到该 IP 的角色列表（用 | 分隔）
            if [ -n "${ip_roles_map[$scan_ip]:-}" ]; then
                # 避免重复添加同一角色
                if [[ "|${ip_roles_map[$scan_ip]}|" != *"|${scan_section}|"* ]]; then
                    ip_roles_map[$scan_ip]="${ip_roles_map[$scan_ip]}|${scan_section}"
                fi
            else
                ip_roles_map[$scan_ip]="${scan_section}"
            fi
        fi
    done < "$inv_file"
    
    # ---- 第二遍：从 [tdsql_allmacforcheck] 段提取唯一主机，附带角色信息 ----
    current_section=""
    while IFS= read -r line || [[ -n "$line" ]]; do
        line=$(echo "$line" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [[ -z "$line" ]] && continue
        [[ "$line" =~ ^# ]] && continue
        
        if [[ "$line" =~ ^\[(.+)\]$ ]]; then
            current_section="${BASH_REMATCH[1]}"
            continue
        fi
        
        # 只从 [tdsql_allmacforcheck] 段提取主机
        [[ "$current_section" != "tdsql_allmacforcheck" ]] && continue
        
        # 提取 ansible_ssh_host=IP
        if [[ "$line" =~ ansible_ssh_host=([0-9.]+) ]]; then
            local ip="${BASH_REMATCH[1]}"
            local alias_name=$(echo "$line" | awk '{print $1}')
            
            # IP 去重
            if echo "$seen_ips" | grep -qw "$ip"; then
                continue
            fi
            seen_ips="${seen_ips} ${ip}"
            ((host_index++)) || true
            
            # 获取该 IP 的角色列表
            local roles="${ip_roles_map[$ip]:-unknown}"
            
            echo "${alias_name},${ip},${SSH_DEFAULT_PORT},${SSH_DEFAULT_USER},${SSH_DEFAULT_AUTH},${roles}"
        fi
    done < "$inv_file"
}

# 获取主机列表
# 优先从 tdsql_hosts (Ansible inventory 格式) 读取
get_hosts_list() {
    local hosts_data=""
    
    # 从 tdsql_hosts 文件加载（Ansible inventory 格式）
    if [ -f "$HOSTS_CONF" ]; then
        hosts_data=$(parse_ansible_inventory "$HOSTS_CONF")
    fi
    
    if [ -z "$hosts_data" ]; then
        log_error "未找到任何主机配置，请检查 tdsql_hosts 文件"
        log_error "文件路径: $HOSTS_CONF"
        return 1
    fi
    
    echo "$hosts_data"
}

# ============================================================================
# SSH 远程执行
# ============================================================================
ssh_exec() {
    local host="$1"
    local port="$2"
    local user="$3"
    local auth="$4"
    local cmd="$5"
    local timeout="${6:-300}"
    
    local auth_type="${auth%%:*}"
    local auth_value="${auth#*:}"
    
    if [ "$auth_type" = "pass" ]; then
        sshpass -p "$auth_value" ssh -n -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
            -o ServerAliveInterval=30 -o ServerAliveCountMax=10 \
            -p "$port" "${user}@${host}" "$cmd" 2>&1
    elif [ "$auth_type" = "key" ]; then
        ssh -n -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
            -o ServerAliveInterval=30 -o ServerAliveCountMax=10 \
            -i "$auth_value" -p "$port" "${user}@${host}" "$cmd" 2>&1
    elif [ "$auth_type" = "key_auth" ] || [ "$auth" = "key_auth" ]; then
        # 免密模式：使用默认 SSH Key 认证（不指定密钥文件，使用系统默认）
        ssh -n -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
            -o BatchMode=yes \
            -o ServerAliveInterval=30 -o ServerAliveCountMax=10 \
            -p "$port" "${user}@${host}" "$cmd" 2>&1
    else
        log_error "不支持的认证方式: $auth_type"
        return 1
    fi
}

# ============================================================================
# 远程测试命令生成
# ============================================================================

# 生成 dd 测试命令
gen_dd_cmd() {
    local test_path="$1"
    local test_file="${test_path}/dd_testfile_$$"
    cat <<'REMOTE_SCRIPT'
#!/bin/bash
TEST_FILE="__TEST_FILE__"
echo "=== DD Sequential Write Test ==="
echo "test_path=__TEST_PATH__"
echo "command=dd if=/dev/zero of=${TEST_FILE} bs=16K count=__DD_COUNT__ oflag=direct"

# 检查路径是否存在
if [ ! -d "__TEST_PATH__" ]; then
    echo "status=ERROR"
    echo "error=路径不存在: __TEST_PATH__"
    exit 1
fi

# 检查磁盘空间 (至少需要 16GB)
avail_kb=$(df -k "__TEST_PATH__" | tail -1 | awk '{print $4}')
if [ "$avail_kb" -lt 16777216 ] 2>/dev/null; then
    echo "status=ERROR"
    echo "error=磁盘空间不足，需要至少16GB，当前可用: $((avail_kb/1024/1024))GB"
    exit 1
fi

# 获取磁盘信息
disk_info=$(df -h "__TEST_PATH__" | tail -1)
echo "disk_info=${disk_info}"

# 执行 dd 测试
start_time=$(date +%s%N)
dd_output=$(dd if=/dev/zero of="${TEST_FILE}" bs=16K count=__DD_COUNT__ oflag=direct 2>&1)
end_time=$(date +%s%N)

elapsed_ms=$(( (end_time - start_time) / 1000000 ))
echo "elapsed_ms=${elapsed_ms}"

# 解析 dd 输出
bytes_line=$(echo "$dd_output" | grep -E 'bytes|字节' | tail -1)
echo "dd_raw_output=${bytes_line}"

# 提取速度
speed=$(echo "$dd_output" | grep -oE '[0-9.]+ [GMKT]B/s' | tail -1)
if [ -z "$speed" ]; then
    speed=$(echo "$dd_output" | grep -oE '[0-9.]+ [GMKT]?B/秒' | tail -1)
fi
echo "speed=${speed}"
echo "status=OK"

# 清理
rm -f "${TEST_FILE}" 2>/dev/null
REMOTE_SCRIPT
}

# 生成 fio 随机读测试命令
gen_fio_randread_cmd() {
    local test_path="$1"
    local fio_size="$2"
    local fio_runtime="$3"
    local test_file="${test_path}/fio_rand_testfile_$$"
    cat <<'REMOTE_SCRIPT'
#!/bin/bash
TEST_FILE="__TEST_FILE__"
echo "=== FIO Random Read Test ==="
echo "test_path=__TEST_PATH__"
echo "fio_size=__FIO_SIZE__"
echo "fio_runtime=__FIO_RUNTIME__"

if [ ! -d "__TEST_PATH__" ]; then
    echo "status=ERROR"
    echo "error=路径不存在: __TEST_PATH__"
    exit 1
fi

# 检查 fio 是否安装
if ! command -v fio &>/dev/null; then
    echo "status=ERROR"
    echo "error=fio 未安装，请先安装: yum install -y fio"
    exit 1
fi

disk_info=$(df -h "__TEST_PATH__" | tail -1)
echo "disk_info=${disk_info}"

start_time=$(date +%s%N)
fio_output=$(fio -filename="${TEST_FILE}" -direct=1 -iodepth=32 -thread \
    -rw=randread -ioengine=libaio -bs=16k -size=__FIO_SIZE__ -numjobs=8 \
    -runtime=__FIO_RUNTIME__ -group_reporting -name=mytest 2>&1)
end_time=$(date +%s%N)

elapsed_ms=$(( (end_time - start_time) / 1000000 ))
echo "elapsed_ms=${elapsed_ms}"

# 解析 fio 输出 — 读取 IOPS 和带宽
read_bw=$(echo "$fio_output" | grep -E '^\s*read\s*:' | head -1)
if [ -z "$read_bw" ]; then
    read_bw=$(echo "$fio_output" | grep -iE 'READ:' | head -1)
fi
echo "fio_read_summary=${read_bw}"

# 提取 IOPS（只保留数值部分）
iops=$(echo "$fio_output" | grep -oE 'IOPS=[0-9.kKmM]+' | head -1 | sed 's/^IOPS=//')
if [ -z "$iops" ]; then
    iops=$(echo "$fio_output" | grep -oE 'iops\s*=\s*[0-9.kKmM]+' | head -1 | sed 's/^iops\s*=\s*//')
fi
echo "iops=${iops}"

# 提取带宽 - 优先从 Run status group 汇总行提取括号中的 MB/s 值（十进制）
# 格式示例: READ: bw=315MiB/s (330MB/s), ...
bw=$(echo "$read_bw" | sed -n 's/.*(\([0-9.]*[GMKT]*B\/s\)).*/\1/p')
if [ -z "$bw" ]; then
    # 回退：从 fio 输出中提取 BW=xxxMiB/s
    bw=$(echo "$fio_output" | grep -oE 'BW=[0-9.]+[GMKT]iB/s' | head -1 | sed 's/^BW=//')
fi
if [ -z "$bw" ]; then
    bw=$(echo "$fio_output" | grep -oE 'bw=[0-9.]+[GMKT]iB/s' | head -1 | sed 's/^bw=//')
fi
if [ -z "$bw" ]; then
    bw=$(echo "$fio_output" | grep -oE 'BW=[0-9.]+[GMKT]B/s' | head -1 | sed 's/^BW=//')
fi
echo "bandwidth=${bw}"

# 提取延迟
lat_avg=$(echo "$fio_output" | grep -A3 'lat' | grep 'avg' | head -1)
echo "lat_avg=${lat_avg}"

# 提取 clat 百分位
clat_pct=$(echo "$fio_output" | grep -E '99\.(00|50|90|99)th' | head -4)
echo "clat_percentiles=${clat_pct}"

# 提取精确延迟数值 (usec 或 msec)
# clat (usec): min=xx, max=xx, avg=xx, stdev=xx
# 或 clat (msec): min=xx, max=xx, avg=xx, stdev=xx
clat_line=$(echo "$fio_output" | grep -E 'clat.*avg=' | head -1)
echo "clat_detail=${clat_line}"

# 提取延迟单位
lat_unit=$(echo "$clat_line" | grep -oE '\(usec\)|\(msec\)|\(nsec\)' | tr -d '()')
echo "lat_unit=${lat_unit}"

# 提取 avg 延迟
clat_avg_val=$(echo "$clat_line" | grep -oE 'avg=[0-9.]+' | head -1 | cut -d'=' -f2)
echo "clat_avg=${clat_avg_val}"

# 提取 min 延迟
clat_min_val=$(echo "$clat_line" | grep -oE 'min=[0-9.]+' | head -1 | cut -d'=' -f2)
echo "clat_min=${clat_min_val}"

# 提取 max 延迟
clat_max_val=$(echo "$clat_line" | grep -oE 'max=[0-9.]+' | head -1 | cut -d'=' -f2)
echo "clat_max=${clat_max_val}"

# 提取百分位单位（可能与clat单位不同）
pct_unit_line=$(echo "$fio_output" | grep -E 'clat percentiles' | head -1)
pct_unit=$(echo "$pct_unit_line" | grep -oE '\(usec\)|\(msec\)|\(nsec\)' | tr -d '()')
echo "pct_unit=${pct_unit}"

# 提取 P99 延迟
p99_line=$(echo "$fio_output" | grep -E '99\.00th' | head -1)
p99_val=$(echo "$p99_line" | sed -n 's/.*99\.00th=\[ *\([0-9]*\)\].*/\1/p')

# 将百分位值统一转换为与clat相同的单位
if [ "$pct_unit" = "msec" ] && [ "$lat_unit" = "usec" ]; then
    p99_val=$((p99_val * 1000))
elif [ "$pct_unit" = "usec" ] && [ "$lat_unit" = "msec" ]; then
    p99_val=$(echo "scale=2; $p99_val / 1000" | bc)
fi
echo "clat_p99=${p99_val}"

# 提取 P99.9 延迟
p999_line=$(echo "$fio_output" | grep -E '99\.90th' | head -1)
p999_val=$(echo "$p999_line" | sed -n 's/.*99\.90th=\[ *\([0-9]*\)\].*/\1/p')

# 同样转换单位
if [ "$pct_unit" = "msec" ] && [ "$lat_unit" = "usec" ]; then
    p999_val=$((p999_val * 1000))
elif [ "$pct_unit" = "usec" ] && [ "$lat_unit" = "msec" ]; then
    p999_val=$(echo "scale=2; $p999_val / 1000" | bc)
fi
echo "clat_p999=${p999_val}"

echo "status=OK"

rm -f "${TEST_FILE}" 2>/dev/null
REMOTE_SCRIPT
}

# 生成 fio 随机写测试命令
gen_fio_randwrite_cmd() {
    local test_path="$1"
    local fio_size="$2"
    local fio_runtime="$3"
    local test_file="${test_path}/fio_write_testfile_$$"
    cat <<'REMOTE_SCRIPT'
#!/bin/bash
TEST_FILE="__TEST_FILE__"
echo "=== FIO Random Write Test ==="
echo "test_path=__TEST_PATH__"
echo "fio_size=__FIO_SIZE__"
echo "fio_runtime=__FIO_RUNTIME__"

if [ ! -d "__TEST_PATH__" ]; then
    echo "status=ERROR"
    echo "error=路径不存在: __TEST_PATH__"
    exit 1
fi

if ! command -v fio &>/dev/null; then
    echo "status=ERROR"
    echo "error=fio 未安装，请先安装: yum install -y fio"
    exit 1
fi

disk_info=$(df -h "__TEST_PATH__" | tail -1)
echo "disk_info=${disk_info}"

start_time=$(date +%s%N)
fio_output=$(fio -filename="${TEST_FILE}" -direct=1 -iodepth=32 -thread \
    -rw=randwrite -ioengine=libaio -bs=16k -size=__FIO_SIZE__ -numjobs=8 \
    -runtime=__FIO_RUNTIME__ -group_reporting -name=mytest 2>&1)
end_time=$(date +%s%N)

elapsed_ms=$(( (end_time - start_time) / 1000000 ))
echo "elapsed_ms=${elapsed_ms}"

# 解析 fio 输出 — 写入 IOPS 和带宽
write_bw=$(echo "$fio_output" | grep -E '^\s*write\s*:' | head -1)
if [ -z "$write_bw" ]; then
    write_bw=$(echo "$fio_output" | grep -iE 'WRITE:' | head -1)
fi
echo "fio_write_summary=${write_bw}"

iops=$(echo "$fio_output" | grep -oE 'IOPS=[0-9.kKmM]+' | head -1 | sed 's/^IOPS=//')
if [ -z "$iops" ]; then
    iops=$(echo "$fio_output" | grep -oE 'iops\s*=\s*[0-9.kKmM]+' | head -1 | sed 's/^iops\s*=\s*//')
fi
echo "iops=${iops}"

# 提取带宽 - 优先从 Run status group 汇总行提取括号中的 MB/s 值（十进制）
# 格式示例: WRITE: bw=315MiB/s (330MB/s), ...
bw=$(echo "$write_bw" | sed -n 's/.*(\([0-9.]*[GMKT]*B\/s\)).*/\1/p')
if [ -z "$bw" ]; then
    bw=$(echo "$fio_output" | grep -oE 'BW=[0-9.]+[GMKT]iB/s' | head -1 | sed 's/^BW=//')
fi
if [ -z "$bw" ]; then
    bw=$(echo "$fio_output" | grep -oE 'bw=[0-9.]+[GMKT]iB/s' | head -1 | sed 's/^bw=//')
fi
if [ -z "$bw" ]; then
    bw=$(echo "$fio_output" | grep -oE 'BW=[0-9.]+[GMKT]B/s' | head -1 | sed 's/^BW=//')
fi
echo "bandwidth=${bw}"

lat_avg=$(echo "$fio_output" | grep -A3 'lat' | grep 'avg' | head -1)
echo "lat_avg=${lat_avg}"

clat_pct=$(echo "$fio_output" | grep -E '99\.(00|50|90|99)th' | head -4)
echo "clat_percentiles=${clat_pct}"

# 提取精确延迟数值
clat_line=$(echo "$fio_output" | grep -E 'clat.*avg=' | head -1)
echo "clat_detail=${clat_line}"

lat_unit=$(echo "$clat_line" | grep -oE '\(usec\)|\(msec\)|\(nsec\)' | tr -d '()')
echo "lat_unit=${lat_unit}"

clat_avg_val=$(echo "$clat_line" | grep -oE 'avg=[0-9.]+' | head -1 | cut -d'=' -f2)
echo "clat_avg=${clat_avg_val}"

clat_min_val=$(echo "$clat_line" | grep -oE 'min=[0-9.]+' | head -1 | cut -d'=' -f2)
echo "clat_min=${clat_min_val}"

clat_max_val=$(echo "$clat_line" | grep -oE 'max=[0-9.]+' | head -1 | cut -d'=' -f2)
echo "clat_max=${clat_max_val}"

# 提取百分位单位
pct_unit_line=$(echo "$fio_output" | grep -E 'clat percentiles' | head -1)
pct_unit=$(echo "$pct_unit_line" | grep -oE '\(usec\)|\(msec\)|\(nsec\)' | tr -d '()')
echo "pct_unit=${pct_unit}"

p99_line=$(echo "$fio_output" | grep -E '99\.00th' | head -1)
p99_val=$(echo "$p99_line" | sed -n 's/.*99\.00th=\[ *\([0-9]*\)\].*/\1/p')

# 将百分位值统一转换为与clat相同的单位
if [ "$pct_unit" = "msec" ] && [ "$lat_unit" = "usec" ]; then
    p99_val=$((p99_val * 1000))
elif [ "$pct_unit" = "usec" ] && [ "$lat_unit" = "msec" ]; then
    p99_val=$(echo "scale=2; $p99_val / 1000" | bc)
fi
echo "clat_p99=${p99_val}"

p999_line=$(echo "$fio_output" | grep -E '99\.90th' | head -1)
p999_val=$(echo "$p999_line" | sed -n 's/.*99\.90th=\[ *\([0-9]*\)\].*/\1/p')

if [ "$pct_unit" = "msec" ] && [ "$lat_unit" = "usec" ]; then
    p999_val=$((p999_val * 1000))
elif [ "$pct_unit" = "usec" ] && [ "$lat_unit" = "msec" ]; then
    p999_val=$(echo "scale=2; $p999_val / 1000" | bc)
fi
echo "clat_p999=${p999_val}"

echo "status=OK"

rm -f "${TEST_FILE}" 2>/dev/null
REMOTE_SCRIPT
}

# ============================================================================
# 单主机测试执行
# ============================================================================
test_single_host() {
    local alias="$1"
    local ip="$2"
    local port="$3"
    local user="$4"
    local auth="$5"
    local host_result_dir="${RESULT_DIR}/${alias}_${ip}"
    
    mkdir -p "$host_result_dir"
    
    log_step "开始测试主机: ${alias} (${ip}:${port})"
    
    # 先检查连通性
    log_info "  检查 SSH 连通性..."
    local conn_test
    local ssh_exit_code=0
    conn_test=$(ssh_exec "$ip" "$port" "$user" "$auth" "echo OK; hostname; uname -r; cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | head -1") || ssh_exit_code=$?
    
    if [[ "$conn_test" != OK* ]]; then
        log_error "  SSH 连接失败: ${alias} (${ip})"
        echo "status=CONNECT_FAILED" > "${host_result_dir}/connection.txt"
        # 根据退出码生成有意义的错误信息
        local error_msg="${conn_test}"
        if [ -z "$error_msg" ]; then
            case $ssh_exit_code in
                1)   error_msg="SSH 命令执行异常" ;;
                2)   error_msg="SSH 连接冲突或参数错误" ;;
                5)   error_msg="SSH 密码认证失败 (密码错误或目标主机不允许密码登录)" ;;
                6)   error_msg="SSH 主机公钥验证失败" ;;
                255) error_msg="SSH 连接被拒绝 (请检查端口${port}是否正确、网络是否可达)" ;;
                *)   error_msg="SSH 连接失败 (退出码: ${ssh_exit_code})" ;;
            esac
        fi
        echo "error=${error_msg}" >> "${host_result_dir}/connection.txt"
        return 1
    fi
    
    # 保存主机基本信息
    echo "$conn_test" > "${host_result_dir}/host_info.txt"
    
    # 获取系统信息
    log_info "  收集系统信息..."
    local sys_info
    sys_info=$(ssh_exec "$ip" "$port" "$user" "$auth" "
        echo '=== CPU Info ==='
        lscpu 2>/dev/null | grep -E 'Model name|CPU\(s\)|Thread|Core|Socket' || cat /proc/cpuinfo | grep 'model name' | head -1
        echo '=== Memory Info ==='
        free -h 2>/dev/null || free -m
        echo '=== Disk Info ==='
        df -h
        echo '=== Block Devices ==='
        lsblk 2>/dev/null || fdisk -l 2>/dev/null | grep 'Disk /'
    " 2>&1) || true
    echo "$sys_info" > "${host_result_dir}/system_info.txt"
    
    # 收集硬件信息（物理机/虚拟机、厂商、型号、RAID 控制器等）
    log_info "  收集硬件信息..."
    local hw_info
    hw_info=$(ssh_exec "$ip" "$port" "$user" "$auth" '
        # ---- 机器类型判定 ----
        machine_type="unknown"
        virt_type=""
        # 优先使用 systemd-detect-virt（最准确）
        if command -v systemd-detect-virt &>/dev/null; then
            v=$(systemd-detect-virt 2>/dev/null)
            if [ -n "$v" ] && [ "$v" != "none" ]; then
                machine_type="virtual"
                virt_type="$v"
            else
                machine_type="physical"
            fi
        fi
        # 兜底：virt-what
        if [ "$machine_type" = "unknown" ] && command -v virt-what &>/dev/null; then
            v=$(virt-what 2>/dev/null | head -1)
            if [ -n "$v" ]; then
                machine_type="virtual"
                virt_type="$v"
            else
                machine_type="physical"
            fi
        fi
        # 再兜底：dmidecode
        if [ "$machine_type" = "unknown" ] && command -v dmidecode &>/dev/null; then
            prod=$(dmidecode -s system-product-name 2>/dev/null | head -1)
            manuf=$(dmidecode -s system-manufacturer 2>/dev/null | head -1)
            case "${prod}${manuf}" in
                *VMware*|*KVM*|*QEMU*|*VirtualBox*|*Xen*|*Hyper-V*|*Microsoft\ Corporation*|*Bochs*|*Parallels*|*OpenStack*|*Virtual\ Machine*)
                    machine_type="virtual"
                    virt_type="$prod"
                    ;;
                *)
                    machine_type="physical"
                    ;;
            esac
        fi
        # /sys/class/dmi 兜底
        if [ "$machine_type" = "unknown" ] && [ -r /sys/class/dmi/id/product_name ]; then
            prod=$(cat /sys/class/dmi/id/product_name 2>/dev/null)
            case "$prod" in
                *VMware*|*KVM*|*QEMU*|*VirtualBox*|*Xen*|*HVM*|*Virtual*)
                    machine_type="virtual"; virt_type="$prod" ;;
                *) machine_type="physical" ;;
            esac
        fi
        # Virtio 磁盘也倾向于虚拟机
        if [ "$machine_type" = "unknown" ]; then
            if ls /sys/block/ 2>/dev/null | grep -qE "^vd[a-z]"; then
                machine_type="virtual"; virt_type="${virt_type:-kvm/virtio}"
            else
                machine_type="physical"
            fi
        fi
        echo "machine_type=${machine_type}"
        echo "virt_type=${virt_type}"
        
        # ---- 厂商 / 型号 / BIOS ----
        sys_manuf=""; sys_product=""; sys_serial=""; bios_ver=""
        if command -v dmidecode &>/dev/null; then
            sys_manuf=$(dmidecode -s system-manufacturer 2>/dev/null | head -1 | tr -d "\r")
            sys_product=$(dmidecode -s system-product-name 2>/dev/null | head -1 | tr -d "\r")
            sys_serial=$(dmidecode -s system-serial-number 2>/dev/null | head -1 | tr -d "\r")
            bios_ver=$(dmidecode -s bios-version 2>/dev/null | head -1 | tr -d "\r")
        fi
        [ -z "$sys_manuf" ] && [ -r /sys/class/dmi/id/sys_vendor ] && sys_manuf=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null)
        [ -z "$sys_product" ] && [ -r /sys/class/dmi/id/product_name ] && sys_product=$(cat /sys/class/dmi/id/product_name 2>/dev/null)
        echo "sys_manufacturer=${sys_manuf}"
        echo "sys_product=${sys_product}"
        echo "sys_serial=${sys_serial}"
        echo "bios_version=${bios_ver}"
        
        # ---- RAID 控制器（硬件 RAID） ----
        raid_controller=""
        if command -v lspci &>/dev/null; then
            raid_controller=$(lspci 2>/dev/null | grep -iE "raid|mega|perc|smartarr" | head -1 | sed "s/^[^:]*:[[:space:]]*//")
        fi
        echo "raid_controller=${raid_controller}"
        
        # ---- 软 RAID (mdadm) 检查 ----
        mdadm_raid=""
        if [ -f /proc/mdstat ]; then
            mdadm_raid=$(grep -E "^md[0-9]+" /proc/mdstat 2>/dev/null | head -3 | tr "\n" ";" | sed "s/;$//")
        fi
        echo "mdadm_raid=${mdadm_raid}"
        
        # ---- 硬件 RAID 逻辑盘（MegaCli/storcli/hpssacli） ----
        hw_raid_detail=""
        if command -v storcli64 &>/dev/null; then
            hw_raid_detail=$(storcli64 /c0 /vall show 2>/dev/null | grep -E "^[0-9]+/" | head -5 | tr "\n" ";")
        elif command -v storcli &>/dev/null; then
            hw_raid_detail=$(storcli /c0 /vall show 2>/dev/null | grep -E "^[0-9]+/" | head -5 | tr "\n" ";")
        elif command -v MegaCli &>/dev/null; then
            hw_raid_detail=$(MegaCli -LDInfo -Lall -aAll 2>/dev/null | grep -E "RAID Level|Size" | head -6 | tr "\n" ";")
        elif command -v MegaCli64 &>/dev/null; then
            hw_raid_detail=$(MegaCli64 -LDInfo -Lall -aAll 2>/dev/null | grep -E "RAID Level|Size" | head -6 | tr "\n" ";")
        elif command -v hpssacli &>/dev/null; then
            hw_raid_detail=$(hpssacli ctrl all show config 2>/dev/null | grep -E "logicaldrive|RAID" | head -5 | tr "\n" ";")
        elif command -v ssacli &>/dev/null; then
            hw_raid_detail=$(ssacli ctrl all show config 2>/dev/null | grep -E "logicaldrive|RAID" | head -5 | tr "\n" ";")
        fi
        echo "hw_raid_detail=${hw_raid_detail}"
        
        # ---- 全部物理磁盘列表 ----
        echo "=== All Disks ==="
        if command -v lsblk &>/dev/null; then
            # -d 只显示顶层块设备（非分区）
            lsblk -dno NAME,SIZE,ROTA,MODEL,TRAN 2>/dev/null
        fi
    ' 2>&1) || true
    echo "$hw_info" > "${host_result_dir}/hardware_info.txt"
    
    # 遍历测试路径
    IFS=',' read -ra paths <<< "$TEST_PATHS"
    for test_path in "${paths[@]}"; do
        test_path=$(echo "$test_path" | xargs)  # trim
        local path_label=$(echo "$test_path" | tr '/' '_' | sed 's/^_//')
        local path_result_dir="${host_result_dir}/${path_label}"
        mkdir -p "$path_result_dir"
        
        log_info "  测试路径: ${test_path}"
        
        # 检查远程路径是否存在
        local path_check
        path_check=$(ssh_exec "$ip" "$port" "$user" "$auth" "[ -d '${test_path}' ] && echo EXISTS || echo NOTFOUND" 2>&1) || true
        
        if [[ "$path_check" != *"EXISTS"* ]]; then
            log_warn "  路径不存在，跳过: ${test_path}"
            echo "status=PATH_NOT_FOUND" > "${path_result_dir}/dd_result.txt"
            echo "status=PATH_NOT_FOUND" > "${path_result_dir}/fio_randread_result.txt"
            echo "status=PATH_NOT_FOUND" > "${path_result_dir}/fio_randwrite_result.txt"
            continue
        fi
        
        # 收集该路径对应的磁盘详细信息
        local disk_detail
        disk_detail=$(ssh_exec "$ip" "$port" "$user" "$auth" "
            TP='${test_path}'
            # 找到该挂载点对应的设备（如 /dev/vdb 或 /dev/sda1）
            src=\$(df -P \"\$TP\" 2>/dev/null | awk 'NR==2{print \$1}')
            echo \"device=\${src}\"
            mp=\$(df -P \"\$TP\" 2>/dev/null | awk 'NR==2{print \$6}')
            echo \"mountpoint=\${mp}\"
            fs=\$(df -PT \"\$TP\" 2>/dev/null | awk 'NR==2{print \$2}')
            echo \"fstype=\${fs}\"
            # 从设备反推底层磁盘（去掉分区号 / lvm 映射）
            dev_base=\$(basename \"\$src\" 2>/dev/null)
            # 处理 lvm（/dev/mapper/xxx）
            if [[ \"\$src\" == /dev/mapper/* ]] || [[ \"\$src\" == /dev/dm-* ]]; then
                if command -v lsblk &>/dev/null; then
                    parent=\$(lsblk -ndo PKNAME \"\$src\" 2>/dev/null | head -1)
                    [ -n \"\$parent\" ] && dev_base=\"\$parent\"
                fi
            fi
            # 处理分区（sda1 -> sda, nvme0n1p1 -> nvme0n1, vdb1 -> vdb）
            base_disk=\$(echo \"\$dev_base\" | sed -E 's/p?[0-9]+\$//' | sed -E 's/([a-z])[0-9]+\$/\1/')
            [ -z \"\$base_disk\" ] && base_disk=\"\$dev_base\"
            echo \"base_disk=\${base_disk}\"
            
            # 获取该底层磁盘的属性
            if [ -b \"/dev/\$base_disk\" ] && command -v lsblk &>/dev/null; then
                disk_size=\$(lsblk -dno SIZE \"/dev/\$base_disk\" 2>/dev/null | head -1 | xargs)
                rota=\$(lsblk -dno ROTA \"/dev/\$base_disk\" 2>/dev/null | head -1 | xargs)
                tran=\$(lsblk -dno TRAN \"/dev/\$base_disk\" 2>/dev/null | head -1 | xargs)
                model=\$(lsblk -dno MODEL \"/dev/\$base_disk\" 2>/dev/null | head -1 | xargs)
                echo \"disk_size=\${disk_size}\"
                echo \"rota=\${rota}\"
                echo \"tran=\${tran}\"
                echo \"model=\${model}\"
                # 磁盘类型推断
                if [[ \"\$base_disk\" == nvme* ]]; then
                    echo \"disk_type=NVMe SSD\"
                elif [[ \"\$base_disk\" == vd* ]]; then
                    # virtio 虚拟盘（rota 值不可靠，按虚拟盘处理）
                    echo \"disk_type=Cloud Disk (virtio)\"
                elif [[ \"\$base_disk\" == xvd* ]]; then
                    echo \"disk_type=Cloud Disk (xen)\"
                elif [ \"\$rota\" = \"0\" ]; then
                    echo \"disk_type=SSD\"
                elif [ \"\$rota\" = \"1\" ]; then
                    echo \"disk_type=HDD\"
                else
                    echo \"disk_type=Unknown\"
                fi
            fi
            
            # RAID 信息（mdadm）
            if [[ \"\$src\" == /dev/md* ]]; then
                if command -v mdadm &>/dev/null; then
                    raid_info=\$(mdadm --detail \"\$src\" 2>/dev/null | grep -E 'Raid Level|Array Size|Raid Devices' | tr '\n' ';' | sed 's/;\$//')
                    echo \"raid_info=\${raid_info}\"
                fi
            fi
        " 2>&1) || true
        echo "$disk_detail" > "${path_result_dir}/disk_detail.txt"
        
        # DD 顺序写测试
        if [[ "$TEST_TYPE" == "all" || "$TEST_TYPE" == "dd" ]]; then
            log_info "    [1/3] dd 顺序写测试 (bs=16K, count=${DD_COUNT})..."
            local dd_cmd
            dd_cmd=$(gen_dd_cmd "$test_path")
            dd_cmd=$(echo "$dd_cmd" | sed "s|__TEST_FILE__|${test_path}/dd_testfile_$$|g")
            dd_cmd=$(echo "$dd_cmd" | sed "s|__TEST_PATH__|${test_path}|g")
            dd_cmd=$(echo "$dd_cmd" | sed "s|__DD_COUNT__|${DD_COUNT}|g")
            
            local dd_result
            dd_result=$(ssh_exec "$ip" "$port" "$user" "$auth" "$dd_cmd" 600) || true
            echo "$dd_result" > "${path_result_dir}/dd_result.txt"
            
            local dd_speed=$(echo "$dd_result" | grep '^speed=' | cut -d'=' -f2)
            if [ -n "$dd_speed" ]; then
                log_info "    dd 写入速度: ${dd_speed}"
            else
                log_warn "    dd 测试结果解析异常"
            fi
        fi
        
        # FIO 随机读测试
        if [[ "$TEST_TYPE" == "all" || "$TEST_TYPE" == "fio_read" ]]; then
            log_info "    [2/3] fio 随机读测试 (randread, bs=16k, runtime=${FIO_RUNTIME}s)..."
            local fio_read_cmd
            fio_read_cmd=$(gen_fio_randread_cmd "$test_path" "$FIO_SIZE" "$FIO_RUNTIME")
            fio_read_cmd=$(echo "$fio_read_cmd" | sed "s|__TEST_FILE__|${test_path}/fio_rand_testfile_$$|g")
            fio_read_cmd=$(echo "$fio_read_cmd" | sed "s|__TEST_PATH__|${test_path}|g")
            fio_read_cmd=$(echo "$fio_read_cmd" | sed "s|__FIO_SIZE__|${FIO_SIZE}|g")
            fio_read_cmd=$(echo "$fio_read_cmd" | sed "s|__FIO_RUNTIME__|${FIO_RUNTIME}|g")
            
            local fio_read_result
            fio_read_result=$(ssh_exec "$ip" "$port" "$user" "$auth" "$fio_read_cmd" $((FIO_RUNTIME + 120))) || true
            echo "$fio_read_result" > "${path_result_dir}/fio_randread_result.txt"
            
            local fio_read_iops=$(echo "$fio_read_result" | grep '^iops=' | cut -d'=' -f2-)
            local fio_read_bw=$(echo "$fio_read_result" | grep '^bandwidth=' | cut -d'=' -f2-)
            if [ -n "$fio_read_iops" ]; then
                log_info "    fio 随机读 IOPS: ${fio_read_iops}, 速度: ${fio_read_bw}"
            else
                log_warn "    fio 随机读测试结果解析异常"
            fi
        fi
        
        # FIO 随机写测试
        if [[ "$TEST_TYPE" == "all" || "$TEST_TYPE" == "fio_write" ]]; then
            log_info "    [3/3] fio 随机写测试 (randwrite, bs=16k, runtime=${FIO_RUNTIME}s)..."
            local fio_write_cmd
            fio_write_cmd=$(gen_fio_randwrite_cmd "$test_path" "$FIO_SIZE" "$FIO_RUNTIME")
            fio_write_cmd=$(echo "$fio_write_cmd" | sed "s|__TEST_FILE__|${test_path}/fio_write_testfile_$$|g")
            fio_write_cmd=$(echo "$fio_write_cmd" | sed "s|__TEST_PATH__|${test_path}|g")
            fio_write_cmd=$(echo "$fio_write_cmd" | sed "s|__FIO_SIZE__|${FIO_SIZE}|g")
            fio_write_cmd=$(echo "$fio_write_cmd" | sed "s|__FIO_RUNTIME__|${FIO_RUNTIME}|g")
            
            local fio_write_result
            fio_write_result=$(ssh_exec "$ip" "$port" "$user" "$auth" "$fio_write_cmd" $((FIO_RUNTIME + 120))) || true
            echo "$fio_write_result" > "${path_result_dir}/fio_randwrite_result.txt"
            
            local fio_write_iops=$(echo "$fio_write_result" | grep '^iops=' | cut -d'=' -f2-)
            local fio_write_bw=$(echo "$fio_write_result" | grep '^bandwidth=' | cut -d'=' -f2-)
            if [ -n "$fio_write_iops" ]; then
                log_info "    fio 随机写 IOPS: ${fio_write_iops}, 速度: ${fio_write_bw}"
            else
                log_warn "    fio 随机写测试结果解析异常"
            fi
        fi
    done
    
    log_info "主机 ${alias} (${ip}) 测试完成 ✓"
    echo ""
}

# ============================================================================
# 主流程
# ============================================================================
main() {
    parse_args "$@"
    print_banner
    
    # 创建结果目录
    RESULT_DIR="${OUTPUT_DIR}/${TIMESTAMP}"
    mkdir -p "$RESULT_DIR"
    
    # 初始化 SSH 配置（确保端口等参数已设置）
    init_ssh_global_config
    
    log_info "测试时间: $(date '+%Y-%m-%d %H:%M:%S')"
    log_info "结果目录: ${RESULT_DIR}"
    log_info "SSH 端口: ${SSH_DEFAULT_PORT}"
    if [ -n "$SSH_PASSWORD" ]; then
        log_info "认证模式: 密码认证"
    else
        log_info "认证模式: SSH 免密认证（SSH Key）"
    fi
    log_info "测试路径: ${TEST_PATHS}"
    log_info "测试类型: ${TEST_TYPE}"
    log_info "FIO 参数: size=${FIO_SIZE}, runtime=${FIO_RUNTIME}s"
    log_info "DD  参数: bs=16K, count=${DD_COUNT}"
    if [ "$PARALLEL_JOBS" -le 0 ]; then
        log_info "并发数量: 全部并行 (将自动设为主机数)"
    elif [ "$PARALLEL_JOBS" -eq 1 ]; then
        log_info "并发数量: 串行"
    else
        log_info "并发数量: ${PARALLEL_JOBS}"
    fi
    echo ""
    
    # 获取主机列表
    local hosts_data
    hosts_data=$(get_hosts_list) || exit 1
    
    local host_count=0
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        ((host_count++)) || true
    done <<< "$hosts_data"
    
    log_info "共发现 ${host_count} 台主机需要测试"
    
    # 如果 PARALLEL_JOBS=0，自动设为主机数（全部并行）
    if [ "$PARALLEL_JOBS" -le 0 ]; then
        PARALLEL_JOBS=$host_count
    fi
    
    if [ "$PARALLEL_JOBS" -ge 2 ]; then
        log_info "执行模式: 并行 (最大并发: ${PARALLEL_JOBS})"
    else
        log_info "执行模式: 串行"
    fi
    echo ""
    
    # 保存测试参数
    cat > "${RESULT_DIR}/test_params.txt" <<EOF
timestamp=${TIMESTAMP}
test_date=$(date '+%Y-%m-%d %H:%M:%S')
test_paths=${TEST_PATHS}
test_type=${TEST_TYPE}
fio_size=${FIO_SIZE}
fio_runtime=${FIO_RUNTIME}
host_count=${host_count}
parallel_jobs=${PARALLEL_JOBS}
EOF
    
    # 保存主机列表
    echo "$hosts_data" > "${RESULT_DIR}/hosts_list.txt"
    
    # 创建并行日志目录
    local log_dir="${RESULT_DIR}/parallel_logs"
    mkdir -p "$log_dir"
    
    # 执行测试
    local success_count=0
    local fail_count=0
    local test_start=$(date +%s)
    
    if [ "$PARALLEL_JOBS" -le 1 ]; then
        # 串行执行 — 日志直接输出到终端
        while IFS=',' read -r alias ip port user auth roles; do
            [[ -z "$alias" ]] && continue
            alias=$(echo "$alias" | xargs)
            ip=$(echo "$ip" | xargs)
            port=$(echo "$port" | xargs)
            user=$(echo "$user" | xargs)
            auth=$(echo "$auth" | xargs)
            
            if test_single_host "$alias" "$ip" "$port" "$user" "$auth"; then
                ((success_count++)) || true
            else
                ((fail_count++)) || true
            fi
        done <<< "$hosts_data"
    else
        # 并行执行 — 每台主机日志输出到独立文件，避免终端混乱
        local pids=()
        local host_aliases=()
        local host_ips=()
        local host_logs=()
        local running=0
        
        log_info "所有主机开始并行测试..."
        echo ""
        
        while IFS=',' read -r alias ip port user auth roles; do
            [[ -z "$alias" ]] && continue
            alias=$(echo "$alias" | xargs)
            ip=$(echo "$ip" | xargs)
            port=$(echo "$port" | xargs)
            user=$(echo "$user" | xargs)
            auth=$(echo "$auth" | xargs)
            
            # 等待空闲槽位
            while [ $running -ge $PARALLEL_JOBS ]; do
                for i in "${!pids[@]}"; do
                    if ! kill -0 "${pids[$i]}" 2>/dev/null; then
                        if wait "${pids[$i]}" 2>/dev/null; then
                            ((success_count++)) || true
                        else
                            ((fail_count++)) || true
                        fi
                        unset 'pids[i]'
                        ((running--)) || true
                        # 实时提示已完成的主机
                        log_info "  ✓ ${host_aliases[$i]} (${host_ips[$i]}) 测试完成"
                    fi
                done
                sleep 1
            done
            
            local host_log="${log_dir}/${alias}_${ip}.log"
            log_info "  → 启动并行测试: ${alias} (${ip})"
            
            # 后台执行，日志重定向到独立文件
            test_single_host "$alias" "$ip" "$port" "$user" "$auth" > "$host_log" 2>&1 &
            pids+=($!)
            host_aliases+=("$alias")
            host_ips+=("$ip")
            host_logs+=("$host_log")
            ((running++)) || true
        done <<< "$hosts_data"
        
        # 等待所有剩余任务
        log_info "等待所有并行任务完成..."
        for i in "${!pids[@]}"; do
            if wait "${pids[$i]}" 2>/dev/null; then
                ((success_count++)) || true
            else
                ((fail_count++)) || true
            fi
            log_info "  ✓ ${host_aliases[$i]} (${host_ips[$i]}) 测试完成"
        done
        
        # 汇总输出每台主机的测试日志
        echo ""
        echo -e "${BLUE}═══════════════════ 各主机测试详情 ═══════════════════${NC}"
        for log_file in "${host_logs[@]}"; do
            if [ -f "$log_file" ]; then
                echo ""
                cat "$log_file"
            fi
        done
    fi
    
    local test_end=$(date +%s)
    local total_time=$((test_end - test_start))
    
    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
    log_info "测试全部完成!"
    log_info "总耗时: ${total_time} 秒"
    log_info "成功: ${success_count} 台, 失败: ${fail_count} 台"
    log_info "结果目录: ${RESULT_DIR}"
    echo ""
    
    # 保存汇总信息
    cat >> "${RESULT_DIR}/test_params.txt" <<EOF
total_time_seconds=${total_time}
success_count=${success_count}
fail_count=${fail_count}
EOF
    
    # 生成 HTML 报告
    log_step "正在生成 HTML 报告..."
    if [ -f "${SCRIPT_DIR}/generate_report.sh" ]; then
        bash "${SCRIPT_DIR}/generate_report.sh" "$RESULT_DIR"
    else
        log_warn "报告生成脚本不存在: generate_report.sh"
    fi
}

main "$@"