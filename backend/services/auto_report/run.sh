#!/usr/bin/env bash
# TDSQL 运维汇报 PPT 一键生成脚本
#
# 用法:
#   ./run.sh [选项]
#
# 选项:
#   --inspection-csv <path>   指定巡检 CSV
#   --report-date <date>      报告日期 (YYYY-MM-DD)
#   --report-title <title>    报告标题
#   --output <name>           输出 PPT 文件名 (不含路径，放在 output/ 下)
#   -h, --help                帮助

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOLKIT_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="$SCRIPT_DIR/output"

# 默认参数
REPORT_DATE="$(date +%Y-%m-%d)"
REPORT_TITLE="陕西农信核心 TDSQL 数据库主动运维报告"
OUTPUT_NAME=""
EXTRA_ARGS=()

# 解析参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --inspection-csv)  EXTRA_ARGS+=(--inspection-csv "$2"); shift 2 ;;
        --report-date)     REPORT_DATE="$2"; shift 2 ;;
        --report-title)    REPORT_TITLE="$2"; shift 2 ;;
        --output)          OUTPUT_NAME="$2"; shift 2 ;;
        -h|--help)
            head -15 "$0" | tail -12
            exit 0
            ;;
        *)
            echo "未知参数: $1"
            exit 1
            ;;
    esac
done

if [[ -z "$OUTPUT_NAME" ]]; then
    OUTPUT_NAME="report_${REPORT_DATE}.pptx"
fi

mkdir -p "$OUTPUT_DIR"

echo "╔═══════════════════════════════════════════════════════╗"
echo "║     TDSQL 运维汇报 PPT 自动生成                      ║"
echo "╚═══════════════════════════════════════════════════════╝"
echo ""
echo "  报告日期: $REPORT_DATE"
echo "  报告标题: $REPORT_TITLE"
echo "  输出文件: $OUTPUT_DIR/$OUTPUT_NAME"
echo ""

# Step 1: 数据采集
echo "━━━ Step 1/2: 采集数据 ━━━"
python3 "$SCRIPT_DIR/collect_report_data.py" \
    --report-date "$REPORT_DATE" \
    --report-title "$REPORT_TITLE" \
    -o "$OUTPUT_DIR/report_data.json" \
    "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"

echo ""

# Step 2: 生成 PPT
echo "━━━ Step 2/2: 生成 PPT ━━━"
NODE_PATH="$(npm root -g)" node "$SCRIPT_DIR/generate_pptx.js" \
    "$OUTPUT_DIR/report_data.json" \
    "$OUTPUT_DIR/$OUTPUT_NAME"

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  完成！PPT 已生成: $OUTPUT_DIR/$OUTPUT_NAME"
echo "═══════════════════════════════════════════════════════"
