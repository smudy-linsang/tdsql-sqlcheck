#!/bin/bash
################################################################################
# generate_report.sh — TDSQL 磁盘性能测试 HTML 报告生成器
#
# 功能:
#   读取 disk_perf_test.sh 生成的测试结果，生成美观的 HTML 可视化报告
#   包含：导航栏、总览对比、详细结果、性能图表、延迟分析、磁盘标准参考
#
# 用法:
#   ./generate_report.sh <结果目录>
#   ./generate_report.sh results/20260421_200000
#
# 版本: 2.0
################################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULT_DIR="${1:-}"
REPORT_DIR="${SCRIPT_DIR}/reports"

if [ -z "$RESULT_DIR" ]; then
    echo "[ERROR] 请指定结果目录"
    echo "用法: $0 <结果目录>"
    exit 1
fi

if [ ! -d "$RESULT_DIR" ]; then
    echo "[ERROR] 结果目录不存在: $RESULT_DIR"
    exit 1
fi

mkdir -p "$REPORT_DIR"

# 读取测试参数
TIMESTAMP=""
TEST_DATE=""
TEST_PATHS=""
TEST_TYPE=""
FIO_SIZE=""
FIO_RUNTIME=""
HOST_COUNT=""
TOTAL_TIME=""
SUCCESS_COUNT=""
FAIL_COUNT=""

if [ -f "${RESULT_DIR}/test_params.txt" ]; then
    while IFS='=' read -r key value; do
        case "$key" in
            timestamp) TIMESTAMP="$value" ;;
            test_date) TEST_DATE="$value" ;;
            test_paths) TEST_PATHS="$value" ;;
            test_type) TEST_TYPE="$value" ;;
            fio_size) FIO_SIZE="$value" ;;
            fio_runtime) FIO_RUNTIME="$value" ;;
            host_count) HOST_COUNT="$value" ;;
            total_time_seconds) TOTAL_TIME="$value" ;;
            success_count) SUCCESS_COUNT="$value" ;;
            fail_count) FAIL_COUNT="$value" ;;
        esac
    done < "${RESULT_DIR}/test_params.txt"
fi

[ -z "$TIMESTAMP" ] && TIMESTAMP=$(basename "$RESULT_DIR")
[ -z "$TEST_DATE" ] && TEST_DATE=$(date '+%Y-%m-%d %H:%M:%S')

REPORT_FILE="${REPORT_DIR}/disk_perf_report_${TIMESTAMP}.html"

# ============================================================================
# 解析结果数据，生成 JSON（含延迟指标）
# ============================================================================
generate_json_data() {
    local first_host=1
    
    # 从 hosts_list.txt 加载 IP → 角色映射
    declare -A ip_roles_map
    if [ -f "${RESULT_DIR}/hosts_list.txt" ]; then
        while IFS=',' read -r _alias _ip _port _user _auth _roles; do
            [[ -z "$_ip" ]] && continue
            _ip=$(echo "$_ip" | xargs)
            _roles=$(echo "$_roles" | xargs)
            if [ -n "$_roles" ]; then
                ip_roles_map[$_ip]="$_roles"
            fi
        done < "${RESULT_DIR}/hosts_list.txt"
    fi
    
    echo "["
    
    for host_dir in "${RESULT_DIR}"/*/; do
        [ ! -d "$host_dir" ] && continue
        local dirname=$(basename "$host_dir")
        [[ "$dirname" == "." || "$dirname" == ".." ]] && continue
        # 排除非主机目录（如 parallel_logs 等辅助目录）
        [[ "$dirname" == "parallel_logs" ]] && continue
        # 主机目录名格式必须包含 IP（至少含一个点号）
        [[ "$dirname" != *"."* ]] && continue
        
        local alias=$(echo "$dirname" | rev | cut -d'_' -f2- | rev)
        local ip=$(echo "$dirname" | rev | cut -d'_' -f1 | rev)
        
        # 获取该 IP 的角色列表
        local roles="${ip_roles_map[$ip]:-}"
        
        local hostname=""
        local kernel=""
        local os_info=""
        if [ -f "${host_dir}/host_info.txt" ]; then
            hostname=$(sed -n '2p' "${host_dir}/host_info.txt" 2>/dev/null || echo "")
            kernel=$(sed -n '3p' "${host_dir}/host_info.txt" 2>/dev/null || echo "")
            os_info=$(sed -n '4p' "${host_dir}/host_info.txt" 2>/dev/null | sed 's/PRETTY_NAME=//;s/"//g' || echo "")
        fi
        
        local conn_status="OK"
        local conn_error=""
        if [ -f "${host_dir}/connection.txt" ]; then
            conn_status=$(grep '^status=' "${host_dir}/connection.txt" | cut -d'=' -f2)
            conn_error=$(grep '^error=' "${host_dir}/connection.txt" | cut -d'=' -f2- | sed 's/"/\\"/g' || echo "")
        fi
        
        local cpu_info=""
        local mem_info=""
        if [ -f "${host_dir}/system_info.txt" ]; then
            cpu_info=$(grep -E 'Model name' "${host_dir}/system_info.txt" 2>/dev/null | head -1 | sed 's/.*Model name:\s*//' | xargs || echo "")
            mem_info=$(grep -E '^Mem:' "${host_dir}/system_info.txt" 2>/dev/null | head -1 | awk '{print $2}' || echo "")
        fi
        
        # 解析硬件信息
        local machine_type="" virt_type="" sys_manufacturer="" sys_product="" sys_serial=""
        local bios_version="" raid_controller="" mdadm_raid="" hw_raid_detail=""
        if [ -f "${host_dir}/hardware_info.txt" ]; then
            machine_type=$(grep '^machine_type=' "${host_dir}/hardware_info.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
            virt_type=$(grep '^virt_type=' "${host_dir}/hardware_info.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
            sys_manufacturer=$(grep '^sys_manufacturer=' "${host_dir}/hardware_info.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
            sys_product=$(grep '^sys_product=' "${host_dir}/hardware_info.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
            sys_serial=$(grep '^sys_serial=' "${host_dir}/hardware_info.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
            bios_version=$(grep '^bios_version=' "${host_dir}/hardware_info.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
            raid_controller=$(grep '^raid_controller=' "${host_dir}/hardware_info.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
            mdadm_raid=$(grep '^mdadm_raid=' "${host_dir}/hardware_info.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
            hw_raid_detail=$(grep '^hw_raid_detail=' "${host_dir}/hardware_info.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
        fi
        # 全部物理磁盘列表（All Disks 之后的行）
        local all_disks=""
        if [ -f "${host_dir}/hardware_info.txt" ]; then
            all_disks=$(awk '/^=== All Disks ===/{flag=1; next} flag{print}' "${host_dir}/hardware_info.txt" 2>/dev/null | grep -vE '^\s*$' | tr '\n' '|' | sed 's/|$//')
        fi
        
        [ $first_host -eq 0 ] && echo ","
        first_host=0
        
        # 转义函数：清理字符串中的双引号和反斜杠
        _esc() { echo "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
        
        echo "  {"
        echo "    \"alias\": \"${alias}\","
        echo "    \"ip\": \"${ip}\","
        echo "    \"roles\": \"${roles}\","
        echo "    \"hostname\": \"${hostname}\","
        echo "    \"kernel\": \"${kernel}\","
        echo "    \"os\": \"${os_info}\","
        echo "    \"cpu\": \"${cpu_info}\","
        echo "    \"memory\": \"${mem_info}\","
        echo "    \"machine_type\": \"$(_esc "${machine_type}")\","
        echo "    \"virt_type\": \"$(_esc "${virt_type}")\","
        echo "    \"sys_manufacturer\": \"$(_esc "${sys_manufacturer}")\","
        echo "    \"sys_product\": \"$(_esc "${sys_product}")\","
        echo "    \"sys_serial\": \"$(_esc "${sys_serial}")\","
        echo "    \"bios_version\": \"$(_esc "${bios_version}")\","
        echo "    \"raid_controller\": \"$(_esc "${raid_controller}")\","
        echo "    \"mdadm_raid\": \"$(_esc "${mdadm_raid}")\","
        echo "    \"hw_raid_detail\": \"$(_esc "${hw_raid_detail}")\","
        echo "    \"all_disks\": \"$(_esc "${all_disks}")\","
        echo "    \"conn_status\": \"${conn_status}\","
        echo "    \"conn_error\": \"${conn_error}\","
        echo "    \"paths\": ["
        
        local first_path=1
        for path_dir in "${host_dir}"/*/; do
            [ ! -d "$path_dir" ] && continue
            local path_name=$(basename "$path_dir")
            local display_path="/${path_name//_//}"
            
            [ $first_path -eq 0 ] && echo "      ,"
            first_path=0
            
            echo "      {"
            echo "        \"path\": \"${display_path}\","
            echo "        \"path_label\": \"${path_name}\","
            
            # 解析该路径的磁盘详细信息
            local d_device="" d_mount="" d_fstype="" d_base_disk="" d_disk_size=""
            local d_rota="" d_tran="" d_model="" d_disk_type="" d_raid_info=""
            if [ -f "${path_dir}/disk_detail.txt" ]; then
                d_device=$(grep '^device=' "${path_dir}/disk_detail.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
                d_mount=$(grep '^mountpoint=' "${path_dir}/disk_detail.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
                d_fstype=$(grep '^fstype=' "${path_dir}/disk_detail.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
                d_base_disk=$(grep '^base_disk=' "${path_dir}/disk_detail.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
                d_disk_size=$(grep '^disk_size=' "${path_dir}/disk_detail.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
                d_rota=$(grep '^rota=' "${path_dir}/disk_detail.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
                d_tran=$(grep '^tran=' "${path_dir}/disk_detail.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
                d_model=$(grep '^model=' "${path_dir}/disk_detail.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
                d_disk_type=$(grep '^disk_type=' "${path_dir}/disk_detail.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
                d_raid_info=$(grep '^raid_info=' "${path_dir}/disk_detail.txt" 2>/dev/null | head -1 | cut -d'=' -f2- || echo "")
            fi
            echo "        \"disk_detail\": {"
            echo "          \"device\": \"$(echo "$d_device" | sed 's/\\/\\\\/g; s/"/\\"/g')\","
            echo "          \"mountpoint\": \"$(echo "$d_mount" | sed 's/\\/\\\\/g; s/"/\\"/g')\","
            echo "          \"fstype\": \"$(echo "$d_fstype" | sed 's/\\/\\\\/g; s/"/\\"/g')\","
            echo "          \"base_disk\": \"$(echo "$d_base_disk" | sed 's/\\/\\\\/g; s/"/\\"/g')\","
            echo "          \"disk_size\": \"$(echo "$d_disk_size" | sed 's/\\/\\\\/g; s/"/\\"/g')\","
            echo "          \"rota\": \"${d_rota}\","
            echo "          \"tran\": \"$(echo "$d_tran" | sed 's/\\/\\\\/g; s/"/\\"/g')\","
            echo "          \"model\": \"$(echo "$d_model" | sed 's/\\/\\\\/g; s/"/\\"/g')\","
            echo "          \"disk_type\": \"$(echo "$d_disk_type" | sed 's/\\/\\\\/g; s/"/\\"/g')\","
            echo "          \"raid_info\": \"$(echo "$d_raid_info" | sed 's/\\/\\\\/g; s/"/\\"/g')\""
            echo "        },"
            
            # DD 结果
            local dd_status="N/A" dd_speed="" dd_elapsed="" dd_disk_info=""
            if [ -f "${path_dir}/dd_result.txt" ]; then
                dd_status=$(grep '^status=' "${path_dir}/dd_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "N/A")
                dd_speed=$(grep '^speed=' "${path_dir}/dd_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                dd_elapsed=$(grep '^elapsed_ms=' "${path_dir}/dd_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                dd_disk_info=$(grep '^disk_info=' "${path_dir}/dd_result.txt" 2>/dev/null | cut -d'=' -f2- || echo "")
            fi
            
            echo "        \"disk_info\": \"$(echo "$dd_disk_info" | sed 's/"/\\"/g')\","
            echo "        \"dd\": {"
            echo "          \"status\": \"${dd_status}\","
            echo "          \"speed\": \"${dd_speed}\","
            echo "          \"elapsed_ms\": \"${dd_elapsed}\""
            echo "        },"
            
            # FIO 随机读结果（含延迟）
            local fio_read_status="N/A" fio_read_iops="" fio_read_bw="" fio_read_elapsed="" fio_read_summary=""
            local fio_read_lat_unit="" fio_read_clat_avg="" fio_read_clat_min="" fio_read_clat_max=""
            local fio_read_p99="" fio_read_p999=""
            if [ -f "${path_dir}/fio_randread_result.txt" ]; then
                fio_read_status=$(grep '^status=' "${path_dir}/fio_randread_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "N/A")
                fio_read_iops=$(grep '^iops=' "${path_dir}/fio_randread_result.txt" 2>/dev/null | cut -d'=' -f2- || echo "")
                fio_read_bw=$(grep '^bandwidth=' "${path_dir}/fio_randread_result.txt" 2>/dev/null | cut -d'=' -f2- || echo "")
                fio_read_elapsed=$(grep '^elapsed_ms=' "${path_dir}/fio_randread_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                fio_read_summary=$(grep '^fio_read_summary=' "${path_dir}/fio_randread_result.txt" 2>/dev/null | cut -d'=' -f2- || echo "")
                fio_read_lat_unit=$(grep '^lat_unit=' "${path_dir}/fio_randread_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                fio_read_clat_avg=$(grep '^clat_avg=' "${path_dir}/fio_randread_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                fio_read_clat_min=$(grep '^clat_min=' "${path_dir}/fio_randread_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                fio_read_clat_max=$(grep '^clat_max=' "${path_dir}/fio_randread_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                fio_read_p99=$(grep '^clat_p99=' "${path_dir}/fio_randread_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                fio_read_p999=$(grep '^clat_p999=' "${path_dir}/fio_randread_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
            fi
            
            echo "        \"fio_read\": {"
            echo "          \"status\": \"${fio_read_status}\","
            echo "          \"iops\": \"${fio_read_iops}\","
            echo "          \"bandwidth\": \"${fio_read_bw}\","
            echo "          \"elapsed_ms\": \"${fio_read_elapsed}\","
            echo "          \"summary\": \"$(echo "$fio_read_summary" | sed 's/"/\\"/g')\","
            echo "          \"lat_unit\": \"${fio_read_lat_unit}\","
            echo "          \"clat_avg\": \"${fio_read_clat_avg}\","
            echo "          \"clat_min\": \"${fio_read_clat_min}\","
            echo "          \"clat_max\": \"${fio_read_clat_max}\","
            echo "          \"clat_p99\": \"${fio_read_p99}\","
            echo "          \"clat_p999\": \"${fio_read_p999}\""
            echo "        },"
            
            # FIO 随机写结果（含延迟）
            local fio_write_status="N/A" fio_write_iops="" fio_write_bw="" fio_write_elapsed="" fio_write_summary=""
            local fio_write_lat_unit="" fio_write_clat_avg="" fio_write_clat_min="" fio_write_clat_max=""
            local fio_write_p99="" fio_write_p999=""
            if [ -f "${path_dir}/fio_randwrite_result.txt" ]; then
                fio_write_status=$(grep '^status=' "${path_dir}/fio_randwrite_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "N/A")
                fio_write_iops=$(grep '^iops=' "${path_dir}/fio_randwrite_result.txt" 2>/dev/null | cut -d'=' -f2- || echo "")
                fio_write_bw=$(grep '^bandwidth=' "${path_dir}/fio_randwrite_result.txt" 2>/dev/null | cut -d'=' -f2- || echo "")
                fio_write_elapsed=$(grep '^elapsed_ms=' "${path_dir}/fio_randwrite_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                fio_write_summary=$(grep '^fio_write_summary=' "${path_dir}/fio_randwrite_result.txt" 2>/dev/null | cut -d'=' -f2- || echo "")
                fio_write_lat_unit=$(grep '^lat_unit=' "${path_dir}/fio_randwrite_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                fio_write_clat_avg=$(grep '^clat_avg=' "${path_dir}/fio_randwrite_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                fio_write_clat_min=$(grep '^clat_min=' "${path_dir}/fio_randwrite_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                fio_write_clat_max=$(grep '^clat_max=' "${path_dir}/fio_randwrite_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                fio_write_p99=$(grep '^clat_p99=' "${path_dir}/fio_randwrite_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
                fio_write_p999=$(grep '^clat_p999=' "${path_dir}/fio_randwrite_result.txt" 2>/dev/null | cut -d'=' -f2 || echo "")
            fi
            
            echo "        \"fio_write\": {"
            echo "          \"status\": \"${fio_write_status}\","
            echo "          \"iops\": \"${fio_write_iops}\","
            echo "          \"bandwidth\": \"${fio_write_bw}\","
            echo "          \"elapsed_ms\": \"${fio_write_elapsed}\","
            echo "          \"summary\": \"$(echo "$fio_write_summary" | sed 's/"/\\"/g')\","
            echo "          \"lat_unit\": \"${fio_write_lat_unit}\","
            echo "          \"clat_avg\": \"${fio_write_clat_avg}\","
            echo "          \"clat_min\": \"${fio_write_clat_min}\","
            echo "          \"clat_max\": \"${fio_write_clat_max}\","
            echo "          \"clat_p99\": \"${fio_write_p99}\","
            echo "          \"clat_p999\": \"${fio_write_p999}\""
            echo "        }"
            
            echo "      }"
        done
        
        echo "    ]"
        echo "  }"
    done
    
    echo "]"
}

# ============================================================================
# 生成 HTML 报告
# ============================================================================
JSON_DATA=$(generate_json_data)
REPORT_TIME=$(date '+%Y-%m-%d %H:%M:%S')

# 开始写入 HTML — 第一部分：head + CSS
cat > "$REPORT_FILE" <<'HTMLPART1'
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TDSQL 磁盘性能测试报告</title>
<style>
:root {
    --primary: #6366f1;
    --primary-light: #818cf8;
    --primary-dark: #4f46e5;
    --success: #10b981;
    --success-light: #34d399;
    --warning: #f59e0b;
    --warning-light: #fbbf24;
    --danger: #ef4444;
    --danger-light: #f87171;
    --info: #3b82f6;
    --info-light: #60a5fa;
    --bg-primary: #0f172a;
    --bg-secondary: #1e293b;
    --bg-card: #1e293b;
    --bg-card-hover: #334155;
    --text-primary: #f1f5f9;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --border: #334155;
    --border-light: #475569;
    --gradient-1: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    --gradient-2: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
    --gradient-3: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
    --gradient-4: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%);
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.3);
    --shadow-md: 0 4px 6px rgba(0,0,0,0.3);
    --shadow-lg: 0 10px 25px rgba(0,0,0,0.4);
    --shadow-xl: 0 20px 50px rgba(0,0,0,0.5);
    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
    --radius-xl: 20px;
    --nav-width: 220px;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.6;
    min-height: 100vh;
}

/* ===== 侧边导航 ===== */
.sidebar-nav {
    position: fixed;
    top: 0;
    left: 0;
    width: var(--nav-width);
    height: 100vh;
    background: rgba(15, 23, 42, 0.98);
    backdrop-filter: blur(20px);
    border-right: 1px solid var(--border);
    z-index: 200;
    display: flex;
    flex-direction: column;
    transition: transform 0.3s ease;
    overflow-y: auto;
}
.sidebar-nav .nav-brand {
    padding: 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 12px;
}
.sidebar-nav .nav-logo {
    width: 36px; height: 36px;
    background: var(--gradient-1);
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px; font-weight: 700; color: white; flex-shrink: 0;
}
.sidebar-nav .nav-title {
    font-size: 14px; font-weight: 600; color: var(--text-primary);
    line-height: 1.3;
}
.sidebar-nav .nav-subtitle {
    font-size: 11px; color: var(--text-muted);
}
.nav-section {
    padding: 16px 12px 8px;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    color: var(--text-muted);
}
.nav-link {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 16px; margin: 2px 8px;
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    text-decoration: none;
    font-size: 13px; font-weight: 500;
    transition: all 0.2s ease;
    cursor: pointer;
}
.nav-link:hover {
    background: rgba(99, 102, 241, 0.1);
    color: var(--text-primary);
}
.nav-link.active {
    background: rgba(99, 102, 241, 0.15);
    color: var(--primary-light);
    font-weight: 600;
}
.nav-link .nav-icon { font-size: 16px; width: 20px; text-align: center; }
.nav-link .nav-badge {
    margin-left: auto;
    background: rgba(99, 102, 241, 0.2);
    color: var(--primary-light);
    padding: 1px 8px;
    border-radius: 10px;
    font-size: 11px;
}
.nav-divider { height: 1px; background: var(--border); margin: 8px 16px; }

/* 移动端导航切换 */
.nav-toggle {
    display: none;
    position: fixed;
    top: 12px; left: 12px;
    z-index: 300;
    width: 40px; height: 40px;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text-primary);
    font-size: 18px;
    cursor: pointer;
    align-items: center; justify-content: center;
}
@media (max-width: 1024px) {
    .sidebar-nav { transform: translateX(-100%); }
    .sidebar-nav.open { transform: translateX(0); }
    .nav-toggle { display: flex; }
    .main-wrapper { margin-left: 0 !important; }
}

/* ===== 主内容区 ===== */
.main-wrapper {
    margin-left: var(--nav-width);
    min-height: 100vh;
    transition: margin-left 0.3s ease;
}

/* 顶部信息条 */
.top-bar {
    background: rgba(30, 41, 59, 0.95);
    backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--border);
    padding: 12px 32px;
    position: sticky;
    top: 0;
    z-index: 100;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.top-bar-left { display: flex; align-items: center; gap: 16px; }
.top-bar-breadcrumb { font-size: 13px; color: var(--text-secondary); }
.top-bar-breadcrumb span { color: var(--text-primary); font-weight: 500; }
.top-bar-right { display: flex; align-items: center; gap: 16px; font-size: 13px; color: var(--text-secondary); }
.top-bar-right .status-pill {
    display: flex; align-items: center; gap: 6px;
    padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 500;
}
.status-pill.success { background: rgba(16,185,129,0.1); color: var(--success); border: 1px solid rgba(16,185,129,0.2); }
.status-pill.danger { background: rgba(239,68,68,0.1); color: var(--danger); border: 1px solid rgba(239,68,68,0.2); }

/* 英雄区域 */
.hero {
    background: linear-gradient(135deg, #1e1b4b 0%, #312e81 30%, #1e293b 100%);
    padding: 40px 32px;
    position: relative;
    overflow: hidden;
}
.hero::before {
    content: '';
    position: absolute; top: -50%; right: -20%;
    width: 600px; height: 600px;
    background: radial-gradient(circle, rgba(99,102,241,0.15) 0%, transparent 70%);
    border-radius: 50%;
}
.hero-content { max-width: 1400px; margin: 0 auto; position: relative; z-index: 1; }
.hero h1 {
    font-size: 28px; font-weight: 700; margin-bottom: 6px;
    background: linear-gradient(135deg, #c7d2fe, #e0e7ff);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}
.hero-subtitle { font-size: 14px; color: var(--text-secondary); margin-bottom: 28px; }
.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 14px;
}
.stat-card {
    background: rgba(255,255,255,0.05);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: var(--radius-md);
    padding: 18px;
    transition: all 0.3s ease;
}
.stat-card:hover { background: rgba(255,255,255,0.08); transform: translateY(-2px); box-shadow: var(--shadow-lg); }
.stat-card .stat-icon {
    width: 36px; height: 36px; border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; margin-bottom: 10px;
}
.stat-card .stat-value { font-size: 24px; font-weight: 700; color: var(--text-primary); line-height: 1.2; }
.stat-card .stat-label { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }

/* 主内容 */
.main-content { max-width: 1400px; margin: 0 auto; padding: 28px 32px; }

/* 区块 */
.section { margin-bottom: 32px; scroll-margin-top: 60px; }
.section-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 18px; padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
}
.section-title {
    font-size: 18px; font-weight: 600;
    display: flex; align-items: center; gap: 10px;
}
.section-title .icon {
    width: 32px; height: 32px; border-radius: 8px;
    display: flex; align-items: center; justify-content: center; font-size: 16px;
}

/* 标签页 */
.tabs {
    display: flex; gap: 4px;
    background: var(--bg-secondary);
    border-radius: var(--radius-md);
    padding: 4px; margin-bottom: 20px;
    overflow-x: auto;
}
.tab-btn {
    padding: 9px 18px; border: none; background: transparent;
    color: var(--text-secondary); font-size: 13px; font-weight: 500;
    border-radius: var(--radius-sm); cursor: pointer;
    transition: all 0.2s ease; white-space: nowrap;
    display: flex; align-items: center; gap: 8px;
}
.tab-btn:hover { color: var(--text-primary); background: rgba(255,255,255,0.05); }
.tab-btn.active { background: var(--primary); color: white; box-shadow: var(--shadow-md); }
.tab-btn .badge {
    background: rgba(255,255,255,0.2); padding: 2px 8px;
    border-radius: 10px; font-size: 11px;
}
.tab-content { display: none; animation: fadeIn 0.3s ease; }
.tab-content.active { display: block; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
</style>
HTMLPART1

# 第二部分：更多 CSS
cat >> "$REPORT_FILE" <<'HTMLPART2'
<style>
/* 主机卡片 */
.host-card {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: var(--radius-lg); margin-bottom: 16px;
    overflow: hidden; transition: all 0.3s ease;
}
.host-card:hover { border-color: var(--primary); box-shadow: 0 0 0 1px var(--primary), var(--shadow-lg); }
.host-card-header {
    padding: 18px 22px;
    display: flex; align-items: center; justify-content: space-between;
    cursor: pointer; user-select: none; transition: background 0.2s ease;
}
.host-card-header:hover { background: rgba(255,255,255,0.02); }
.host-info { display: flex; align-items: center; gap: 14px; }
.host-avatar {
    width: 44px; height: 44px; border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; font-weight: 700; color: white;
}
.host-meta h3 { font-size: 15px; font-weight: 600; margin-bottom: 2px; }
.host-meta .host-ip { font-size: 12px; color: var(--text-secondary); font-family: 'SF Mono','Fira Code',monospace; }
.host-tags { display: flex; gap: 8px; align-items: center; }
.tag { padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 500; border: 1px solid; }
.tag-success { color: var(--success); border-color: rgba(16,185,129,0.3); background: rgba(16,185,129,0.1); }
.tag-danger { color: var(--danger); border-color: rgba(239,68,68,0.3); background: rgba(239,68,68,0.1); }
.tag-warning { color: var(--warning); border-color: rgba(245,158,11,0.3); background: rgba(245,158,11,0.1); }
.tag-info { color: var(--info); border-color: rgba(59,130,246,0.3); background: rgba(59,130,246,0.1); }
.expand-icon { font-size: 18px; color: var(--text-muted); transition: transform 0.3s ease; }
.host-card.expanded .expand-icon { transform: rotate(180deg); }
.host-card-body { display: none; padding: 0 22px 22px; border-top: 1px solid var(--border); }
.host-card.expanded .host-card-body { display: block; animation: slideDown 0.3s ease; }
@keyframes slideDown { from { opacity: 0; } to { opacity: 1; } }
.sys-info-bar {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 10px; padding: 14px 0; margin-bottom: 14px;
}
.sys-info-item { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.sys-info-item .label { color: var(--text-muted); }
.sys-info-item .value { color: var(--text-primary); font-weight: 500; }

/* 硬件信息面板 */
.hw-panel {
    margin: 12px 0 16px;
    padding: 14px 16px;
    background: linear-gradient(135deg, rgba(99,102,241,0.05) 0%, rgba(139,92,246,0.05) 100%);
    border: 1px solid rgba(139,92,246,0.18);
    border-radius: var(--radius-md);
    position: relative;
    overflow: hidden;
}
.hw-panel::before {
    content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
    background: linear-gradient(180deg, #6366f1, #8b5cf6);
}
.hw-panel-title {
    font-size: 12px; font-weight: 700; color: #6366f1;
    display: flex; align-items: center; gap: 6px; margin-bottom: 10px;
    letter-spacing: 0.3px;
}
.hw-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 10px 16px;
}
.hw-item {
    display: flex; align-items: center; gap: 8px; font-size: 12px;
    padding: 6px 10px;
    background: rgba(255,255,255,0.5);
    border-radius: 6px;
    border: 1px solid rgba(139,92,246,0.1);
}
.hw-item .ico { font-size: 14px; }
.hw-item .k { color: var(--text-muted); font-size: 11px; white-space: nowrap; }
.hw-item .v { color: var(--text-primary); font-weight: 600; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hw-item .v.mono { font-family: 'SFMono-Regular', Consolas, monospace; font-size: 11px; }
.hw-badge {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 700;
}
.hw-badge.physical { background: rgba(79,172,254,0.15); color: #0369a1; border: 1px solid rgba(79,172,254,0.3); }
.hw-badge.virtual { background: rgba(240,147,251,0.15); color: #a21caf; border: 1px solid rgba(240,147,251,0.3); }
.hw-badge.unknown { background: rgba(156,163,175,0.15); color: #6b7280; border: 1px solid rgba(156,163,175,0.3); }

/* 路径标题中的磁盘标签 */
.disk-tag {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 8px; margin-left: 8px;
    border-radius: 10px; font-size: 10px; font-weight: 600;
    font-family: 'SFMono-Regular', Consolas, monospace;
}
.disk-tag.ssd { background: rgba(67,233,123,0.15); color: #15803d; border: 1px solid rgba(67,233,123,0.3); }
.disk-tag.hdd { background: rgba(251,191,36,0.15); color: #b45309; border: 1px solid rgba(251,191,36,0.3); }
.disk-tag.nvme { background: rgba(139,92,246,0.15); color: #6d28d9; border: 1px solid rgba(139,92,246,0.3); }
.disk-tag.virtio { background: rgba(240,147,251,0.15); color: #a21caf; border: 1px solid rgba(240,147,251,0.3); }
.disk-tag.unknown { background: rgba(156,163,175,0.15); color: #6b7280; border: 1px solid rgba(156,163,175,0.3); }
.disk-tag.raid { background: rgba(255,107,107,0.15); color: #b91c1c; border: 1px solid rgba(255,107,107,0.3); }

/* 路径区 */
.path-section { margin-top: 14px; }
.path-title {
    font-size: 14px; font-weight: 600;
    padding: 10px 14px;
    background: rgba(99,102,241,0.08);
    border-radius: var(--radius-sm);
    margin-bottom: 14px;
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    border-left: 3px solid var(--primary);
}

/* 测试结果网格 */
.test-results-grid {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 14px;
}
@media (max-width: 1024px) { .test-results-grid { grid-template-columns: 1fr; } }
.test-result-card {
    background: rgba(255,255,255,0.03); border: 1px solid var(--border);
    border-radius: var(--radius-md); padding: 18px;
    position: relative; overflow: hidden; transition: all 0.3s ease;
}
.test-result-card:hover { background: rgba(255,255,255,0.05); transform: translateY(-2px); box-shadow: var(--shadow-md); }
.test-result-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; }
.test-result-card.dd-card::before { background: var(--gradient-3); }
.test-result-card.fio-read-card::before { background: var(--gradient-4); }
.test-result-card.fio-write-card::before { background: var(--gradient-2); }
.test-type-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.test-type-name { font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.test-type-icon {
    width: 26px; height: 26px; border-radius: 8px;
    display: flex; align-items: center; justify-content: center; font-size: 13px;
}
.status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.status-dot.ok { background: var(--success); box-shadow: 0 0 8px rgba(16,185,129,0.5); }
.status-dot.error { background: var(--danger); box-shadow: 0 0 8px rgba(239,68,68,0.5); }
.status-dot.na { background: var(--text-muted); }
.metric-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.04);
}
.metric-row:last-child { border-bottom: none; }
.metric-label { font-size: 12px; color: var(--text-secondary); }
.metric-value { font-size: 13px; font-weight: 600; color: var(--text-primary); font-family: 'SF Mono','Fira Code',monospace; }
.metric-value.highlight { font-size: 20px; background: var(--gradient-3); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
.metric-value.highlight-green { font-size: 20px; background: var(--gradient-4); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
.metric-value.highlight-pink { font-size: 20px; background: var(--gradient-2); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
.metric-value.lat-warn { color: var(--warning); }
.metric-value.lat-danger { color: var(--danger); }

/* 延迟子区块 */
.latency-section {
    margin-top: 10px; padding-top: 10px;
    border-top: 1px dashed rgba(255,255,255,0.06);
}
.latency-title {
    font-size: 11px; font-weight: 600; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 6px;
}

/* 汇总表格 */
.summary-table-wrapper { overflow-x: auto; border-radius: var(--radius-md); border: 1px solid var(--border); }
.summary-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.summary-table thead { background: rgba(99,102,241,0.1); }
.summary-table th {
    padding: 12px 14px; text-align: left; font-weight: 600;
    color: var(--text-primary); border-bottom: 2px solid var(--border); white-space: nowrap;
}
.summary-table td { padding: 10px 14px; border-bottom: 1px solid var(--border); color: var(--text-secondary); }
.summary-table tbody tr { transition: background 0.2s ease; }
.summary-table tbody tr:hover { background: rgba(255,255,255,0.03); }
.summary-table tbody tr:last-child td { border-bottom: none; }
.perf-bar { height: 6px; border-radius: 3px; background: var(--border); overflow: hidden; min-width: 60px; }
.perf-bar-fill { height: 100%; border-radius: 3px; transition: width 1s ease; }
.perf-excellent .perf-bar-fill { background: var(--success); }
.perf-good .perf-bar-fill { background: var(--info); }
.perf-fair .perf-bar-fill { background: var(--warning); }
.perf-poor .perf-bar-fill { background: var(--danger); }
.perf-badge {
    padding: 2px 8px; border-radius: 12px; font-size: 10px;
    font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
    display: inline-block;
}
.perf-badge.excellent { background: rgba(16,185,129,0.15); color: var(--success); border: 1px solid rgba(16,185,129,0.3); }
.perf-badge.good { background: rgba(59,130,246,0.15); color: var(--info); border: 1px solid rgba(59,130,246,0.3); }
.perf-badge.fair { background: rgba(245,158,11,0.15); color: var(--warning); border: 1px solid rgba(245,158,11,0.3); }
.perf-badge.poor { background: rgba(239,68,68,0.15); color: var(--danger); border: 1px solid rgba(239,68,68,0.3); }

/* 图表 */
.chart-container {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: var(--radius-lg); padding: 22px; margin-bottom: 20px;
}
.chart-title { font-size: 15px; font-weight: 600; margin-bottom: 18px; display: flex; align-items: center; gap: 8px; }
.bar-chart { display: flex; flex-direction: column; gap: 10px; }
.bar-row { display: grid; grid-template-columns: 140px 1fr 100px; align-items: center; gap: 12px; }
.bar-label { font-size: 12px; color: var(--text-secondary); text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-track { height: 26px; background: rgba(255,255,255,0.04); border-radius: 6px; overflow: hidden; position: relative; }
.bar-fill {
    height: 100%; border-radius: 6px;
    transition: width 1.2s cubic-bezier(0.4,0,0.2,1);
    position: relative; min-width: 2px;
}
.bar-fill.gradient-blue { background: linear-gradient(90deg, #4facfe, #00f2fe); }
.bar-fill.gradient-green { background: linear-gradient(90deg, #43e97b, #38f9d7); }
.bar-fill.gradient-pink { background: linear-gradient(90deg, #f093fb, #f5576c); }
.bar-fill.gradient-purple { background: linear-gradient(90deg, #667eea, #764ba2); }
.bar-fill.gradient-orange { background: linear-gradient(90deg, #f7971e, #ffd200); }
.bar-value { font-size: 12px; font-weight: 600; color: var(--text-primary); font-family: 'SF Mono','Fira Code',monospace; }

/* 参考面板 */
.ref-panel {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: var(--radius-lg); padding: 24px; margin-bottom: 24px;
}
.ref-grid {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;
}
@media (max-width: 900px) { .ref-grid { grid-template-columns: 1fr; } }
.ref-card {
    background: rgba(255,255,255,0.03); border: 1px solid var(--border);
    border-radius: var(--radius-md); padding: 20px;
    position: relative; overflow: hidden;
}
.ref-card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
}
.ref-card.hdd::before { background: linear-gradient(90deg, #64748b, #94a3b8); }
.ref-card.ssd::before { background: var(--gradient-3); }
.ref-card.nvme::before { background: var(--gradient-4); }
.ref-card-title {
    font-size: 16px; font-weight: 700; margin-bottom: 14px;
    display: flex; align-items: center; gap: 10px;
}
.ref-card-title .ref-icon {
    width: 32px; height: 32px; border-radius: 8px;
    display: flex; align-items: center; justify-content: center; font-size: 16px;
}
.ref-metric {
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.04);
    font-size: 13px;
}
.ref-metric:last-child { border-bottom: none; }
.ref-metric .ref-label { color: var(--text-secondary); }
.ref-metric .ref-value { font-weight: 600; color: var(--text-primary); font-family: 'SF Mono','Fira Code',monospace; }

/* 参数面板 */
.params-panel {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: var(--radius-lg); padding: 22px; margin-bottom: 24px;
}
.params-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
.param-item {
    display: flex; align-items: flex-start; gap: 12px;
    padding: 12px; background: rgba(255,255,255,0.02); border-radius: var(--radius-sm);
}
.param-icon {
    width: 36px; height: 36px; border-radius: 8px;
    display: flex; align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0;
}
.param-text .param-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
.param-text .param-value { font-size: 13px; font-weight: 500; color: var(--text-primary); margin-top: 2px; font-family: 'SF Mono','Fira Code',monospace; }

/* 页脚 */
.footer {
    text-align: center; padding: 28px;
    color: var(--text-muted); font-size: 12px;
    border-top: 1px solid var(--border); margin-top: 40px;
}

/* 滚动条 */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--border-light); }

/* 打印 */
@media print {
    body { background: white; color: #1a1a1a; }
    .sidebar-nav, .nav-toggle { display: none !important; }
    .main-wrapper { margin-left: 0 !important; }
    .host-card-body { display: block !important; }
}
</style>
</head>
<body>
HTMLPART2

# 写入侧边导航
cat >> "$REPORT_FILE" <<'HTML_NAV'
<!-- 移动端导航切换按钮 -->
<button class="nav-toggle" onclick="document.querySelector('.sidebar-nav').classList.toggle('open')">☰</button>

<!-- 侧边导航 -->
<nav class="sidebar-nav" id="sidebarNav">
    <div class="nav-brand">
        <div class="nav-logo">T</div>
        <div>
            <div class="nav-title">TDSQL 磁盘性能</div>
            <div class="nav-subtitle">测试报告</div>
        </div>
    </div>
    <div class="nav-section">报告概览</div>
    <a class="nav-link active" onclick="navTo('section-overview')" id="navlink-overview">
        <span class="nav-icon">📊</span> 总览对比
        <span class="nav-badge" id="nav-badge-overview"></span>
    </a>
    <a class="nav-link" onclick="navTo('section-detail')" id="navlink-detail">
        <span class="nav-icon">📋</span> 详细结果
        <span class="nav-badge" id="nav-badge-detail"></span>
    </a>
    <div class="nav-divider"></div>
    <div class="nav-section">性能分析</div>
    <a class="nav-link" onclick="navTo('section-chart')" id="navlink-chart">
        <span class="nav-icon">📈</span> 性能图表
    </a>
    <a class="nav-link" onclick="navTo('section-latency')" id="navlink-latency">
        <span class="nav-icon">⏱️</span> 延迟分析
    </a>
    <div class="nav-divider"></div>
    <div class="nav-section">参考标准</div>
    <a class="nav-link" onclick="navTo('section-reference')" id="navlink-reference">
        <span class="nav-icon">📚</span> 磁盘类型参考
    </a>
    <a class="nav-link" onclick="navTo('section-params')" id="navlink-params">
        <span class="nav-icon">⚙️</span> 测试参数
    </a>
</nav>
HTML_NAV

# 写入主内容区开始 + 顶部栏
cat >> "$REPORT_FILE" <<HTML_TOPBAR
<div class="main-wrapper">
<div class="top-bar">
    <div class="top-bar-left">
        <div class="top-bar-breadcrumb">TDSQL Toolkit / <span>磁盘性能测试报告</span></div>
    </div>
    <div class="top-bar-right">
        <span>📅 ${TEST_DATE}</span>
        <span>⏱️ 总耗时 ${TOTAL_TIME:-0}s</span>
        <span class="status-pill success">✅ 成功 ${SUCCESS_COUNT:-0}</span>
        ${FAIL_COUNT:+<span class="status-pill danger">❌ 失败 ${FAIL_COUNT}</span>}
    </div>
</div>
HTML_TOPBAR

# 写入英雄区域
cat >> "$REPORT_FILE" <<HTML_HERO
<div class="hero">
    <div class="hero-content">
        <h1>🔬 TDSQL 磁盘性能测试报告</h1>
        <p class="hero-subtitle">批量磁盘I/O性能基准测试 · dd 顺序写入 / fio 随机读 / fio 随机写 · 含延迟分析与磁盘类型参考</p>
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-icon" style="background:rgba(99,102,241,0.15);color:var(--primary-light);">🖥️</div>
                <div class="stat-value">${HOST_COUNT:-0}</div>
                <div class="stat-label">测试主机数</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon" style="background:rgba(16,185,129,0.15);color:var(--success-light);">✅</div>
                <div class="stat-value" style="color:var(--success);">${SUCCESS_COUNT:-0}</div>
                <div class="stat-label">测试成功</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon" style="background:rgba(239,68,68,0.15);color:var(--danger-light);">❌</div>
                <div class="stat-value" style="color:var(--danger);">${FAIL_COUNT:-0}</div>
                <div class="stat-label">测试失败</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon" style="background:rgba(245,158,11,0.15);color:var(--warning-light);">📁</div>
                <div class="stat-value" style="font-size:16px;">${TEST_PATHS:-/data,/data1}</div>
                <div class="stat-label">测试路径</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon" style="background:rgba(59,130,246,0.15);color:var(--info-light);">⏱️</div>
                <div class="stat-value">${TOTAL_TIME:-0}s</div>
                <div class="stat-label">总耗时</div>
            </div>
        </div>
    </div>
</div>
HTML_HERO

# 写入主内容区各 section 容器
cat >> "$REPORT_FILE" <<HTML_SECTIONS
<div class="main-content">

    <!-- 总览对比 -->
    <div class="section" id="section-overview">
        <div class="section-header">
            <div class="section-title">
                <span class="icon" style="background:rgba(99,102,241,0.15);color:var(--primary-light);">📊</span>
                总览对比
            </div>
        </div>
        <div id="tab-overview"></div>
    </div>

    <!-- 详细结果 -->
    <div class="section" id="section-detail">
        <div class="section-header">
            <div class="section-title">
                <span class="icon" style="background:rgba(67,233,123,0.15);color:#43e97b;">📋</span>
                详细结果
            </div>
        </div>
        <div id="tab-detail"></div>
    </div>

    <!-- 性能图表 -->
    <div class="section" id="section-chart">
        <div class="section-header">
            <div class="section-title">
                <span class="icon" style="background:rgba(79,172,254,0.15);color:#4facfe;">📈</span>
                性能图表
            </div>
        </div>
        <div id="tab-chart"></div>
    </div>

    <!-- 延迟分析 -->
    <div class="section" id="section-latency">
        <div class="section-header">
            <div class="section-title">
                <span class="icon" style="background:rgba(245,158,11,0.15);color:var(--warning-light);">⏱️</span>
                延迟分析
            </div>
        </div>
        <div id="tab-latency"></div>
    </div>

    <!-- 磁盘类型参考 -->
    <div class="section" id="section-reference">
        <div class="section-header">
            <div class="section-title">
                <span class="icon" style="background:rgba(139,92,246,0.15);color:#a78bfa;">📚</span>
                磁盘类型性能参考标准
            </div>
        </div>
        <div id="tab-reference"></div>
    </div>

    <!-- 测试参数 -->
    <div class="section" id="section-params">
        <div class="section-header">
            <div class="section-title">
                <span class="icon" style="background:rgba(99,102,241,0.15);color:var(--primary-light);">⚙️</span>
                测试参数配置
            </div>
        </div>
HTML_SECTIONS

# 写入测试参数面板
cat >> "$REPORT_FILE" <<HTML_PARAMS
        <div class="params-grid">
            <div class="param-item">
                <div class="param-icon" style="background:rgba(79,172,254,0.1);color:#4facfe;">📝</div>
                <div class="param-text">
                    <div class="param-label">DD 测试命令</div>
                    <div class="param-value">dd if=/dev/zero of=testfile bs=16K count=1024000 oflag=direct</div>
                </div>
            </div>
            <div class="param-item">
                <div class="param-icon" style="background:rgba(67,233,123,0.1);color:#43e97b;">📖</div>
                <div class="param-text">
                    <div class="param-label">FIO 随机读命令</div>
                    <div class="param-value">fio -direct=1 -iodepth=32 -rw=randread -bs=16k -size=${FIO_SIZE:-10G} -numjobs=8 -runtime=${FIO_RUNTIME:-120}</div>
                </div>
            </div>
            <div class="param-item">
                <div class="param-icon" style="background:rgba(240,147,251,0.1);color:#f093fb;">✏️</div>
                <div class="param-text">
                    <div class="param-label">FIO 随机写命令</div>
                    <div class="param-value">fio -direct=1 -iodepth=32 -rw=randwrite -bs=16k -size=${FIO_SIZE:-10G} -numjobs=8 -runtime=${FIO_RUNTIME:-120}</div>
                </div>
            </div>
            <div class="param-item">
                <div class="param-icon" style="background:rgba(102,126,234,0.1);color:#667eea;">💾</div>
                <div class="param-text">
                    <div class="param-label">测试路径</div>
                    <div class="param-value">/data (安装目录盘) · /data1 (数据盘)</div>
                </div>
            </div>
        </div>
    </div>

</div><!-- end main-content -->

<div class="footer">
    <p>TDSQL Disk Performance Test Report · Generated by TDSQL Toolkit</p>
    <p style="margin-top:4px;">测试工具: dd + fio · 报告生成时间: ${REPORT_TIME}</p>
</div>

</div><!-- end main-wrapper -->
HTML_PARAMS

# 写入 JavaScript 开始标签和数据
cat >> "$REPORT_FILE" <<'HTML_JS_START'
<script>
HTML_JS_START

cat >> "$REPORT_FILE" <<JSDATA
const testData = ${JSON_DATA};
JSDATA

# 写入 JavaScript 工具函数和渲染逻辑
cat >> "$REPORT_FILE" <<'JS_UTILS'

// ============================================================================
// 工具函数
// ============================================================================
function parseIOPS(str) {
    if (!str) return 0;
    str = str.replace('IOPS=','').replace('iops=','').trim();
    let val = parseFloat(str);
    if (str.toLowerCase().includes('k')) val *= 1000;
    if (str.toLowerCase().includes('m')) val *= 1000000;
    return isNaN(val) ? 0 : val;
}
function parseBW(str) {
    if (!str) return 0;
    str = str.replace('BW=','').replace('bw=','').trim();
    let val = parseFloat(str);
    if (str.toLowerCase().includes('gib/s') || str.toLowerCase().includes('gb/s')) val *= 1024;
    else if (str.toLowerCase().includes('kib/s') || str.toLowerCase().includes('kb/s')) val /= 1024;
    return isNaN(val) ? 0 : val;
}
// 清理带宽字符串，去掉 BW=/IOPS= 等前缀，只显示速度数值
function cleanBW(str) {
    if (!str) return 'N/A';
    return str.replace(/^BW=/i,'').replace(/^bw=/i,'').trim() || 'N/A';
}
// 清理IOPS字符串，去掉 IOPS= 前缀
function cleanIOPS(str) {
    if (!str) return 'N/A';
    return str.replace(/^IOPS=/i,'').replace(/^iops=/i,'').trim() || 'N/A';
}
function parseSpeed(str) {
    if (!str) return 0;
    let val = parseFloat(str);
    if (str.includes('GB/s') || str.includes('GB/秒')) val *= 1024;
    else if (str.includes('KB/s') || str.includes('KB/秒')) val /= 1024;
    return isNaN(val) ? 0 : val;
}
function formatIOPS(val) {
    if (val >= 1000000) return (val/1000000).toFixed(1)+'M';
    if (val >= 1000) return (val/1000).toFixed(1)+'K';
    return val.toFixed(0);
}
// 将延迟统一转换为 usec
function latToUsec(val, unit) {
    if (!val || isNaN(parseFloat(val))) return 0;
    let v = parseFloat(val);
    if (unit === 'msec') v *= 1000;
    else if (unit === 'nsec') v /= 1000;
    return v;
}
// 格式化延迟显示
function formatLat(usec) {
    if (usec <= 0) return 'N/A';
    if (usec >= 1000) return (usec/1000).toFixed(2) + ' ms';
    return usec.toFixed(1) + ' μs';
}
// 延迟评级
function rateLatency(usec) {
    if (usec <= 0) return { level:'na', label:'N/A', color:'var(--text-muted)' };
    if (usec <= 200) return { level:'excellent', label:'优秀', color:'var(--success)' };
    if (usec <= 1000) return { level:'good', label:'良好', color:'var(--info)' };
    if (usec <= 5000) return { level:'fair', label:'一般', color:'var(--warning)' };
    return { level:'poor', label:'较差', color:'var(--danger)' };
}
function ratePerformance(type, value) {
    const thresholds = {
        dd_speed: { excellent:500, good:300, fair:150 },
        fio_read_iops: { excellent:50000, good:20000, fair:5000 },
        fio_write_iops: { excellent:30000, good:10000, fair:3000 },
        fio_read_bw: { excellent:500, good:200, fair:50 },
        fio_write_bw: { excellent:300, good:100, fair:30 },
    };
    const t = thresholds[type] || { excellent:100, good:50, fair:20 };
    if (value >= t.excellent) return { level:'excellent', label:'优秀', color:'var(--success)' };
    if (value >= t.good) return { level:'good', label:'良好', color:'var(--info)' };
    if (value >= t.fair) return { level:'fair', label:'一般', color:'var(--warning)' };
    return { level:'poor', label:'较差', color:'var(--danger)' };
}
const gradientColors = [
    'var(--gradient-1)','var(--gradient-3)','var(--gradient-4)','var(--gradient-2)',
    'linear-gradient(135deg,#a18cd1 0%,#fbc2eb 100%)',
    'linear-gradient(135deg,#ffecd2 0%,#fcb69f 100%)'
];
function getHostColor(i) { return gradientColors[i % gradientColors.length]; }

// ============================================================================
// 导航
// ============================================================================
function navTo(sectionId) {
    const el = document.getElementById(sectionId);
    if (el) {
        el.scrollIntoView({ behavior:'smooth', block:'start' });
    }
    // 更新导航高亮
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    const navId = 'navlink-' + sectionId.replace('section-','');
    const navEl = document.getElementById(navId);
    if (navEl) navEl.classList.add('active');
    // 移动端关闭导航
    document.querySelector('.sidebar-nav').classList.remove('open');
}

// 滚动监听 — 自动高亮导航
const sectionIds = ['overview','detail','chart','latency','reference','params'];
window.addEventListener('scroll', function() {
    let current = '';
    sectionIds.forEach(id => {
        const el = document.getElementById('section-' + id);
        if (el && el.getBoundingClientRect().top <= 120) current = id;
    });
    if (current) {
        document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
        const navEl = document.getElementById('navlink-' + current);
        if (navEl) navEl.classList.add('active');
    }
});

function toggleHost(cardId) {
    document.getElementById(cardId).classList.toggle('expanded');
}

// ============================================================================
// 角色分组配置 & 工具函数
// ============================================================================
const ROLE_CONFIG = {
    tdsql_zk:        { label: '管控节点 (ZK)', icon: '🎛️', color: '#6366f1', bg: 'rgba(99,102,241,0.08)', border: 'rgba(99,102,241,0.25)' },
    tdsql_db:        { label: 'DB 节点',       icon: '🗄️', color: '#f59e0b', bg: 'rgba(245,158,11,0.08)',  border: 'rgba(245,158,11,0.25)' },
    tdsql_proxy:     { label: 'Proxy 节点',    icon: '🔀', color: '#10b981', bg: 'rgba(16,185,129,0.08)',  border: 'rgba(16,185,129,0.25)' },
    tdsql_hdfs:      { label: 'HDFS 节点',     icon: '📦', color: '#8b5cf6', bg: 'rgba(139,92,246,0.08)',  border: 'rgba(139,92,246,0.25)' },
    tdsql_kafka:     { label: 'Kafka 节点',    icon: '📡', color: '#ec4899', bg: 'rgba(236,72,153,0.08)',  border: 'rgba(236,72,153,0.25)' },
    tdsql_lvs:       { label: 'LVS 节点',      icon: '⚖️', color: '#14b8a6', bg: 'rgba(20,184,166,0.08)',  border: 'rgba(20,184,166,0.25)' },
    tdsql_chitu:     { label: '赤兔节点',      icon: '🐎', color: '#f97316', bg: 'rgba(249,115,22,0.08)',  border: 'rgba(249,115,22,0.25)' },
    tdsql_V3chitu:   { label: '新赤兔节点',    icon: '🦄', color: '#a855f7', bg: 'rgba(168,85,247,0.08)',  border: 'rgba(168,85,247,0.25)' },
    tdsql_monitor:   { label: '监控节点',      icon: '📊', color: '#06b6d4', bg: 'rgba(6,182,212,0.08)',   border: 'rgba(6,182,212,0.25)' },
    tdsql_scheduler: { label: 'Scheduler',     icon: '⏰', color: '#6366f1', bg: 'rgba(99,102,241,0.08)',  border: 'rgba(99,102,241,0.25)' },
    tdsql_oss:       { label: 'OSS 节点',      icon: '☁️', color: '#3b82f6', bg: 'rgba(59,130,246,0.08)',  border: 'rgba(59,130,246,0.25)' },
    tdsql_consumer:  { label: '多源同步消费者', icon: '🔄', color: '#84cc16', bg: 'rgba(132,204,22,0.08)',  border: 'rgba(132,204,22,0.25)' },
    tdsql_es:        { label: 'ES 节点',       icon: '🔍', color: '#eab308', bg: 'rgba(234,179,8,0.08)',   border: 'rgba(234,179,8,0.25)' },
    tdsql_mc:        { label: 'Meta Cluster',  icon: '🧩', color: '#64748b', bg: 'rgba(100,116,139,0.08)', border: 'rgba(100,116,139,0.25)' },
    _other:          { label: '其他节点',      icon: '📋', color: '#94a3b8', bg: 'rgba(148,163,184,0.08)', border: 'rgba(148,163,184,0.25)' }
};

function getRoleLabel(role) {
    return (ROLE_CONFIG[role] || ROLE_CONFIG._other).label;
}
function getRoleIcon(role) {
    return (ROLE_CONFIG[role] || ROLE_CONFIG._other).icon;
}
function getRoleConfig(role) {
    return ROLE_CONFIG[role] || ROLE_CONFIG._other;
}

function getRoleTags(host) {
    if (!host.roles) return '';
    const roles = host.roles.split('|').filter(r => r);
    if (!roles.length) return '';
    return roles.map(r => {
        const cfg = getRoleConfig(r);
        return '<span style="display:inline-flex;align-items:center;gap:3px;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:500;background:' + cfg.bg + ';color:' + cfg.color + ';border:1px solid ' + cfg.border + ';">' + cfg.icon + ' ' + getRoleLabel(r) + '</span>';
    }).join(' ');
}

function groupHostsByRole(hosts) {
    const ROLE_PRIORITY = ['tdsql_zk', 'tdsql_db', 'tdsql_proxy'];
    const MGMT_ROLES = ['tdsql_zk', 'tdsql_scheduler', 'tdsql_oss', 'tdsql_chitu', 'tdsql_V3chitu', 'tdsql_monitor'];
    const groups = {};
    const groupOrder = [];
    hosts.forEach(host => {
        const roles = host.roles ? host.roles.split('|').filter(r => r) : [];
        let primaryGroup = '_other';
        for (const pr of ROLE_PRIORITY) {
            if (roles.includes(pr)) {
                primaryGroup = (pr === 'tdsql_zk') ? 'tdsql_zk' : pr;
                break;
            }
        }
        if (primaryGroup === '_other') {
            for (const mr of MGMT_ROLES) {
                if (roles.includes(mr)) { primaryGroup = 'tdsql_zk'; break; }
            }
        }
        if (primaryGroup === '_other' && roles.length > 0) {
            primaryGroup = roles[0];
        }
        if (!groups[primaryGroup]) {
            groups[primaryGroup] = [];
            groupOrder.push(primaryGroup);
        }
        groups[primaryGroup].push(host);
    });
    const sortedOrder = [...ROLE_PRIORITY.filter(r => groups[r]), ...groupOrder.filter(r => !ROLE_PRIORITY.includes(r))];
    return { groups, order: sortedOrder };
}
JS_UTILS

cat >> "$REPORT_FILE" <<'JS_OVERVIEW'

// ============================================================================
// 渲染总览对比表（含具体 IOPS 数值 + 延迟指标）
// ============================================================================
function renderOverview() {
    const container = document.getElementById('tab-overview');
    if (!testData.length) {
        container.innerHTML = '<div style="text-align:center;padding:60px;color:var(--text-muted);"><div style="font-size:48px;margin-bottom:16px;">📭</div><h3 style="font-size:18px;color:var(--text-secondary);">暂无测试数据</h3><p>请先运行 disk_perf_test.sh 执行磁盘性能测试</p></div>';
        return;
    }

    let rows = [];
    testData.forEach(host => {
        host.paths.forEach(p => {
            const ddSpeed = parseSpeed(p.dd.speed);
            const readIOPS = parseIOPS(p.fio_read.iops);
            const readBW = parseBW(p.fio_read.bandwidth);
            const writeIOPS = parseIOPS(p.fio_write.iops);
            const writeBW = parseBW(p.fio_write.bandwidth);
            const readLatAvg = latToUsec(p.fio_read.clat_avg, p.fio_read.lat_unit);
            const readLatP99 = latToUsec(p.fio_read.clat_p99, p.fio_read.lat_unit);
            const writeLatAvg = latToUsec(p.fio_write.clat_avg, p.fio_write.lat_unit);
            const writeLatP99 = latToUsec(p.fio_write.clat_p99, p.fio_write.lat_unit);

            rows.push({
                alias: host.alias, ip: host.ip, path: p.path, roles: host.roles || '',
                ddSpeed, ddSpeedRaw: p.dd.speed, ddStatus: p.dd.status,
                readIOPS, readBW, readIOPSRaw: p.fio_read.iops, readBWRaw: p.fio_read.bandwidth, readStatus: p.fio_read.status,
                writeIOPS, writeBW, writeIOPSRaw: p.fio_write.iops, writeBWRaw: p.fio_write.bandwidth, writeStatus: p.fio_write.status,
                readLatAvg, readLatP99, writeLatAvg, writeLatP99,
                connStatus: host.conn_status
            });
        });
    });

    const maxDD = Math.max(...rows.map(r => r.ddSpeed), 1);
    const maxReadIOPS = Math.max(...rows.map(r => r.readIOPS), 1);
    const maxWriteIOPS = Math.max(...rows.map(r => r.writeIOPS), 1);

    let html = `
    <div class="summary-table-wrapper">
        <table class="summary-table">
            <thead>
                <tr>
                    <th rowspan="2">主机</th>
                    <th rowspan="2">IP</th>
                    <th rowspan="2">角色</th>
                    <th rowspan="2">路径</th>
                    <th colspan="2" style="text-align:center;border-bottom:1px solid var(--border);">DD 顺序写</th>
                    <th colspan="4" style="text-align:center;border-bottom:1px solid var(--border);">FIO 随机读</th>
                    <th colspan="4" style="text-align:center;border-bottom:1px solid var(--border);">FIO 随机写</th>
                </tr>
                <tr>
                    <th>速度</th>
                    <th>评级</th>
                    <th>IOPS</th>
                    <th>速度</th>
                    <th>Avg延迟</th>
                    <th>P99延迟</th>
                    <th>IOPS</th>
                    <th>速度</th>
                    <th>Avg延迟</th>
                    <th>P99延迟</th>
                </tr>
            </thead>
            <tbody>`;

    rows.forEach(r => {
        const ddRate = ratePerformance('dd_speed', r.ddSpeed);
        const readRate = ratePerformance('fio_read_iops', r.readIOPS);
        const writeRate = ratePerformance('fio_write_iops', r.writeIOPS);
        const readLatRate = rateLatency(r.readLatAvg);
        const writeLatRate = rateLatency(r.writeLatAvg);
        const readP99Rate = rateLatency(r.readLatP99);
        const writeP99Rate = rateLatency(r.writeLatP99);
        const statusIcon = r.connStatus === 'CONNECT_FAILED' ? '🔴 ' : '';

        html += `<tr>
            <td><strong>${statusIcon}${r.alias}</strong></td>
            <td style="font-family:monospace;font-size:11px;">${r.ip}</td>
            <td style="max-width:180px;">${r.roles ? r.roles.split('|').filter(x=>x).map(x => { const c = getRoleConfig(x); return '<span style="display:inline-block;padding:1px 6px;border-radius:8px;font-size:9px;font-weight:500;background:'+c.bg+';color:'+c.color+';border:1px solid '+c.border+';margin:1px;white-space:nowrap;">'+c.icon+' '+getRoleLabel(x)+'</span>'; }).join('') : '<span style="color:var(--text-muted);font-size:11px;">-</span>'}</td>
            <td><code style="background:rgba(99,102,241,0.1);padding:2px 6px;border-radius:4px;font-size:11px;">${r.path}</code></td>
            <td style="font-weight:600;">${r.ddSpeedRaw || 'N/A'}</td>
            <td><span class="perf-badge ${ddRate.level}">${ddRate.label}</span></td>
            <td style="font-weight:600;font-family:monospace;">${r.readIOPS > 0 ? formatIOPS(r.readIOPS) : 'N/A'}</td>
            <td style="font-size:12px;font-family:monospace;">${cleanBW(r.readBWRaw)}</td>
            <td style="color:${readLatRate.color};font-weight:500;">${formatLat(r.readLatAvg)}</td>
            <td style="color:${readP99Rate.color};font-weight:500;">${formatLat(r.readLatP99)}</td>
            <td style="font-weight:600;font-family:monospace;">${r.writeIOPS > 0 ? formatIOPS(r.writeIOPS) : 'N/A'}</td>
            <td style="font-size:12px;font-family:monospace;">${cleanBW(r.writeBWRaw)}</td>
            <td style="color:${writeLatRate.color};font-weight:500;">${formatLat(r.writeLatAvg)}</td>
            <td style="color:${writeP99Rate.color};font-weight:500;">${formatLat(r.writeLatP99)}</td>
        </tr>`;
    });

    html += '</tbody></table></div>';

    // 性能评级说明
    html += `
    <div style="margin-top:18px;padding:16px;background:rgba(255,255,255,0.02);border-radius:var(--radius-md);border:1px solid var(--border);">
        <div style="font-size:13px;font-weight:600;margin-bottom:12px;">📋 性能评级标准</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;font-size:12px;color:var(--text-secondary);">
            <div><strong>DD 顺序写:</strong>
                <span class="perf-badge excellent">优秀</span> ≥500MB/s
                <span class="perf-badge good">良好</span> ≥300MB/s
                <span class="perf-badge fair">一般</span> ≥150MB/s
                <span class="perf-badge poor">较差</span> &lt;150MB/s
            </div>
            <div><strong>FIO 随机读 IOPS:</strong>
                <span class="perf-badge excellent">优秀</span> ≥50K
                <span class="perf-badge good">良好</span> ≥20K
                <span class="perf-badge fair">一般</span> ≥5K
                <span class="perf-badge poor">较差</span> &lt;5K
            </div>
            <div><strong>FIO 随机写 IOPS:</strong>
                <span class="perf-badge excellent">优秀</span> ≥30K
                <span class="perf-badge good">良好</span> ≥10K
                <span class="perf-badge fair">一般</span> ≥3K
                <span class="perf-badge poor">较差</span> &lt;3K
            </div>
            <div><strong>I/O 延迟 (Avg):</strong>
                <span class="perf-badge excellent">优秀</span> ≤200μs
                <span class="perf-badge good">良好</span> ≤1ms
                <span class="perf-badge fair">一般</span> ≤5ms
                <span class="perf-badge poor">较差</span> &gt;5ms
            </div>
        </div>
    </div>`;

    // 失败主机列表
    const failedHosts = testData.filter(h => h.conn_status === 'CONNECT_FAILED');
    if (failedHosts.length > 0) {
        html += `
        <div style="margin-top:18px;padding:16px;background:rgba(255,71,87,0.05);border-radius:var(--radius-md);border:1px solid rgba(255,71,87,0.2);">
            <div style="font-size:13px;font-weight:600;margin-bottom:12px;color:var(--danger);">⚠️ 连接失败主机 (${failedHosts.length}台)</div>
            <div style="display:grid;gap:8px;">`;
        failedHosts.forEach(h => {
            const errMsg = h.conn_error || '请检查: SSH密码是否正确 / 是否已配置免密登录 / SSH端口是否正确 / 网络是否可达';
            html += `
                <div style="display:flex;align-items:center;gap:12px;padding:10px 14px;background:rgba(255,71,87,0.08);border-radius:8px;">
                    <span style="font-size:18px;">🔴</span>
                    <div style="flex:1;">
                        <div style="font-weight:600;font-size:13px;">${h.alias} <span style="font-family:monospace;font-weight:400;color:var(--text-muted);margin-left:6px;">${h.ip}</span></div>
                        <div style="font-size:12px;color:var(--text-muted);margin-top:2px;font-family:monospace;word-break:break-all;">${errMsg}</div>
                    </div>
                </div>`;
        });
        html += `</div></div>`;
    }

    container.innerHTML = html;
    document.getElementById('nav-badge-overview').textContent = rows.length;
}
JS_OVERVIEW

cat >> "$REPORT_FILE" <<'JS_DETAIL'

// ============================================================================
// 渲染单个主机卡片（复用函数）
// ============================================================================
function renderHostCard(host, idx) {
    const cardId = 'host-card-' + idx;
    const bgColor = getHostColor(idx);
    const statusTag = host.conn_status === 'CONNECT_FAILED'
        ? '<span class="tag tag-danger">连接失败</span>'
        : '<span class="tag tag-success">已连接</span>';
    const roleTags = getRoleTags(host);

    let html = '';
    html += '<div class="host-card expanded" id="' + cardId + '">';
    html += '<div class="host-card-header" onclick="toggleHost(\'' + cardId + '\')">';
    html += '<div class="host-info">';
    html += '<div class="host-avatar" style="background:' + bgColor + ';">' + host.alias.charAt(0).toUpperCase() + '</div>';
    html += '<div class="host-meta"><h3>' + host.alias + '</h3><span class="host-ip">' + host.ip + '</span></div>';
    html += '</div>';
    html += '<div class="host-tags">' + statusTag + ' ' + roleTags;
    if (host.os) html += '<span class="tag tag-info">' + host.os + '</span>';
    html += '<span class="expand-icon">▼</span></div>';
    html += '</div>';
    html += '<div class="host-card-body">';

    if (host.conn_status === 'CONNECT_FAILED') {
        html += '<div style="padding:24px;text-align:center;">';
        html += '<div style="font-size:48px;margin-bottom:12px;">🔌</div>';
        html += '<h3 style="color:var(--danger);margin-bottom:8px;">SSH 连接失败</h3>';
        if (host.conn_error) {
            html += '<div style="background:rgba(255,71,87,0.1);border:1px solid rgba(255,71,87,0.3);border-radius:8px;padding:12px 16px;margin-top:12px;text-align:left;font-family:monospace;font-size:13px;color:var(--text-muted);word-break:break-all;max-height:120px;overflow-y:auto;">' + host.conn_error + '</div>';
        } else {
            html += '<p style="color:var(--text-muted);margin-top:8px;">请检查 SSH 密码是否正确、目标主机是否已配置免密登录、SSH 端口是否正确、网络是否可达。</p>';
        }
        html += '</div></div></div>';
        return html;
    }

    html += '<div class="sys-info-bar">';
    if (host.hostname) html += '<div class="sys-info-item"><span class="label">主机名:</span><span class="value">' + host.hostname + '</span></div>';
    if (host.cpu) html += '<div class="sys-info-item"><span class="label">CPU:</span><span class="value">' + host.cpu + '</span></div>';
    if (host.memory) html += '<div class="sys-info-item"><span class="label">内存:</span><span class="value">' + host.memory + '</span></div>';
    if (host.kernel) html += '<div class="sys-info-item"><span class="label">内核:</span><span class="value">' + host.kernel + '</span></div>';
    html += '</div>';
    
    // 硬件信息面板（机器类型、厂商、型号、RAID）
    const hasHw = host.machine_type || host.sys_manufacturer || host.sys_product || host.raid_controller || host.mdadm_raid || host.hw_raid_detail;
    if (hasHw) {
        let mtBadge = '';
        if (host.machine_type === 'physical') {
            mtBadge = '<span class="hw-badge physical">🖥️ 物理机</span>';
        } else if (host.machine_type === 'virtual') {
            const vt = host.virt_type ? ' · ' + host.virt_type : '';
            mtBadge = '<span class="hw-badge virtual">☁️ 虚拟机' + vt + '</span>';
        } else {
            mtBadge = '<span class="hw-badge unknown">❔ 未知</span>';
        }
        html += '<div class="hw-panel">';
        html += '<div class="hw-panel-title">🔧 硬件信息 ' + mtBadge + '</div>';
        html += '<div class="hw-grid">';
        if (host.sys_manufacturer) html += '<div class="hw-item"><span class="ico">🏭</span><span class="k">厂商:</span><span class="v">' + host.sys_manufacturer + '</span></div>';
        if (host.sys_product) html += '<div class="hw-item"><span class="ico">📦</span><span class="k">型号:</span><span class="v">' + host.sys_product + '</span></div>';
        if (host.bios_version) html += '<div class="hw-item"><span class="ico">💾</span><span class="k">BIOS:</span><span class="v mono">' + host.bios_version + '</span></div>';
        if (host.sys_serial) html += '<div class="hw-item"><span class="ico">🔢</span><span class="k">SN:</span><span class="v mono">' + host.sys_serial + '</span></div>';
        if (host.raid_controller) html += '<div class="hw-item"><span class="ico">🎛️</span><span class="k">RAID控制器:</span><span class="v">' + host.raid_controller + '</span></div>';
        if (host.mdadm_raid) html += '<div class="hw-item"><span class="ico">🧩</span><span class="k">软RAID:</span><span class="v mono">' + host.mdadm_raid.replace(/\|/g,' / ') + '</span></div>';
        if (host.hw_raid_detail) html += '<div class="hw-item" style="grid-column:1/-1;"><span class="ico">🗄️</span><span class="k">硬RAID配置:</span><span class="v mono" style="white-space:normal;">' + host.hw_raid_detail.replace(/;/g,' | ') + '</span></div>';
        if (host.all_disks) html += '<div class="hw-item" style="grid-column:1/-1;"><span class="ico">💽</span><span class="k">磁盘列表:</span><span class="v mono" style="white-space:normal;">' + host.all_disks.replace(/\|/g,' ； ') + '</span></div>';
        html += '</div></div>';
    }

    host.paths.forEach(p => {
        const ddSpeed = parseSpeed(p.dd.speed);
        const readIOPS = parseIOPS(p.fio_read.iops);
        const writeIOPS = parseIOPS(p.fio_write.iops);
        const ddRate = ratePerformance('dd_speed', ddSpeed);
        const readRate = ratePerformance('fio_read_iops', readIOPS);
        const writeRate = ratePerformance('fio_write_iops', writeIOPS);

        const rLatAvg = latToUsec(p.fio_read.clat_avg, p.fio_read.lat_unit);
        const rLatMin = latToUsec(p.fio_read.clat_min, p.fio_read.lat_unit);
        const rLatMax = latToUsec(p.fio_read.clat_max, p.fio_read.lat_unit);
        const rLatP99 = latToUsec(p.fio_read.clat_p99, p.fio_read.lat_unit);
        const rLatP999 = latToUsec(p.fio_read.clat_p999, p.fio_read.lat_unit);
        const wLatAvg = latToUsec(p.fio_write.clat_avg, p.fio_write.lat_unit);
        const wLatMin = latToUsec(p.fio_write.clat_min, p.fio_write.lat_unit);
        const wLatMax = latToUsec(p.fio_write.clat_max, p.fio_write.lat_unit);
        const wLatP99 = latToUsec(p.fio_write.clat_p99, p.fio_write.lat_unit);
        const wLatP999 = latToUsec(p.fio_write.clat_p999, p.fio_write.lat_unit);

        const rLatAvgRate = rateLatency(rLatAvg);
        const wLatAvgRate = rateLatency(wLatAvg);

        const pathIcon = p.path === '/data1' ? '💾' : '📂';
        const pathDesc = p.path === '/data1' ? '数据盘' : (p.path === '/data' ? '安装目录盘' : '');
        
        // 构建磁盘标签
        let diskTags = '';
        const dd = p.disk_detail || {};
        if (dd.disk_type) {
            let cls = 'unknown';
            const dt = (dd.disk_type || '').toLowerCase();
            if (dt.indexOf('nvme') >= 0) cls = 'nvme';
            else if (dt.indexOf('cloud') >= 0 || dt.indexOf('virtio') >= 0 || dt.indexOf('xen') >= 0) cls = 'virtio';
            else if (dt.indexOf('ssd') >= 0) cls = 'ssd';
            else if (dt.indexOf('hdd') >= 0) cls = 'hdd';
            const icon = cls === 'nvme' ? '⚡' : (cls === 'ssd' ? '🟢' : (cls === 'hdd' ? '🔵' : (cls === 'virtio' ? '☁️' : '⚪')));
            diskTags += '<span class="disk-tag ' + cls + '">' + icon + ' ' + dd.disk_type + '</span>';
        }
        if (dd.tran && dd.tran !== 'null' && dd.tran.length > 0) {
            let cls = 'unknown';
            const t = dd.tran.toLowerCase();
            if (t.indexOf('virtio') >= 0) cls = 'virtio';
            else if (t.indexOf('nvme') >= 0) cls = 'nvme';
            diskTags += '<span class="disk-tag ' + cls + '">🔌 ' + dd.tran.toUpperCase() + '</span>';
        }
        if (dd.base_disk) {
            diskTags += '<span class="disk-tag unknown">/dev/' + dd.base_disk + (dd.disk_size ? ' · ' + dd.disk_size : '') + '</span>';
        }
        if (dd.raid_info) {
            const rInfo = dd.raid_info.replace(/;/g, ' / ').replace(/\s+/g,' ').trim();
            diskTags += '<span class="disk-tag raid">🛡️ ' + rInfo + '</span>';
        }

        html += '<div class="path-section">';
        html += '<div class="path-title">' + pathIcon + ' ' + p.path + ' ';
        if (pathDesc) html += '<span style="font-size:11px;color:var(--text-muted);font-weight:400;">— ' + pathDesc + '</span>';
        html += diskTags;
        if (p.disk_info) html += '<span style="font-size:10px;color:var(--text-muted);font-weight:400;margin-left:auto;font-family:monospace;">' + p.disk_info + '</span>';
        html += '</div>';

        html += '<div class="test-results-grid">';

        // DD 顺序写
        html += '<div class="test-result-card dd-card">';
        html += '<div class="test-type-header"><div class="test-type-name"><div class="test-type-icon" style="background:rgba(79,172,254,0.15);color:#4facfe;">⚡</div>DD 顺序写</div>';
        html += '<span class="status-dot ' + (p.dd.status==='OK'?'ok':(p.dd.status==='N/A'||p.dd.status==='PATH_NOT_FOUND'?'na':'error')) + '"></span></div>';
        html += '<div class="metric-row"><span class="metric-label">写入速度</span><span class="metric-value highlight">' + (p.dd.speed||'N/A') + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">性能评级</span><span class="perf-badge ' + ddRate.level + '">' + ddRate.label + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">耗时</span><span class="metric-value">' + (p.dd.elapsed_ms ? (p.dd.elapsed_ms/1000).toFixed(1)+'s' : 'N/A') + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">测试参数</span><span class="metric-value" style="font-size:10px;">bs=16K count=1024000</span></div>';
        html += '</div>';

        // FIO 随机读（含延迟）
        html += '<div class="test-result-card fio-read-card">';
        html += '<div class="test-type-header"><div class="test-type-name"><div class="test-type-icon" style="background:rgba(67,233,123,0.15);color:#43e97b;">📖</div>FIO 随机读</div>';
        html += '<span class="status-dot ' + (p.fio_read.status==='OK'?'ok':(p.fio_read.status==='N/A'||p.fio_read.status==='PATH_NOT_FOUND'?'na':'error')) + '"></span></div>';
        html += '<div class="metric-row"><span class="metric-label">IOPS</span><span class="metric-value highlight-green">' + cleanIOPS(p.fio_read.iops) + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">速度</span><span class="metric-value">' + cleanBW(p.fio_read.bandwidth) + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">IOPS 评级</span><span class="perf-badge ' + readRate.level + '">' + readRate.label + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">耗时</span><span class="metric-value">' + (p.fio_read.elapsed_ms ? (p.fio_read.elapsed_ms/1000).toFixed(1)+'s' : 'N/A') + '</span></div>';
        html += '<div class="latency-section"><div class="latency-title">⏱ 读延迟</div>';
        html += '<div class="metric-row"><span class="metric-label">Avg 延迟</span><span class="metric-value" style="color:' + rLatAvgRate.color + ';">' + formatLat(rLatAvg) + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">Min / Max</span><span class="metric-value" style="font-size:11px;">' + formatLat(rLatMin) + ' / ' + formatLat(rLatMax) + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">P99</span><span class="metric-value">' + formatLat(rLatP99) + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">P99.9</span><span class="metric-value">' + formatLat(rLatP999) + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">延迟评级</span><span class="perf-badge ' + rLatAvgRate.level + '">' + rLatAvgRate.label + '</span></div>';
        html += '</div>';
        html += '</div>';

        // FIO 随机写（含延迟）
        html += '<div class="test-result-card fio-write-card">';
        html += '<div class="test-type-header"><div class="test-type-name"><div class="test-type-icon" style="background:rgba(240,147,251,0.15);color:#f093fb;">✏️</div>FIO 随机写</div>';
        html += '<span class="status-dot ' + (p.fio_write.status==='OK'?'ok':(p.fio_write.status==='N/A'||p.fio_write.status==='PATH_NOT_FOUND'?'na':'error')) + '"></span></div>';
        html += '<div class="metric-row"><span class="metric-label">IOPS</span><span class="metric-value highlight-pink">' + cleanIOPS(p.fio_write.iops) + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">速度</span><span class="metric-value">' + cleanBW(p.fio_write.bandwidth) + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">IOPS 评级</span><span class="perf-badge ' + writeRate.level + '">' + writeRate.label + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">耗时</span><span class="metric-value">' + (p.fio_write.elapsed_ms ? (p.fio_write.elapsed_ms/1000).toFixed(1)+'s' : 'N/A') + '</span></div>';
        html += '<div class="latency-section"><div class="latency-title">⏱ 写延迟</div>';
        html += '<div class="metric-row"><span class="metric-label">Avg 延迟</span><span class="metric-value" style="color:' + wLatAvgRate.color + ';">' + formatLat(wLatAvg) + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">Min / Max</span><span class="metric-value" style="font-size:11px;">' + formatLat(wLatMin) + ' / ' + formatLat(wLatMax) + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">P99</span><span class="metric-value">' + formatLat(wLatP99) + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">P99.9</span><span class="metric-value">' + formatLat(wLatP999) + '</span></div>';
        html += '<div class="metric-row"><span class="metric-label">延迟评级</span><span class="perf-badge ' + wLatAvgRate.level + '">' + wLatAvgRate.label + '</span></div>';
        html += '</div>';
        html += '</div>';

        html += '</div>'; // test-results-grid
        html += '</div>'; // path-section
    });

    html += '</div></div>'; // host-card-body, host-card
    return html;
}

// ============================================================================
// 渲染详细结果（按角色分组展示）
// ============================================================================
function renderDetail() {
    const container = document.getElementById('tab-detail');
    if (!testData.length) {
        container.innerHTML = '<div style="text-align:center;padding:60px;color:var(--text-muted);"><div style="font-size:48px;margin-bottom:16px;">📭</div><h3>暂无测试数据</h3></div>';
        return;
    }

    // 检查是否有角色信息
    const hasRoles = testData.some(h => h.roles && h.roles.length > 0);
    let html = '';

    if (hasRoles) {
        // 按角色分组展示
        const { groups, order } = groupHostsByRole(testData);
        let globalIdx = 0;

        order.forEach(groupKey => {
            const hosts = groups[groupKey];
            const cfg = getRoleConfig(groupKey);
            const groupId = 'role-group-' + groupKey.replace(/[^a-zA-Z0-9]/g, '_');

            html += '<div class="role-group" id="' + groupId + '" style="margin-bottom:24px;">';
            // 分组标题栏
            html += '<div class="role-group-header" style="display:flex;align-items:center;gap:12px;padding:14px 20px;margin-bottom:16px;background:' + cfg.bg + ';border:1px solid ' + cfg.border + ';border-radius:12px;cursor:pointer;" onclick="toggleRoleGroup(\'' + groupId + '\')">';
            html += '<span style="font-size:24px;">' + cfg.icon + '</span>';
            html += '<div style="flex:1;">';
            html += '<div style="font-size:15px;font-weight:700;color:' + cfg.color + ';">' + cfg.label + '</div>';
            html += '<div style="font-size:11px;color:var(--text-muted);margin-top:2px;">共 ' + hosts.length + ' 台主机</div>';
            html += '</div>';
            html += '<span class="role-group-badge" style="display:inline-flex;align-items:center;justify-content:center;min-width:28px;height:28px;border-radius:14px;background:' + cfg.color + ';color:#fff;font-size:13px;font-weight:700;">' + hosts.length + '</span>';
            html += '<span class="role-expand-icon" style="font-size:12px;color:var(--text-muted);transition:transform 0.3s;">▼</span>';
            html += '</div>';

            // 分组内的主机卡片
            html += '<div class="role-group-body">';
            hosts.forEach(host => {
                html += renderHostCard(host, globalIdx);
                globalIdx++;
            });
            html += '</div>';
            html += '</div>'; // role-group
        });
    } else {
        // 无角色信息时，平铺展示（兼容旧数据）
        testData.forEach((host, idx) => {
            html += renderHostCard(host, idx);
        });
    }

    container.innerHTML = html;
    document.getElementById('nav-badge-detail').textContent = testData.length;
}

// 切换角色分组的展开/折叠
function toggleRoleGroup(groupId) {
    const group = document.getElementById(groupId);
    if (!group) return;
    const body = group.querySelector('.role-group-body');
    const icon = group.querySelector('.role-expand-icon');
    if (body.style.display === 'none') {
        body.style.display = '';
        if (icon) icon.style.transform = 'rotate(0deg)';
    } else {
        body.style.display = 'none';
        if (icon) icon.style.transform = 'rotate(-90deg)';
    }
}
JS_DETAIL

cat >> "$REPORT_FILE" <<'JS_CHARTS'

// ============================================================================
// 渲染性能图表
// ============================================================================
function renderBarChart(title, icon, data, unit, gradientClass) {
    const maxVal = Math.max(...data.map(d => d.value), 1);
    let bars = data.map(d => {
        const pct = (d.value / maxVal * 100).toFixed(1);
        return '<div class="bar-row"><div class="bar-label" title="' + d.label + '">' + d.label + '</div><div class="bar-track"><div class="bar-fill ' + gradientClass + '" style="width:' + pct + '%;"></div></div><div class="bar-value">' + (d.raw || 'N/A') + '</div></div>';
    }).join('');
    return '<div class="chart-container"><div class="chart-title">' + icon + ' ' + title + '</div><div class="bar-chart">' + bars + '</div></div>';
}

function renderCharts() {
    const container = document.getElementById('tab-chart');
    if (!testData.length) {
        container.innerHTML = '<div style="text-align:center;padding:60px;color:var(--text-muted);"><div style="font-size:48px;margin-bottom:16px;">📊</div><h3>暂无图表数据</h3></div>';
        return;
    }

    let ddData=[], readData=[], writeData=[], bwReadData=[], bwWriteData=[];
    testData.forEach(host => {
        host.paths.forEach(p => {
            const label = host.alias + ' (' + p.path + ')';
            ddData.push({ label, value: parseSpeed(p.dd.speed), raw: p.dd.speed });
            readData.push({ label, value: parseIOPS(p.fio_read.iops), raw: cleanIOPS(p.fio_read.iops) });
            writeData.push({ label, value: parseIOPS(p.fio_write.iops), raw: cleanIOPS(p.fio_write.iops) });
            bwReadData.push({ label, value: parseBW(p.fio_read.bandwidth), raw: cleanBW(p.fio_read.bandwidth) });
            bwWriteData.push({ label, value: parseBW(p.fio_write.bandwidth), raw: cleanBW(p.fio_write.bandwidth) });
        });
    });

    let html = '';
    html += renderBarChart('DD 顺序写入速度对比', '⚡', ddData, 'MB/s', 'gradient-blue');
    html += renderBarChart('FIO 随机读 IOPS 对比', '📖', readData, 'IOPS', 'gradient-green');
    html += renderBarChart('FIO 随机写 IOPS 对比', '✏️', writeData, 'IOPS', 'gradient-pink');
    html += renderBarChart('FIO 随机读速度对比', '📊', bwReadData, 'MiB/s', 'gradient-purple');
    html += renderBarChart('FIO 随机写速度对比', '📉', bwWriteData, 'MiB/s', 'gradient-orange');
    container.innerHTML = html;
}

// ============================================================================
// 渲染延迟分析
// ============================================================================
function renderLatency() {
    const container = document.getElementById('tab-latency');
    if (!testData.length) {
        container.innerHTML = '<div style="text-align:center;padding:60px;color:var(--text-muted);"><div style="font-size:48px;margin-bottom:16px;">⏱️</div><h3>暂无延迟数据</h3></div>';
        return;
    }

    let readLatData=[], writeLatData=[], readP99Data=[], writeP99Data=[];
    testData.forEach(host => {
        host.paths.forEach(p => {
            const label = host.alias + ' (' + p.path + ')';
            const rAvg = latToUsec(p.fio_read.clat_avg, p.fio_read.lat_unit);
            const wAvg = latToUsec(p.fio_write.clat_avg, p.fio_write.lat_unit);
            const rP99 = latToUsec(p.fio_read.clat_p99, p.fio_read.lat_unit);
            const wP99 = latToUsec(p.fio_write.clat_p99, p.fio_write.lat_unit);
            readLatData.push({ label, value: rAvg, raw: formatLat(rAvg) });
            writeLatData.push({ label, value: wAvg, raw: formatLat(wAvg) });
            readP99Data.push({ label, value: rP99, raw: formatLat(rP99) });
            writeP99Data.push({ label, value: wP99, raw: formatLat(wP99) });
        });
    });

    let html = '';

    // 延迟汇总表
    html += '<div class="summary-table-wrapper" style="margin-bottom:20px;"><table class="summary-table"><thead><tr>';
    html += '<th>主机</th><th>路径</th>';
    html += '<th>读 Avg延迟</th><th>读 P99延迟</th><th>读延迟评级</th>';
    html += '<th>写 Avg延迟</th><th>写 P99延迟</th><th>写延迟评级</th>';
    html += '</tr></thead><tbody>';

    testData.forEach(host => {
        host.paths.forEach(p => {
            const rAvg = latToUsec(p.fio_read.clat_avg, p.fio_read.lat_unit);
            const rP99 = latToUsec(p.fio_read.clat_p99, p.fio_read.lat_unit);
            const wAvg = latToUsec(p.fio_write.clat_avg, p.fio_write.lat_unit);
            const wP99 = latToUsec(p.fio_write.clat_p99, p.fio_write.lat_unit);
            const rRate = rateLatency(rAvg);
            const wRate = rateLatency(wAvg);

            html += '<tr>';
            html += '<td><strong>' + host.alias + '</strong><br><span style="font-size:11px;color:var(--text-muted);font-family:monospace;">' + host.ip + '</span></td>';
            html += '<td><code style="background:rgba(99,102,241,0.1);padding:2px 6px;border-radius:4px;font-size:11px;">' + p.path + '</code></td>';
            html += '<td style="font-weight:600;color:' + rRate.color + ';">' + formatLat(rAvg) + '</td>';
            html += '<td style="font-weight:500;">' + formatLat(rP99) + '</td>';
            html += '<td><span class="perf-badge ' + rRate.level + '">' + rRate.label + '</span></td>';
            html += '<td style="font-weight:600;color:' + wRate.color + ';">' + formatLat(wAvg) + '</td>';
            html += '<td style="font-weight:500;">' + formatLat(wP99) + '</td>';
            html += '<td><span class="perf-badge ' + wRate.level + '">' + wRate.label + '</span></td>';
            html += '</tr>';
        });
    });
    html += '</tbody></table></div>';

    // 延迟对比图
    html += renderBarChart('随机读 Avg 延迟对比（越低越好）', '📖', readLatData, 'μs', 'gradient-green');
    html += renderBarChart('随机写 Avg 延迟对比（越低越好）', '✏️', writeLatData, 'μs', 'gradient-pink');
    html += renderBarChart('随机读 P99 延迟对比（越低越好）', '📖', readP99Data, 'μs', 'gradient-blue');
    html += renderBarChart('随机写 P99 延迟对比（越低越好）', '✏️', writeP99Data, 'μs', 'gradient-orange');

    // 延迟评级说明
    html += '<div style="margin-top:16px;padding:16px;background:rgba(255,255,255,0.02);border-radius:var(--radius-md);border:1px solid var(--border);">';
    html += '<div style="font-size:13px;font-weight:600;margin-bottom:10px;">📋 延迟评级标准说明</div>';
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px;font-size:12px;color:var(--text-secondary);">';
    html += '<div><span class="perf-badge excellent">优秀</span> Avg ≤ 200μs — NVMe 级别</div>';
    html += '<div><span class="perf-badge good">良好</span> Avg ≤ 1ms — SSD 级别</div>';
    html += '<div><span class="perf-badge fair">一般</span> Avg ≤ 5ms — SATA SSD 级别</div>';
    html += '<div><span class="perf-badge poor">较差</span> Avg > 5ms — HDD 级别</div>';
    html += '</div></div>';

    container.innerHTML = html;
}
JS_CHARTS

cat >> "$REPORT_FILE" <<'JS_REFERENCE'

// ============================================================================
// 渲染磁盘类型参考标准
// ============================================================================
function renderReference() {
    const container = document.getElementById('tab-reference');

    let html = '<div class="ref-grid">';

    // HDD 机械硬盘
    html += '<div class="ref-card hdd">';
    html += '<div class="ref-card-title"><div class="ref-icon" style="background:rgba(100,116,139,0.15);color:#94a3b8;">💿</div>HDD 机械硬盘</div>';
    html += '<div class="ref-metric"><span class="ref-label">顺序读速度</span><span class="ref-value">100 ~ 200 MB/s</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">顺序写速度</span><span class="ref-value">80 ~ 180 MB/s</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机读 IOPS (4K)</span><span class="ref-value">75 ~ 150</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机写 IOPS (4K)</span><span class="ref-value">75 ~ 150</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机读 IOPS (16K)</span><span class="ref-value">50 ~ 120</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机写 IOPS (16K)</span><span class="ref-value">50 ~ 100</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机读速度 (16K)</span><span class="ref-value">0.8 ~ 1.9 MB/s</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机写速度 (16K)</span><span class="ref-value">0.8 ~ 1.6 MB/s</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">平均读延迟</span><span class="ref-value">5 ~ 15 ms</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">平均写延迟</span><span class="ref-value">5 ~ 15 ms</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">P99 延迟</span><span class="ref-value">10 ~ 30 ms</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">典型转速</span><span class="ref-value">7200 / 10000 / 15000 RPM</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">接口类型</span><span class="ref-value">SATA III / SAS</span></div>';
    html += '<div style="margin-top:12px;padding:10px;background:rgba(100,116,139,0.08);border-radius:8px;font-size:11px;color:var(--text-muted);line-height:1.6;">';
    html += '<strong>适用场景：</strong>冷数据存储、备份归档、日志存储等对延迟不敏感的场景。<br>';
    html += '<strong>注意：</strong>HDD 的随机 I/O 性能极差，不适合 TDSQL 数据库的数据盘使用。15K RPM SAS 盘在 RAID 阵列下可获得更好的 IOPS。';
    html += '</div>';
    html += '</div>';

    // SATA SSD
    html += '<div class="ref-card ssd">';
    html += '<div class="ref-card-title"><div class="ref-icon" style="background:rgba(79,172,254,0.15);color:#4facfe;">💾</div>SATA SSD 固态硬盘</div>';
    html += '<div class="ref-metric"><span class="ref-label">顺序读速度</span><span class="ref-value">400 ~ 560 MB/s</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">顺序写速度</span><span class="ref-value">300 ~ 530 MB/s</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机读 IOPS (4K)</span><span class="ref-value">30K ~ 100K</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机写 IOPS (4K)</span><span class="ref-value">20K ~ 90K</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机读 IOPS (16K)</span><span class="ref-value">20K ~ 70K</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机写 IOPS (16K)</span><span class="ref-value">15K ~ 50K</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机读速度 (16K)</span><span class="ref-value">312 ~ 560 MB/s</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机写速度 (16K)</span><span class="ref-value">234 ~ 530 MB/s</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">平均读延迟</span><span class="ref-value">50 ~ 200 μs</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">平均写延迟</span><span class="ref-value">100 ~ 500 μs</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">P99 延迟</span><span class="ref-value">0.5 ~ 2 ms</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">接口类型</span><span class="ref-value">SATA III (6Gbps)</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">接口带宽上限</span><span class="ref-value">~560 MB/s</span></div>';
    html += '<div style="margin-top:12px;padding:10px;background:rgba(79,172,254,0.08);border-radius:8px;font-size:11px;color:var(--text-muted);line-height:1.6;">';
    html += '<strong>适用场景：</strong>中小规模 TDSQL 部署、安装目录盘、中等负载数据库。<br>';
    html += '<strong>注意：</strong>SATA 接口带宽是瓶颈（最大 ~560MB/s），顺序读写受限。企业级 SSD 通常有更好的写入耐久度和稳定性。';
    html += '</div>';
    html += '</div>';

    // NVMe SSD
    html += '<div class="ref-card nvme">';
    html += '<div class="ref-card-title"><div class="ref-icon" style="background:rgba(67,233,123,0.15);color:#43e97b;">🚀</div>NVMe SSD 固态硬盘</div>';
    html += '<div class="ref-metric"><span class="ref-label">顺序读速度</span><span class="ref-value">2000 ~ 7000 MB/s</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">顺序写速度</span><span class="ref-value">1500 ~ 5000 MB/s</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机读 IOPS (4K)</span><span class="ref-value">200K ~ 1000K+</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机写 IOPS (4K)</span><span class="ref-value">100K ~ 500K+</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机读 IOPS (16K)</span><span class="ref-value">100K ~ 500K</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机写 IOPS (16K)</span><span class="ref-value">50K ~ 300K</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机读速度 (16K)</span><span class="ref-value">1500 ~ 5000 MB/s</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">随机写速度 (16K)</span><span class="ref-value">780 ~ 3500 MB/s</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">平均读延迟</span><span class="ref-value">10 ~ 100 μs</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">平均写延迟</span><span class="ref-value">20 ~ 200 μs</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">P99 延迟</span><span class="ref-value">50 ~ 500 μs</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">接口类型</span><span class="ref-value">PCIe Gen3/Gen4/Gen5</span></div>';
    html += '<div class="ref-metric"><span class="ref-label">接口带宽上限</span><span class="ref-value">Gen3: ~3.5GB/s · Gen4: ~7GB/s</span></div>';
    html += '<div style="margin-top:12px;padding:10px;background:rgba(67,233,123,0.08);border-radius:8px;font-size:11px;color:var(--text-muted);line-height:1.6;">';
    html += '<strong>适用场景：</strong>TDSQL 生产环境数据盘首选，高并发 OLTP 场景，对延迟敏感的核心业务。<br>';
    html += '<strong>推荐：</strong>TDSQL 数据盘强烈建议使用 NVMe SSD，可显著提升数据库读写性能和响应时间。企业级 NVMe（如 Intel P4610/P5510、Samsung PM9A3）具有更好的一致性和耐久度。';
    html += '</div>';
    html += '</div>';

    html += '</div>'; // ref-grid

    // 综合对比表
    html += '<div style="margin-top:24px;">';
    html += '<div style="font-size:15px;font-weight:600;margin-bottom:14px;display:flex;align-items:center;gap:8px;">📊 三种磁盘类型综合对比（16K Block Size）</div>';
    html += '<div class="summary-table-wrapper"><table class="summary-table"><thead><tr>';
    html += '<th>指标</th><th>💿 HDD (7200RPM)</th><th>💾 SATA SSD</th><th>🚀 NVMe SSD</th><th>说明</th>';
    html += '</tr></thead><tbody>';
    html += '<tr><td><strong>顺序写速度</strong></td><td>80~180 MB/s</td><td>300~530 MB/s</td><td style="color:var(--success);font-weight:600;">1500~5000 MB/s</td><td>NVMe 是 HDD 的 20~60 倍</td></tr>';
    html += '<tr><td><strong>随机读 IOPS</strong></td><td style="color:var(--danger);">50~120</td><td>20K~70K</td><td style="color:var(--success);font-weight:600;">100K~500K</td><td>数据库核心指标，差距巨大</td></tr>';
    html += '<tr><td><strong>随机写 IOPS</strong></td><td style="color:var(--danger);">50~100</td><td>15K~50K</td><td style="color:var(--success);font-weight:600;">50K~300K</td><td>影响事务提交速度</td></tr>';
    html += '<tr><td><strong>随机读速度</strong></td><td style="color:var(--danger);">0.8~1.9 MB/s</td><td>312~560 MB/s</td><td style="color:var(--success);font-weight:600;">1500~5000 MB/s</td><td>IOPS × 16K，受接口带宽上限约束</td></tr>';
    html += '<tr><td><strong>随机写速度</strong></td><td style="color:var(--danger);">0.8~1.6 MB/s</td><td>234~530 MB/s</td><td style="color:var(--success);font-weight:600;">780~3500 MB/s</td><td>影响 redo log / binlog 写入效率</td></tr>';
    html += '<tr><td><strong>平均读延迟</strong></td><td style="color:var(--danger);">5~15 ms</td><td>50~200 μs</td><td style="color:var(--success);font-weight:600;">10~100 μs</td><td>NVMe 延迟仅为 HDD 的 1/100</td></tr>';
    html += '<tr><td><strong>平均写延迟</strong></td><td style="color:var(--danger);">5~15 ms</td><td>100~500 μs</td><td style="color:var(--success);font-weight:600;">20~200 μs</td><td>低延迟对 OLTP 至关重要</td></tr>';
    html += '<tr><td><strong>P99 延迟</strong></td><td style="color:var(--danger);">10~30 ms</td><td>0.5~2 ms</td><td style="color:var(--success);font-weight:600;">50~500 μs</td><td>尾部延迟影响用户体验</td></tr>';
    html += '<tr><td><strong>顺序读带宽</strong></td><td>100~200 MB/s</td><td>400~560 MB/s</td><td style="color:var(--success);font-weight:600;">2000~7000 MB/s</td><td>影响全表扫描和备份速度</td></tr>';
    html += '<tr><td><strong>TDSQL 推荐</strong></td><td><span class="perf-badge poor">不推荐</span></td><td><span class="perf-badge fair">可用</span></td><td><span class="perf-badge excellent">强烈推荐</span></td><td>数据盘建议 NVMe，安装盘可用 SSD</td></tr>';
    html += '</tbody></table></div>';
    html += '</div>';

    // TDSQL 磁盘建议
    html += '<div style="margin-top:20px;padding:18px;background:linear-gradient(135deg,rgba(99,102,241,0.08),rgba(139,92,246,0.08));border-radius:var(--radius-md);border:1px solid rgba(99,102,241,0.2);">';
    html += '<div style="font-size:14px;font-weight:600;margin-bottom:10px;">💡 TDSQL 磁盘配置建议</div>';
    html += '<div style="font-size:12px;color:var(--text-secondary);line-height:1.8;">';
    html += '<div style="margin-bottom:6px;">• <strong>/data 安装目录盘：</strong>建议使用 SATA SSD 或 NVMe SSD，顺序写 ≥ 300MB/s，随机读 IOPS ≥ 5K</div>';
    html += '<div style="margin-bottom:6px;">• <strong>/data1 数据盘：</strong>强烈建议使用 NVMe SSD，随机读 IOPS ≥ 50K，随机写 IOPS ≥ 30K，Avg 延迟 ≤ 200μs</div>';
    html += '<div style="margin-bottom:6px;">• <strong>RAID 配置：</strong>建议 RAID 10 以获得最佳读写性能和数据安全性，避免使用 RAID 5/6（写惩罚严重）</div>';
    html += '<div style="margin-bottom:6px;">• <strong>文件系统：</strong>推荐 XFS 或 EXT4，挂载参数建议添加 noatime,nobarrier（SSD 场景）</div>';
    html += '<div>• <strong>I/O 调度器：</strong>NVMe 建议使用 none/mq-deadline，SATA SSD 建议使用 deadline/mq-deadline</div>';
    html += '</div></div>';

    container.innerHTML = html;
}

// ============================================================================
// 初始化
// ============================================================================
document.addEventListener('DOMContentLoaded', function() {
    renderOverview();
    renderDetail();
    renderCharts();
    renderLatency();
    renderReference();
});
JS_REFERENCE

cat >> "$REPORT_FILE" <<'HTML_END'
</script>
</body>
</html>
HTML_END

echo ""
echo "[INFO] HTML 报告已生成: ${REPORT_FILE}"
echo "[INFO] 请在浏览器中打开查看"
