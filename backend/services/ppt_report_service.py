"""G12 运维汇报 PPT 生成与大屏服务

负责从元数据库中读取各项指标，拼装成 auto_report 模块所需的 JSON 格式，
并调用 node generate_pptx.js 一键生成 PPTX 文件。同时提供大屏统计面板数据。
"""
import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from collections import defaultdict
import io
import html
from backend.services.database import _get_connection, _execute_sql

logger = logging.getLogger("tdsql.ppt_report")


class PPTReportService:
    """PPT 汇报与大屏服务"""

    def generate_report_data(self, connection_id: str) -> dict:
        """从元数据库提取各项数据，拼装为 generate_pptx.js 所需的 JSON"""
        conn = _get_connection()
        try:
            # 1. 每日巡检数据 daily_inspection
            insp_data = self._get_inspection_data(conn, connection_id)

            # 2. 大表分析数据 count_table_rows / bigtable_history
            table_data = self._get_table_rows_data(conn, connection_id)

            # 3. 索引分析数据 index_analysis
            index_data = self._get_index_analysis_data(conn, connection_id)

            # 4. 慢查询分析数据 sql_analysis
            sql_data = self._get_sql_analysis_data(conn, connection_id)

            # 5. 网关日志分析数据 gateway_analysis
            gateway_data = self._get_gateway_analysis_data(conn, connection_id)

            # 6. 表结构对比数据 schema_diff
            schema_diff_data = self._get_schema_diff_data(conn, connection_id)

            # 获取服务/连接名称
            cursor = conn.cursor()
            cursor.execute("SELECT name AS service_name, host, port FROM tdsql_connections WHERE id = %s", (connection_id,))
            conn_info = cursor.fetchone()
            service_name = conn_info["service_name"] if conn_info else connection_id
            if not service_name:
                service_name = f"{conn_info['host']}:{conn_info['port']}" if conn_info else connection_id

            report_title = f"{service_name} TDSQL 数据库运维报告"

            return {
                "meta": {
                    "title": report_title,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "generator": "tdsql-toolkit/auto_report"
                },
                "modules": {
                    "daily_inspection": insp_data,
                    "count_table_rows": table_data,
                    "index_analysis": index_data,
                    "sql_analysis": sql_data,
                    "gateway_analysis": gateway_data,
                    "schema_diff": schema_diff_data
                }
            }
        finally:
            conn.close()

    def generate_pdf(self, connection_id: str) -> bytes:
        """
        从元数据库提取数据，并使用 ReportLab 动态生成 PDF 诊断报告的二进制流。
        """
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont

        try:
            pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        except Exception as e:
            logger.warning(f"Failed to register STSong-Light: {e}")

        # 获取数据
        data = self.generate_report_data(connection_id)
        meta = data.get("meta", {})
        modules = data.get("modules", {})

        # 初始化文档
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36
        )

        styles = getSampleStyleSheet()
        
        # 统一字体样式
        title_style = ParagraphStyle(
            'DocTitle',
            parent=styles['Normal'],
            fontName='STSong-Light',
            fontSize=22,
            leading=28,
            textColor=colors.HexColor('#0F172A'),
            alignment=1, # 居中
            spaceAfter=15
        )

        meta_style = ParagraphStyle(
            'DocMeta',
            parent=styles['Normal'],
            fontName='STSong-Light',
            fontSize=10,
            leading=14,
            textColor=colors.HexColor('#64748B'),
            alignment=1,
            spaceAfter=30
        )

        h1_style = ParagraphStyle(
            'SectionH1',
            parent=styles['Normal'],
            fontName='STSong-Light',
            fontSize=14,
            leading=18,
            textColor=colors.HexColor('#1E293B'),
            spaceBefore=12,
            spaceAfter=8,
            keepWithNext=True
        )

        h2_style = ParagraphStyle(
            'SectionH2',
            parent=styles['Normal'],
            fontName='STSong-Light',
            fontSize=11,
            leading=14,
            textColor=colors.HexColor('#3B82F6'),
            spaceBefore=8,
            spaceAfter=4,
            keepWithNext=True
        )

        body_style = ParagraphStyle(
            'BodyTextChinese',
            parent=styles['Normal'],
            fontName='STSong-Light',
            fontSize=9.5,
            leading=13,
            textColor=colors.HexColor('#334155'),
            spaceAfter=5
        )

        table_header_style = ParagraphStyle(
            'TableHeader',
            parent=styles['Normal'],
            fontName='STSong-Light',
            fontSize=9,
            leading=12,
            textColor=colors.white,
            alignment=1
        )

        table_body_style = ParagraphStyle(
            'TableBody',
            parent=styles['Normal'],
            fontName='STSong-Light',
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor('#334155')
        )

        story = []

        def clean(t):
            if t is None:
                return ""
            return html.escape(str(t))

        def p(text, style=body_style):
            return Paragraph(clean(text), style)

        # ── 1. 封面标题页 ──
        story.append(Spacer(1, 20))
        story.append(Paragraph(meta.get("title") or "TDSQL 数据库主动运维报告", title_style))
        story.append(Paragraph(f"报告生成时间: {meta.get('generated_at')}  |  分析引擎: {meta.get('generator')}", meta_style))
        story.append(Spacer(1, 10))

        # ── 2. 指标概览大屏 (Summary Card Table) ──
        dash = self.get_dashboard_data(connection_id)
        story.append(Paragraph("一、 实例运维健康总览", h1_style))
        # v1.6.2.2-UAT-O-05-R3：未采集数据时不得声称"深度扫描后"
        if dash.get("score_status") == "no_data":
            story.append(Paragraph("本实例尚未完成运维数据采集，本报告仅列示当前可用信息，不做健康评分。", body_style))
        else:
            story.append(Paragraph(f"针对实例进行多维度深度扫描后，综合评估得分如下所示：", body_style))
        
        summary_data = [
            # v1.6.2.2-UAT-O-05：score=None 表示无采集数据（未评估），不得虚构健康分
            [Paragraph("<b>健康评分 (基准100分)</b>", body_style), Paragraph("<b>未评估（无采集数据）</b>" if dash['score'] is None else (f"<font color='#EF4444'><b>{dash['score']} 分</b></font>" if dash['score'] < 80 else f"<font color='#10B981'><b>{dash['score']} 分</b></font>"), body_style)],
            [Paragraph("活动告警总数", body_style), Paragraph(str(dash['total_alerts']), body_style)],
            [Paragraph("索引问题总数", body_style), Paragraph(str(dash.get('index', {}).get('summary', {}).get('duplicate_count', 0) + dash.get('index', {}).get('summary', {}).get('prefix_redundant_count', 0)), body_style)],
            [Paragraph("全表扫描慢SQL数", body_style), Paragraph(str(dash.get('sql', {}).get('summary', {}).get('full_scan_count', 0)), body_style)],
        ]
        
        t_summary = Table(summary_data, colWidths=[200, 320])
        t_summary.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
            ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#CBD5E1')),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 12),
        ]))
        story.append(t_summary)
        story.append(Spacer(1, 12))

        # ── 3. 每日巡检数据 (Daily Inspection) ──
        story.append(Paragraph("二、 周期性日常巡检分析", h1_style))
        insp = modules.get("daily_inspection")
        if insp and insp.get("history"):
            story.append(Paragraph(f"系统记录了最近的巡检情况，关键巡检指标和告警详情如下：", body_style))
            hist_rows = [
                [Paragraph("<b>巡检时间</b>", table_header_style), Paragraph("<b>健康分数</b>", table_header_style), Paragraph("<b>告警指标</b>", table_header_style), Paragraph("<b>待优化建议</b>", table_header_style)]
            ]
            for h in insp.get("history", [])[:5]:
                hist_rows.append([
                    Paragraph(h.get("inspect_time", ""), table_body_style),
                    Paragraph(f"{h.get('score', 100)} 分", table_body_style),
                    Paragraph(str(h.get("alert_count", 0)), table_body_style),
                    Paragraph(h.get("suggestion", "") or "暂无", table_body_style)
                ])
            t_insp = Table(hist_rows, colWidths=[120, 80, 80, 240])
            t_insp.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E293B')),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
                ('LEFTPADDING', (0,0), (-1,-1), 6),
                ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ]))
            story.append(t_insp)
        else:
            story.append(Paragraph("暂无相关周期性巡检历史记录。", body_style))
        story.append(Spacer(1, 12))

        # ── 4. 数据容量与大表治理 (Large Tables) ──
        story.append(Paragraph("三、 数据库大表与存储卷分析", h1_style))
        table_rows = modules.get("count_table_rows")
        if table_rows and table_rows.get("tables_top20"):
            top20 = table_rows.get("tables_top20", [])
            story.append(Paragraph(f"目前数据库已分析大表（按照表数据量大小排名前 10 位）：", body_style))
            table_head = [
                [Paragraph("<b>数据库名</b>", table_header_style), Paragraph("<b>数据表名</b>", table_header_style), Paragraph("<b>总行数</b>", table_header_style), Paragraph("<b>物理大小 (GB)</b>", table_header_style)]
            ]
            for t in top20[:10]:
                table_head.append([
                    Paragraph(t.get("database", ""), table_body_style),
                    Paragraph(t.get("table", ""), table_body_style),
                    Paragraph(f"{t.get('row_count', 0):,}", table_body_style),
                    Paragraph(f"{t.get('size_gb', 0.0):.2f}", table_body_style)
                ])
            t_tables = Table(table_head, colWidths=[120, 160, 110, 130])
            t_tables.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E293B')),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ]))
            story.append(t_tables)
        else:
            story.append(Paragraph("未检测到大表存储分析历史。", body_style))
        story.append(Spacer(1, 12))

        # ── 5. 索引健康审计 (Index Audit) ──
        story.append(Paragraph("四、 索引审计与冗余分析", h1_style))
        idx_data = modules.get("index_analysis")
        if idx_data and idx_data.get("duplicate_indexes"):
            story.append(Paragraph(f"共发现 {idx_data.get('summary', {}).get('duplicate_count', 0)} 处完全重复索引，应尽快清理以节省空间和写入开销：", body_style))
            dup_head = [
                [Paragraph("<b>库名</b>", table_header_style), Paragraph("<b>表名</b>", table_header_style), Paragraph("<b>索引A</b>", table_header_style), Paragraph("<b>索引B (重复)</b>", table_header_style), Paragraph("<b>包含列</b>", table_header_style)]
            ]
            for d in idx_data.get("duplicate_indexes", [])[:10]:
                dup_head.append([
                    Paragraph(d.get("schema", ""), table_body_style),
                    Paragraph(d.get("table", ""), table_body_style),
                    Paragraph(d.get("index1", ""), table_body_style),
                    Paragraph(d.get("index2", ""), table_body_style),
                    Paragraph(d.get("columns", ""), table_body_style)
                ])
            t_dup = Table(dup_head, colWidths=[80, 100, 100, 100, 140])
            t_dup.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E293B')),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ]))
            story.append(t_dup)
        else:
            # v1.6.2.2-UAT-O-05-R3：健康结论必须以采集状态为前提——
            # 只有真实完成采集且零问题（completed_empty）才允许“未发现/健康”结论；
            # 未执行（no_data）必须明说未采集，不得用空数组冒充健康。
            _rs = (idx_data or {}).get("run_status", "")
            if (idx_data or {}).get("data_status") != "ok":
                story.append(Paragraph("索引体检尚未执行采集，本项未评估。", body_style))
            elif _rs == "completed_empty":
                story.append(Paragraph("本次索引体检已覆盖全部在采范围，未发现冗余或完全重复的索引。", body_style))
            else:
                story.append(Paragraph("本次索引体检未发现完全重复索引（其他类型发现见明细）。", body_style))
        story.append(Spacer(1, 12))

        # ── 6. 慢 SQL 分析 (Slow Queries) ──
        story.append(Paragraph("五、 全集群 Top-N 慢 SQL 治理建议", h1_style))
        sql_data = modules.get("sql_analysis")
        if sql_data and sql_data.get("top_slow_sql"):
            top_sql = sql_data.get("top_slow_sql", [])
            story.append(Paragraph(f"提取出当前系统最影响 CPU 及 IO 性能的 Top 10 慢 SQL 指纹信息：", body_style))
            sql_head = [
                [Paragraph("<b>数据库</b>", table_header_style), Paragraph("<b>SQL 指纹信息 (前 80 字符)</b>", table_header_style), Paragraph("<b>执行次数</b>", table_header_style), Paragraph("<b>平均耗时 (秒)</b>", table_header_style)]
            ]
            for s in top_sql[:10]:
                finger = s.get("digest_text", "")
                if len(finger) > 80:
                    finger = finger[:80] + "..."
                sql_head.append([
                    Paragraph(s.get("schema" if "schema" in s else "db_name", ""), table_body_style),
                    Paragraph(finger, table_body_style),
                    Paragraph(f"{s.get('count' if 'count' in s else 'exec_count', 0):,}", table_body_style),
                    Paragraph(f"{s.get('avg_time' if 'avg_time' in s else 'avg_seconds', 0.0):.3f}s", table_body_style)
                ])
            t_sql = Table(sql_head, colWidths=[90, 250, 80, 100])
            t_sql.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E293B')),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ]))
            story.append(t_sql)
        else:
            story.append(Paragraph("暂无相关慢查询抓取与性能瓶颈统计。", body_style))
        story.append(Spacer(1, 12))

        # ── 7. 网关日志分析 (Gateway Log) ──
        story.append(Paragraph("六、 网关日志深度性能诊断", h1_style))
        gw = modules.get("gateway_analysis")
        if gw and gw.get("reports"):
            rpts = gw.get("reports", [])
            story.append(Paragraph(f"最近网关日志分析审计历史报告记录：", body_style))
            gw_head = [
                [Paragraph("<b>报告ID</b>", table_header_style), Paragraph("<b>分析时间</b>", table_header_style), Paragraph("<b>总请求量</b>", table_header_style), Paragraph("<b>慢查询率</b>", table_header_style), Paragraph("<b>最大耗时 (毫秒)</b>", table_header_style)]
            ]
            for r in rpts[:5]:
                slow_rate = 0.0
                if r.get("total_queries", 0) > 0:
                    slow_rate = (r.get("slow_queries", 0) / r.get("total_queries", 1)) * 100.0
                gw_head.append([
                    Paragraph(str(r.get("id", "")), table_body_style),
                    Paragraph(r.get("analysis_time", ""), table_body_style),
                    Paragraph(f"{r.get('total_queries', 0):,}", table_body_style),
                    Paragraph(f"{slow_rate:.2f}%", table_body_style),
                    Paragraph(f"{r.get('max_time_ms', 0):,}", table_body_style)
                ])
            t_gw = Table(gw_head, colWidths=[70, 140, 100, 100, 110])
            t_gw.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E293B')),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ]))
            story.append(t_gw)
        else:
            story.append(Paragraph("暂无网关日志分析的存储记录。支持在深度诊断选项卡中上传 interf 日志一键编译生成。", body_style))

        # 构建 PDF 并返回
        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes

    def generate_ppt(self, connection_id: str) -> bytes:
        """
        生成 PPT 报告，返回 pptx 文件的二进制内容。
        """
        data = self.generate_report_data(connection_id)

        temp_dir = Path(tempfile.gettempdir()) / "tdsql_ppt_analysis"
        temp_dir.mkdir(parents=True, exist_ok=True)

        temp_json_file = temp_dir / f"data_{os.getpid()}_{connection_id}.json"
        temp_pptx_file = temp_dir / f"report_{os.getpid()}_{connection_id}.pptx"

        try:
            # 写入临时 JSON 文件
            temp_json_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

            # 调用 generate_pptx.js
            script_path = Path(__file__).parent / "auto_report" / "generate_pptx.js"
            if not script_path.exists():
                raise FileNotFoundError(f"PPT 生成脚本未找到: {script_path}")

            # 确保脚本有可执行权限（针对 Unix/Linux）
            if os.name != "nt":
                try:
                    os.chmod(script_path, 0o755)
                except Exception:
                    pass

            cmd = [
                "node", str(script_path),
                str(temp_json_file),
                str(temp_pptx_file)
            ]

            logger.info(f"执行 PPTX 生成命令: {' '.join(cmd)}")
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            
            if not temp_pptx_file.exists():
                logger.error(f"PPTX 生成失败. stdout={res.stdout}, stderr={res.stderr}")
                raise RuntimeError(f"PPT 生成失败: {res.stderr or '未能生成 pptx 文件'}")

            return temp_pptx_file.read_bytes()

        finally:
            if temp_json_file.exists():
                try:
                    temp_json_file.unlink()
                except Exception:
                    pass
            if temp_pptx_file.exists():
                try:
                    temp_pptx_file.unlink()
                except Exception:
                    pass

    def get_dashboard_data(self, connection_id: str) -> dict:
        """获取集群大屏总览看板数据"""
        data = self.generate_report_data(connection_id)
        
        # 汇总健康分数 (根据各项扣分，100为基准)
        # v1.6.2.2-UAT-O-05：只对 data_status=ok 的模块计分；
        # 全部模块无数据时 score=None（未评估），不得虚构一个健康分。
        score = 100
        alerts = 0
        has_real_data = False
        
        insp = data["modules"]["daily_inspection"]
        if insp and insp.get("data_status") == "ok":
            has_real_data = True
            alerts += insp["summary"]["alert_count"]
            score -= insp["summary"]["alert_count"] * 5
            
        idx = data["modules"]["index_analysis"]
        if idx and idx.get("data_status") == "ok":
            has_real_data = True
            score -= idx["summary"]["duplicate_count"] * 2
            score -= idx["summary"]["prefix_redundant_count"] * 1
            
        sql = data["modules"]["sql_analysis"]
        if sql and sql.get("data_status") == "ok":
            has_real_data = True
            score -= sql["summary"]["full_scan_count"] * 3

        if not has_real_data:
            score = None                     # 无数据不代表健康
        else:
            score = max(30, min(100, score))

        return {
            "score": score,
            "score_status": "ok" if has_real_data else "no_data",
            "total_alerts": alerts,
            "inspection": insp,
            "index": idx,
            "sql": sql,
            "gateway": data["modules"]["gateway_analysis"]
        }

    # ── 数据查询子逻辑（带 Mock 兜底，防库空报错） ─────────────────────────

    def _get_inspection_data(self, conn, connection_id: str) -> dict:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT node, cpu_peak, cpu_avg, mem_peak, conn_peak, slow_query, delay_peak, disk_peak
            FROM daily_inspection
            WHERE connection_id = %s AND inspect_date = (
                SELECT MAX(inspect_date) FROM daily_inspection WHERE connection_id = %s
            )
        """, (connection_id, connection_id))
        rows = list(cursor.fetchall())

        if not rows:
            # v1.6.2.2-UAT-O-05：空数据必须显式标记 no_data，禁止返回演示数据。
            # 假数据会让用户在正式报告里看到不存在的实例/指标并据此决策。
            return {
                "data_status": "no_data",
                "source_file": None,
                "summary": {
                    "instance_count": 0,
                    "avg_cpu": None,
                    "avg_memory": None,
                    "total_slow_queries": 0,
                    "alert_count": 0,
                    "cpu_alerts": []
                },
                "top_slow_queries": []
            }

        cpu_alerts = []
        alert_count = 0
        sum_cpu = 0.0
        sum_mem = 0.0
        sum_slow = 0.0
        
        for r in rows:
            sum_cpu += r["cpu_avg"]
            sum_mem += r["mem_peak"]
            sum_slow += r["slow_query"]
            if r["cpu_peak"] > 70:
                cpu_alerts.append({"name": r["node"], "value": r["cpu_peak"]})
                alert_count += 1
            if r["mem_peak"] > 85:
                alert_count += 1
            if r["delay_peak"] > 30:
                alert_count += 1
            if r["disk_peak"] > 90:
                alert_count += 1

        return {
            "data_status": "ok",
            "source_file": "monitordb_kv",
            "summary": {
                "instance_count": len(rows),
                "avg_cpu": sum_cpu / len(rows),
                "avg_memory": sum_mem / len(rows),
                "total_slow_queries": int(sum_slow),
                "alert_count": alert_count,
                "cpu_alerts": cpu_alerts
            },
            "top_slow_queries": sorted(
                [{"name": r["node"], "count": int(r["slow_query"])} for r in rows],
                key=lambda x: x["count"],
                reverse=True
            )[:10]
        }

    def _get_table_rows_data(self, conn, connection_id: str) -> dict:
        cursor = conn.cursor()
        # 获取最新一天的 snap_date
        cursor.execute("""
            SELECT snap_date, db_name, table_name, table_rows, size_gb 
            FROM bigtable_history
            WHERE connection_id = %s AND snap_date = (
                SELECT MAX(snap_date) FROM bigtable_history WHERE connection_id = %s
            )
            ORDER BY size_gb DESC
        """, (connection_id, connection_id))
        rows = list(cursor.fetchall())

        # 获取快照次数
        cursor.execute("SELECT COUNT(DISTINCT snap_date) as cnt FROM bigtable_history WHERE connection_id = %s", (connection_id,))
        snap_cnt = cursor.fetchone()["cnt"] or 0

        if not rows:
            # v1.6.2.2-UAT-O-05：空数据必须显式标记 no_data，禁止返回演示数据
            return {
                "data_status": "no_data",
                "summary": {
                    "total_tables": 0,
                    "total_rows": 0,
                    "snapshot_count": snap_cnt,
                    "distribution": {}
                },
                "tables_top20": []
            }

        total_rows = sum(r["table_rows"] for r in rows)
        
        # 简单做个大小分布计算
        dist = {"大表(>10GB)": 0, "中表(1-10GB)": 0, "小表(<1GB)": 0}
        for r in rows:
            sz = r["size_gb"]
            if sz >= 10:
                dist["大表(>10GB)"] += 1
            elif sz >= 1:
                dist["中表(1-10GB)"] += 1
            else:
                dist["小表(<1GB)"] += 1

        return {
            "data_status": "ok",
            "summary": {
                "total_tables": len(rows),
                "total_rows": total_rows,
                "snapshot_count": snap_cnt,
                "distribution": dist
            },
            "tables_top20": [
                {
                    "service": connection_id,
                    "database": r["db_name"],
                    "table": r["table_name"],
                    "row_count": r["table_rows"],
                    "size_gb": r["size_gb"]
                }
                for r in rows[:20]
            ]
        }

    def _get_index_analysis_data(self, conn, connection_id: str) -> dict:
        cursor = conn.cursor()
        # v1.6.2.2-UAT-O-05-R3：先读父运行记录区分采集状态——
        # “未执行”与“执行成功零问题”是两种语义，后者是真实的健康证据。
        cursor.execute("""
            SELECT id, total_tables, total_indexes, total_findings, created_at
            FROM index_audit WHERE connection_id = %s ORDER BY id DESC LIMIT 1
        """, (connection_id,))
        audit_row = cursor.fetchone()

        if not audit_row:
            return {
                "data_status": "no_data",
                "run_status": "not_run",
                "summary": {
                    "total_indexes": 0,
                    "unique_tables": 0,
                    "duplicate_count": 0,
                    "prefix_redundant_count": 0,
                    "unused_count": 0,
                    "fragmented_tables": 0,
                    "pk_count": 0,
                    "unique_count": 0,
                    "normal_count": 0
                },
                "duplicate_indexes": []
            }

        cursor.execute("""
            SELECT finding_type, db_name, table_name, index_name, metric AS columns, detail, severity
            FROM index_audit_finding
            WHERE audit_id = %s
        """, (audit_row["id"],))
        rows = list(cursor.fetchall())

        # v1.6.2.2-UAT-O-05-R3：finding_type 生产端写中文（重复索引/前缀冗余索引/...），
        # 消费端原来按英文比较导致真实问题全部漏计为 0。统一映射到机器枚举，兼容存量中文记录。
        _FT = {
            "duplicate": "duplicate", "重复索引": "duplicate",
            "prefix_redundant": "prefix_redundant", "前缀冗余索引": "prefix_redundant",
            "unused": "unused", "未使用索引": "unused",
            "fragmentation": "fragmentation", "表碎片": "fragmentation",
        }
        normalized_types = [_FT.get(r["finding_type"], r["finding_type"]) for r in rows]

        # 统计各项计数
        dup_count = sum(1 for t in normalized_types if t == "duplicate")
        prefix_count = sum(1 for t in normalized_types if t == "prefix_redundant")
        unused_count = sum(1 for t in normalized_types if t == "unused")
        frag_count = sum(1 for t in normalized_types if t == "fragmentation")

        # 重复索引列表
        dup_list = []
        for r, nt in zip(rows, normalized_types):
            if nt == "duplicate":
                # detail 格式通常为: "与 [index2] 完全重复"
                idx2 = "N/A"
                m = re.search(r"与\s+`?(\w+)`?\s+完全重复", r["detail"])
                if m:
                    idx2 = m.group(1)
                dup_list.append({
                    "schema": r["db_name"],
                    "table": r["table_name"],
                    "index1": r["index_name"],
                    "index2": idx2,
                    "columns": r["columns"]
                })

        # 索引总数/表数从父运行记录取（真实采集范围），不从 finding 数猜测。
        # 本查询查不出的分类计数（pk/unique/normal）诚实标 None。
        run_status = ("completed_with_findings" if (audit_row["total_findings"] or 0) > 0
                      else "completed_empty")
        return {
            "data_status": "ok",
            "run_status": run_status,
            "run_at": str(audit_row["created_at"]) if audit_row["created_at"] else "",
            "summary": {
                "total_indexes": audit_row["total_indexes"],
                "unique_tables": audit_row["total_tables"],
                "duplicate_count": dup_count,
                "prefix_redundant_count": prefix_count,
                "unused_count": unused_count,
                "fragmented_tables": frag_count,
                "pk_count": None,
                "unique_count": None,
                "normal_count": None
            },
            "duplicate_indexes": dup_list
        }

    def _get_sql_analysis_data(self, conn, connection_id: str) -> dict:
        cursor = conn.cursor()
        # 取慢查询排行
        cursor.execute("""
            SELECT fingerprint, db_name, exec_count, avg_time_ms, max_time_ms, rows_examined, explain_issues
            FROM slow_queries
            WHERE connection_id = %s
            ORDER BY total_time_ms DESC LIMIT 20
        """, (connection_id,))
        rows = list(cursor.fetchall())

        if not rows:
            # v1.6.2.2-UAT-O-05：空数据必须显式标记 no_data，禁止返回演示数据
            return {
                "data_status": "no_data",
                "summary": {
                    "total_sql_types": 0,
                    "total_count": 0,
                    "full_scan_count": 0,
                    "total_time": 0.0
                },
                "type_distribution": {},
                "top_slow_sql": []
            }

        full_scans = sum(1 for r in rows if "type=ALL" in (r["explain_issues"] or ""))
        total_count = sum(r["exec_count"] for r in rows)
        
        # SQL 类型分布 (简单提取第一个单词进行粗略统计)
        types = defaultdict(int)
        for r in rows:
            words = (r["fingerprint"] or "").split()
            first = words[0].upper() if words else "SELECT"
            if first in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                types[first] += r["exec_count"]
            else:
                types["OTHER"] += r["exec_count"]

        return {
            "data_status": "ok",
            "summary": {
                "total_sql_types": len(rows),
                "total_count": total_count,
                "full_scan_count": full_scans,
                "total_time": sum((r["avg_time_ms"]/1000.0) * r["exec_count"] for r in rows)
            },
            "type_distribution": dict(types),
            "top_slow_sql": [
                {
                    "service": connection_id,
                    "schema": r["db_name"],
                    "digest_text": r["fingerprint"],
                    "avg_time": r["avg_time_ms"]/1000.0,
                    "count": r["exec_count"]
                }
                for r in rows[:10]
            ]
        }

    def _get_gateway_analysis_data(self, conn, connection_id: str) -> dict:
        cursor = conn.cursor()
        # v1.6.2.2-UAT-O-06：模板消费 reports 列表契约，生产者必须提供——
        # 旧实现只返回单条最新记录的 summary，PDF 网关章节因此永远走"暂无记录"分支。
        cursor.execute("""
            SELECT id, log_file_name, total_queries, slow_queries, max_time_ms, avg_time_ms, created_at
            FROM gateway_log_reports
            WHERE connection_id = %s ORDER BY id DESC LIMIT 5
        """, (connection_id,))
        report_rows = list(cursor.fetchall())
        reports = [
            {
                "id": r["id"],
                "log_file_name": r["log_file_name"],
                "analysis_time": str(r["created_at"]) if r["created_at"] else "",
                "total_queries": r["total_queries"],
                "slow_queries": r["slow_queries"],
                "max_time_ms": r["max_time_ms"],
                "avg_time_ms": r["avg_time_ms"],
            }
            for r in report_rows
        ]
        row = report_rows[0] if report_rows else None

        if not row:
            # v1.6.2.2-UAT-O-05：空数据必须显式标记 no_data，禁止返回演示数据
            return {
                "data_status": "no_data",
                "source_file": None,
                "summary": {
                    "total_requests": 0,
                    "daily_count": 0,
                    "error_count": 0
                },
                "daily_stats": [],
                "timecost_distribution": {},
                "error_codes": {},
                "reports": []
            }

        return {
            "data_status": "ok",
            "source_file": row["log_file_name"],
            "summary": {
                "total_requests": row["total_queries"],
                "daily_count": 1,
                # v1.6.2.2-UAT-O-05：网关记录不含错误数字段，不得拿慢查询数冒充错误数
                "error_count": None
            },
            "daily_stats": [
                {"date": row["created_at"][:10] if row["created_at"] else datetime.now().strftime("%Y-%m-%d"), "count": row["total_queries"]}
            ],
            # v1.6.2.2-UAT-O-05：单条聚合记录推导不出耗时分布，95%/4% 是拍脑袋假数据；
            # 只能确定：慢查询(>=阈值) slow_queries 条，其余为阈值以下。
            "timecost_distribution": {
                "<阈值": row["total_queries"] - row["slow_queries"],
                ">=阈值(慢)": row["slow_queries"]
            },
            "error_codes": {},
            "reports": reports
        }

    def _get_schema_diff_data(self, conn, connection_id: str) -> dict:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT severity, COUNT(*) as cnt
            FROM schema_diff_item
            WHERE diff_id = (
                SELECT id FROM schema_diff WHERE left_conn = %s ORDER BY id DESC LIMIT 1
            )
            GROUP BY severity
        """, (connection_id,))
        rows = list(cursor.fetchall())

        if not rows:
            # v1.6.2.2-UAT-O-05：空数据必须显式标记 no_data，禁止虚构"校验一致"
            return {
                "data_status": "no_data",
                "summary": {
                    "prod_instances": 0,
                    "test_instances": 0,
                    "total_databases": 0,
                    "total_tables": 0,
                    "status": "未执行"
                }
            }

        status = "存在差异"
        
        # v1.6.2.2-UAT-O-05：schema_diff 真实分支的 prod/test/databases/tables
        # 原实现是硬编码假值（1/1/3/45）。本查询仅含 severity 计数，
        # 无法推导实例与库表数，诚实标注 None。
        return {
            "data_status": "ok",
            "summary": {
                "prod_instances": None,
                "test_instances": None,
                "total_databases": None,
                "total_tables": None,
                "status": status
            }
        }


ppt_report_service = PPTReportService()
