"""
TDSQL SQL审核工具 - 审核报告PDF导出服务

使用 reportlab 生成专业的审核报告PDF，包含：
- 审核摘要（通过率、统计信息）
- 违规详情（规则ID、严重级别、描述）
- 优化建议（修复方案、优化后SQL）
"""
import json
import logging
import os
from datetime import datetime
from io import BytesIO
from typing import Optional

from backend.config import REPORT_OUTPUT_DIR

logger = logging.getLogger("tdsql.report")

# ── reportlab 导入 ──
try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm, mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

from backend.services.database import _get_connection

# ── 中文字体注册 ──
_font_registered = False
_registered_font_path = ""


def _find_fontconfig_fonts() -> list[str]:
    """使用 fontconfig (fc-list) 查找系统中文字体"""
    import subprocess
    try:
        result = subprocess.run(
            ["fc-list", ":lang=zh", "-f", "%{file}\n"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            fonts = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
            logger.debug(f"fc-list 找到 {len(fonts)} 个中文字体")
            return fonts
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return []


def _scan_directory_for_fonts(directory: str, extensions: tuple) -> list[str]:
    """递归扫描目录查找字体文件"""
    found = []
    try:
        for root, _, files in os.walk(directory):
            for fname in files:
                if fname.lower().endswith(extensions):
                    found.append(os.path.join(root, fname))
    except (OSError, PermissionError):
        pass
    return found


def _register_chinese_font():
    """注册中文字体（尝试多个策略）"""
    global _font_registered, _registered_font_path
    if _font_registered:
        return

    if not HAS_REPORTLAB:
        return

    candidates: list[str] = []

    # 策略1: 使用 fontconfig 查找系统中的中文字体（Linux/macOS）
    if os.name != "nt":
        candidates.extend(_find_fontconfig_fonts())

    # 策略2: 常见字体路径（Windows）
    if os.name == "nt":
        windows_fonts_dir = "C:/Windows/Fonts"
        candidates.extend([
            os.path.join(windows_fonts_dir, "msyh.ttc"),
            os.path.join(windows_fonts, "simsun.ttc"),
            os.path.join(windows_fonts, "simhei.ttf"),
            os.path.join(windows_fonts, "msyhbd.ttc"),
            os.path.join(windows_fonts, "wingding.ttf"),
        ])
        # 扫描 Fonts 目录下的所有 .ttf/.ttc 文件
        candidates.extend(_scan_directory_for_fonts(windows_fonts_dir, (".ttf", ".ttc", ".otf")))

    # 策略3: 常见字体路径（Linux）
    linux_dirs = [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"),
    ]
    for ldir in linux_dirs:
        candidates.extend(_scan_directory_for_fonts(ldir, (".ttf", ".ttc", ".otf")))

    # 策略4: macOS 字体目录
    if os.name == "posix":
        candidates.extend([
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/Library/Fonts/PingFang.ttc",
        ])
        candidates.extend(_scan_directory_for_fonts("/System/Library/Fonts", (".ttf", ".ttc", ".otf")))

    # 去重 + 过滤不存在的文件
    seen: set[str] = set()
    for fp in candidates:
        if fp and fp not in seen and Path(fp).exists():
            seen.add(fp)
            candidates.append(fp)

    # 注册第一个成功的字体
    for font_path in candidates:
        try:
            pdfmetrics.registerFont(TTFont("ChineseFont", font_path))
            _font_registered = True
            _registered_font_path = font_path
            logger.info(f"已注册中文字体: {font_path}")
            return
        except Exception as e:
            logger.debug(f"字体注册失败 {font_path}: {e}")
            continue

    # 策略5: 回退到 Helvetica（不注册中文字体）
    # Helvetica 在 reportlab 内置，但在不支持 CJK 的 PDF viewer 中中文会显示为方块
    # 此处仍使用 Helvetica，PDF 生成不会报错，但中文会显示异常
    logger.warning(
        "未找到任何中文字体，PDF中的中文可能显示为方块。"
        "建议安装: pip install reportlab[utf8] 或系统安装 fonts-noto-cjk"
    )


def _get_styles() -> dict:
    """获取PDF样式"""
    _register_chinese_font()
    styles = getSampleStyleSheet()
    font_name = "ChineseFont" if _font_registered else "Helvetica"

    custom_styles = {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=styles["Title"],
            fontName=font_name,
            fontSize=18,
            spaceAfter=12,
            alignment=TA_CENTER,
        ),
        "heading": ParagraphStyle(
            "ReportHeading",
            parent=styles["Heading2"],
            fontName=font_name,
            fontSize=14,
            spaceBefore=16,
            spaceAfter=8,
            textColor=colors.HexColor("#1a5276"),
        ),
        "subheading": ParagraphStyle(
            "ReportSubHeading",
            parent=styles["Heading3"],
            fontName=font_name,
            fontSize=12,
            spaceBefore=10,
            spaceAfter=6,
            textColor=colors.HexColor("#2c3e50"),
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=10,
            spaceAfter=6,
            leading=16,
        ),
        "body_center": ParagraphStyle(
            "ReportBodyCenter",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=10,
            alignment=TA_CENTER,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "ReportSmall",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=8,
            textColor=colors.grey,
        ),
        "code": ParagraphStyle(
            "ReportCode",
            parent=styles["Normal"],
            fontName="Courier",
            fontSize=9,
            spaceAfter=6,
            leftIndent=20,
            backColor=colors.HexColor("#f8f9fa"),
        ),
    }
    return custom_styles


def _get_severity_color(severity: str) -> colors.Color:
    """获取严重级别对应的颜色"""
    color_map = {
        "ERROR": colors.HexColor("#e74c3c"),
        "WARNING": colors.HexColor("#f39c12"),
        "INFO": colors.HexColor("#3498db"),
    }
    return color_map.get(severity, colors.grey)


def _get_severity_text(severity: str) -> str:
    """获取严重级别的中文描述"""
    text_map = {
        "ERROR": "错误",
        "WARNING": "警告",
        "INFO": "提示",
    }
    return text_map.get(severity, severity)


def generate_audit_report_pdf(
    report_id: int,
    output_path: Optional[str] = None,
) -> tuple[bytes, str]:
    """
    生成审核报告PDF。

    Args:
        report_id: audit_history 表的 ID
        output_path: 可选的文件输出路径，为 None 时返回字节流

    Returns:
        (PDF字节内容, 文件名)
    """
    if not HAS_REPORTLAB:
        raise ImportError("reportlab 未安装，请执行: pip install reportlab")

    # 查询审核历史记录
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM audit_history WHERE id = ?", (report_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise ValueError(f"审核记录不存在: id={report_id}")

    record = dict(row)
    results_json = record.get("results_json", "[]")

    try:
        results = json.loads(results_json)
    except json.JSONDecodeError:
        results = []

    # 生成PDF
    styles = _get_styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    elements = []

    # ── 标题 ──
    elements.append(Paragraph("TDSQL SQL审核报告", styles["title"]))
    elements.append(Spacer(1, 6 * mm))

    # ── 报告信息表 ──
    created_at = record.get("created_at", "")
    report_info = [
        ["报告ID", str(report_id), "审核类型", record.get("audit_type", "")],
        ["生成时间", created_at, "来源", record.get("source", "N/A")],
    ]
    info_table = Table(report_info, colWidths=[3 * cm, 5 * cm, 3 * cm, 5 * cm])
    info_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "ChineseFont" if _font_registered else "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eaf2f8")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#eaf2f8")),
        ("FONTSIZE", (0, 0), (0, -1), 9),
        ("FONTSIZE", (2, 0), (2, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 8 * mm))

    # ── 审核摘要 ──
    elements.append(Paragraph("一、审核摘要", styles["heading"]))

    total_sql = record.get("total_sql", 0)
    passed = record.get("passed", 0)
    failed = record.get("failed", 0)
    error_count = record.get("error_count", 0)
    warning_count = record.get("warning_count", 0)
    pass_rate = record.get("pass_rate", 0)

    summary_data = [
        ["指标", "数值", "指标", "数值"],
        ["SQL总数", str(total_sql), "通过数", str(passed)],
        ["未通过数", str(failed), "通过率", f"{pass_rate:.1f}%"],
        ["ERROR级别", str(error_count), "WARNING级别", str(warning_count)],
    ]
    summary_table = Table(summary_data, colWidths=[3.5 * cm, 4 * cm, 3.5 * cm, 4 * cm])
    summary_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "ChineseFont" if _font_registered else "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 8 * mm))

    # ── 违规详情 ──
    violations = []
    for result in results:
        for v in result.get("violations", []):
            violations.append({
                "sql": result.get("sql", "")[:100],
                "sql_type": result.get("sql_type", ""),
                **v,
            })

    if violations:
        elements.append(Paragraph("二、违规详情", styles["heading"]))
        elements.append(
            Paragraph(f"共发现 <b>{len(violations)}</b> 个违规项：", styles["body"])
        )
        elements.append(Spacer(1, 4 * mm))

        for idx, v in enumerate(violations, 1):
            severity = v.get("severity", "INFO")
            severity_color = _get_severity_color(severity)
            severity_text = _get_severity_text(severity)
            rule_id = v.get("rule_id", "")
            message = v.get("message", "")
            suggestion = v.get("suggestion", "")
            sql_snippet = v.get("sql", "")

            # 违规标题
            elements.append(Paragraph(
                f'<font color="{severity_color.hexval()}">[{severity_text}]</font> '
                f'<b>{rule_id}</b> - {message}',
                styles["body"],
            ))

            # SQL片段
            if sql_snippet:
                elements.append(Paragraph(
                    f"相关SQL: {sql_snippet}",
                    styles["small"],
                ))

            # 优化建议
            if suggestion:
                elements.append(Paragraph(
                    f"优化建议: {suggestion}",
                    styles["body"],
                ))

            if idx < len(violations):
                elements.append(Spacer(1, 3 * mm))
    else:
        elements.append(Paragraph("二、违规详情", styles["heading"]))
        elements.append(Paragraph("未发现违规项，所有SQL均通过审核。", styles["body"]))

    elements.append(Spacer(1, 8 * mm))

    # ── 优化建议汇总 ──
    suggestions = [
        v for v in violations if v.get("suggestion")
    ]

    if suggestions:
        elements.append(Paragraph("三、优化建议汇总", styles["heading"]))

        suggestion_data = [["序号", "规则", "严重级别", "优化建议"]]
        for idx, v in enumerate(suggestions, 1):
            suggestion_data.append([
                str(idx),
                v.get("rule_id", ""),
                _get_severity_text(v.get("severity", "INFO")),
                v.get("suggestion", "")[:80],
            ])

        sg_table = Table(
            suggestion_data,
            colWidths=[1.5 * cm, 2 * cm, 2 * cm, 9.5 * cm],
        )
        sg_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "ChineseFont" if _font_registered else "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#27ae60")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (2, -1), "CENTER"),
            ("ALIGN", (3, 0), (3, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
        ]))
        elements.append(sg_table)
    else:
        elements.append(Paragraph("三、优化建议汇总", styles["heading"]))
        elements.append(Paragraph("无需优化建议。", styles["body"]))

    elements.append(Spacer(1, 12 * mm))

    # ── 页脚信息 ──
    elements.append(Paragraph(
        f"报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"TDSQL SQL审核工具 v0.4.0",
        styles["small"],
    ))

    # 构建PDF
    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    # 生成文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"TDSQL_审核报告_{report_id}_{timestamp}.pdf"

    # 保存到文件（可选）
    if output_path:
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(pdf_bytes)
    else:
        # 默认保存一份到 reports 目录
        report_file = REPORT_OUTPUT_DIR / filename
        report_file.write_bytes(pdf_bytes)

    return pdf_bytes, filename


def generate_slow_query_report_pdf(
    slow_id: int,
) -> tuple[bytes, str]:
    """
    生成慢SQL分析报告PDF。

    Args:
        slow_id: slow_queries 表的 ID

    Returns:
        (PDF字节内容, 文件名)
    """
    if not HAS_REPORTLAB:
        raise ImportError("reportlab 未安装，请执行: pip install reportlab")

    # 查询慢SQL记录
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM slow_queries WHERE id = ?", (slow_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise ValueError(f"慢SQL记录不存在: id={slow_id}")

    record = dict(row)
    analyses = []
    if record.get("analysis_json"):
        try:
            analyses = json.loads(record["analysis_json"])
        except json.JSONDecodeError:
            pass

    styles = _get_styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )

    elements = []

    # 标题
    elements.append(Paragraph("TDSQL 慢SQL分析报告", styles["title"]))
    elements.append(Spacer(1, 6 * mm))

    # 基本信息
    elements.append(Paragraph("一、基本信息", styles["heading"]))
    info_data = [
        ["指标", "数值", "指标", "数值"],
        ["记录ID", str(slow_id), "数据库", record.get("db_name", "")],
        ["执行次数", str(record.get("exec_count", 0)), "严重级别", record.get("severity", "")],
        ["平均耗时", f"{record.get('avg_time_ms', 0):.1f}ms", "最大耗时", f"{record.get('max_time_ms', 0):.1f}ms"],
        ["扫描行数", str(record.get("rows_examined", 0)), "返回行数", str(record.get("rows_sent", 0))],
        ["问题类型", record.get("problem_type", ""), "状态", record.get("status", "")],
    ]
    info_table = Table(info_data, colWidths=[3.5 * cm, 4 * cm, 3.5 * cm, 4 * cm])
    info_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "ChineseFont" if _font_registered else "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 6 * mm))

    # SQL文本
    elements.append(Paragraph("二、SQL文本", styles["heading"]))
    sql_text = record.get("sql_text", "")
    # 截断过长SQL
    if len(sql_text) > 500:
        sql_text = sql_text[:500] + "..."
    elements.append(Paragraph(sql_text, styles["code"]))
    elements.append(Spacer(1, 6 * mm))

    # 分析结果
    if analyses:
        elements.append(Paragraph("三、分析结果", styles["heading"]))
        for idx, analysis in enumerate(analyses, 1):
            severity = analysis.get("severity", "INFO")
            elements.append(Paragraph(
                f'<font color="{_get_severity_color(severity).hexval()}">'
                f'[{_get_severity_text(severity)}]</font> '
                f'<b>{analysis.get("problem_type", "")}</b>',
                styles["body"],
            ))
            if analysis.get("description"):
                elements.append(Paragraph(f"描述: {analysis['description']}", styles["body"]))
            if analysis.get("evidence"):
                elements.append(Paragraph(f"证据: {analysis['evidence']}", styles["body"]))
            if analysis.get("root_cause"):
                elements.append(Paragraph(f"根因: {analysis['root_cause']}", styles["body"]))
            if analysis.get("suggestion"):
                elements.append(Paragraph(f"建议: {analysis['suggestion']}", styles["body"]))
            if analysis.get("optimized_sql"):
                elements.append(Paragraph(f"优化后SQL:", styles["subheading"]))
                elements.append(Paragraph(analysis["optimized_sql"], styles["code"]))
            if idx < len(analyses):
                elements.append(Spacer(1, 4 * mm))
    else:
        elements.append(Paragraph("三、分析结果", styles["heading"]))
        elements.append(Paragraph("暂无分析结果。", styles["body"]))

    # 根因与建议
    if record.get("root_cause") or record.get("suggestion"):
        elements.append(Spacer(1, 6 * mm))
        elements.append(Paragraph("四、优化建议", styles["heading"]))
        if record.get("root_cause"):
            elements.append(Paragraph(f"根因: {record['root_cause']}", styles["body"]))
        if record.get("suggestion"):
            elements.append(Paragraph(f"建议: {record['suggestion']}", styles["body"]))
        if record.get("optimized_sql"):
            elements.append(Paragraph(f"优化后SQL:", styles["subheading"]))
            elements.append(Paragraph(record["optimized_sql"], styles["code"]))

    # 页脚
    elements.append(Spacer(1, 12 * mm))
    elements.append(Paragraph(
        f"报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"TDSQL SQL审核工具 v0.4.0",
        styles["small"],
    ))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"TDSQL_慢SQL报告_{slow_id}_{timestamp}.pdf"

    # 保存一份到 reports 目录
    report_file = REPORT_OUTPUT_DIR / filename
    report_file.write_bytes(pdf_bytes)

    return pdf_bytes, filename
