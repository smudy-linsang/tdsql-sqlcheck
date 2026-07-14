#!/usr/bin/env node
/**
 * TDSQL 运维汇报 PPT 自动生成脚本
 *
 * 基于 pptxgenjs，读取 collect_report_data.py 生成的 JSON 数据，
 * 自动生成包含多个章节的运维汇报演示文稿。
 *
 * 用法:
 *   node generate_pptx.js [report_data.json] [output.pptx]
 *
 * 参数:
 *   report_data.json  - 数据文件 (默认: output/report_data.json)
 *   output.pptx       - 输出 PPT 文件 (默认: output/report_<date>.pptx)
 */

const pptxgen = require("pptxgenjs");
const fs = require("fs");
const path = require("path");

// ── 颜色主题 ─────────────────────────────────────────────────────────────────
const THEME = {
  // 主色系 — 深蓝底色 + 科技蓝色调
  bgDark: "0F172A",       // 深色背景
  bgLight: "F8FAFC",      // 浅色内容背景
  bgCard: "FFFFFF",       // 卡片背景
  bgSection: "1E293B",    // 章节标题背景

  primary: "3B82F6",      // 主蓝色
  primaryDark: "1D4ED8",  // 深蓝
  secondary: "06B6D4",    // 青色
  accent: "F59E0B",       // 金色强调
  success: "10B981",      // 绿色
  warning: "F59E0B",      // 黄色
  danger: "EF4444",       // 红色
  muted: "94A3B8",        // 灰色文字

  textDark: "0F172A",     // 深色文字
  textBody: "334155",     // 正文文字
  textLight: "F8FAFC",    // 浅色文字
  textMuted: "94A3B8",    // 柔和文字

  border: "E2E8F0",       // 边框
  gridLine: "E2E8F0",     // 网格线

  // 图表色板
  chartColors: ["3B82F6", "06B6D4", "10B981", "F59E0B", "EF4444", "8B5CF6", "EC4899", "14B8A6"],
};

// ── 工具函数 ─────────────────────────────────────────────────────────────────

function fmtNum(n) {
  if (n == null) return "0";
  if (n >= 100000000) return (n / 100000000).toFixed(2) + "亿";
  if (n >= 10000) return (n / 10000).toFixed(1) + "万";
  return n.toLocaleString();
}

function fmtPct(n) {
  if (n == null) return "0%";
  return Number(n).toFixed(1) + "%";
}

function truncate(s, maxLen) {
  if (!s) return "";
  return s.length > maxLen ? s.substring(0, maxLen) + "..." : s;
}

function makeShadow() {
  return { type: "outer", color: "000000", blur: 6, offset: 2, angle: 135, opacity: 0.1 };
}

// ── 幻灯片构建器 ─────────────────────────────────────────────────────────────

function addCoverSlide(pres, meta) {
  const slide = pres.addSlide();
  slide.background = { color: THEME.bgDark };

  // 顶部装饰线
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: 10, h: 0.04,
    fill: { color: THEME.primary },
  });

  // 副标题标签
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0.8, y: 1.2, w: 2.5, h: 0.35,
    fill: { color: THEME.primary }, rectRadius: 0.02,
  });
  slide.addText("数据库运维报告", {
    x: 0.8, y: 1.2, w: 2.5, h: 0.35,
    fontSize: 12, fontFace: "Microsoft YaHei",
    color: THEME.textLight, align: "center", valign: "middle", margin: 0,
  });

  // 主标题
  slide.addText(meta.title || "TDSQL 数据库主动运维报告", {
    x: 0.8, y: 1.8, w: 8.4, h: 1.2,
    fontSize: 36, fontFace: "Microsoft YaHei", bold: true,
    color: THEME.textLight, align: "left", valign: "middle", margin: 0,
  });

  // 副标题
  slide.addText("从被动响应到主动预防的数据库运维转型", {
    x: 0.8, y: 3.1, w: 8.4, h: 0.5,
    fontSize: 16, fontFace: "Microsoft YaHei",
    color: THEME.muted, align: "left", margin: 0,
  });

  // 底部信息
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 4.9, w: 10, h: 0.01,
    fill: { color: THEME.muted },
  });

  slide.addText([
    { text: meta.date || new Date().toISOString().slice(0, 10), options: { color: THEME.muted, fontSize: 11 } },
    { text: "  |  运维团队", options: { color: THEME.muted, fontSize: 11 } },
  ], { x: 0.8, y: 5.05, w: 8.4, h: 0.4, fontFace: "Microsoft YaHei", margin: 0 });
}


function addSectionSlide(pres, title, subtitle) {
  const slide = pres.addSlide();
  slide.background = { color: THEME.bgSection };

  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0.8, y: 2.0, w: 0.06, h: 1.4,
    fill: { color: THEME.primary },
  });

  slide.addText(title, {
    x: 1.1, y: 2.0, w: 8, h: 0.8,
    fontSize: 32, fontFace: "Microsoft YaHei", bold: true,
    color: THEME.textLight, margin: 0,
  });

  if (subtitle) {
    slide.addText(subtitle, {
      x: 1.1, y: 2.8, w: 8, h: 0.5,
      fontSize: 14, fontFace: "Microsoft YaHei",
      color: THEME.muted, margin: 0,
    });
  }
}


function addStatCard(slide, pres, x, y, w, h, label, value, color) {
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h,
    fill: { color: THEME.bgCard },
    shadow: makeShadow(),
  });
  // 左侧色条
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w: 0.06, h,
    fill: { color: color || THEME.primary },
  });

  slide.addText(String(value), {
    x: x + 0.2, y: y + 0.1, w: w - 0.3, h: h * 0.55,
    fontSize: 28, fontFace: "Microsoft YaHei", bold: true,
    color: color || THEME.primary, align: "center", valign: "middle", margin: 0,
  });
  slide.addText(label, {
    x: x + 0.2, y: y + h * 0.55, w: w - 0.3, h: h * 0.35,
    fontSize: 10, fontFace: "Microsoft YaHei",
    color: THEME.textMuted, align: "center", valign: "middle", margin: 0,
  });
}


function addSimpleTable(slide, headers, rows, options) {
  const { x = 0.5, y = 1.8, w = 9, fontSize = 9, colW } = options || {};

  const headerRow = headers.map(h => ({
    text: h, options: {
      fill: { color: THEME.primaryDark }, color: "FFFFFF",
      bold: true, fontSize: fontSize, fontFace: "Microsoft YaHei",
      align: "center", valign: "middle",
    }
  }));

  const dataRows = rows.map((row, ri) => row.map(cell => ({
    text: String(cell == null ? "" : cell),
    options: {
      fill: { color: ri % 2 === 0 ? "FFFFFF" : "F8FAFC" },
      color: THEME.textBody, fontSize: fontSize, fontFace: "Microsoft YaHei",
      valign: "middle",
    }
  })));

  const tableOpts = {
    x, y, w,
    border: { pt: 0.5, color: THEME.border },
    autoPage: true,
    autoPageRepeatHeader: true,
    margin: [0.5, 0.5, 0.5, 0.5],
  };
  if (colW) tableOpts.colW = colW;

  slide.addTable([headerRow, ...dataRows], tableOpts);
}


// ── 模块幻灯片生成 ───────────────────────────────────────────────────────────

function buildInspectionSlides(pres, data) {
  if (!data) return;
  const s = data.summary;

  // 章节页
  addSectionSlide(pres, "每日巡检分析", `监控实例 ${s.instance_count} 个 · ${data.source_file}`);

  // 概览页
  const slide = pres.addSlide();
  slide.background = { color: THEME.bgLight };

  slide.addText("巡检概览", {
    x: 0.5, y: 0.3, w: 9, h: 0.5,
    fontSize: 22, fontFace: "Microsoft YaHei", bold: true,
    color: THEME.textDark, margin: 0,
  });

  // 指标卡片
  addStatCard(slide, pres, 0.5, 1.0, 2.0, 1.0, "监控实例", s.instance_count, THEME.primary);
  addStatCard(slide, pres, 2.7, 1.0, 2.0, 1.0, "全天平均 CPU", fmtPct(s.avg_cpu), THEME.success);
  addStatCard(slide, pres, 4.9, 1.0, 2.0, 1.0, "全天平均内存", fmtPct(s.avg_memory), THEME.secondary);
  addStatCard(slide, pres, 7.1, 1.0, 2.4, 1.0, "慢查询总数", fmtNum(s.total_slow_queries), s.total_slow_queries > 10000 ? THEME.danger : THEME.warning);

  // 告警汇总
  const alertCount = s.alert_count || 0;
  const alertColor = alertCount > 0 ? THEME.danger : THEME.success;
  const alertText = alertCount > 0
    ? `发现 ${alertCount} 项异常`
    : "当前无 P0 级异常，系统运行正常";

  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 2.2, w: 9, h: 0.5,
    fill: { color: alertCount > 0 ? "FEF2F2" : "F0FDF4" },
  });
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 2.2, w: 0.06, h: 0.5,
    fill: { color: alertColor },
  });
  slide.addText(alertText, {
    x: 0.8, y: 2.2, w: 8.7, h: 0.5,
    fontSize: 12, fontFace: "Microsoft YaHei", bold: true,
    color: alertColor, valign: "middle", margin: 0,
  });

  // 异常实例详情
  if (s.cpu_alerts && s.cpu_alerts.length > 0) {
    let yPos = 2.9;
    slide.addText("CPU 峰值异常实例：", {
      x: 0.5, y: yPos, w: 9, h: 0.35,
      fontSize: 11, fontFace: "Microsoft YaHei", bold: true,
      color: THEME.danger, margin: 0,
    });
    yPos += 0.35;
    s.cpu_alerts.forEach(a => {
      slide.addText(`  ${a.name}：CPU 峰值 ${a.value}%`, {
        x: 0.5, y: yPos, w: 9, h: 0.3,
        fontSize: 10, fontFace: "Microsoft YaHei",
        color: THEME.textBody, margin: 0,
      });
      yPos += 0.3;
    });
  }

  // TOP 慢查询实例
  if (data.top_slow_queries && data.top_slow_queries.length > 0) {
    const slide2 = pres.addSlide();
    slide2.background = { color: THEME.bgLight };

    slide2.addText("慢查询 TOP 10 实例", {
      x: 0.5, y: 0.3, w: 9, h: 0.5,
      fontSize: 22, fontFace: "Microsoft YaHei", bold: true,
      color: THEME.textDark, margin: 0,
    });

    // 柱状图
    const topData = data.top_slow_queries.filter(t => t.count > 0);
    if (topData.length > 0) {
      slide2.addChart(pres.charts.BAR, [{
        name: "慢查询数",
        labels: topData.map(t => truncate(t.name, 12)),
        values: topData.map(t => t.count),
      }], {
        x: 0.5, y: 1.0, w: 9, h: 4.2,
        barDir: "bar",
        chartColors: [THEME.danger],
        catAxisLabelColor: THEME.textBody,
        valAxisLabelColor: THEME.textMuted,
        valGridLine: { color: THEME.gridLine, size: 0.5 },
        catGridLine: { style: "none" },
        showValue: true,
        dataLabelPosition: "outEnd",
        dataLabelColor: THEME.textBody,
        showLegend: false,
        chartArea: { fill: { color: "FFFFFF" }, roundedCorners: true },
      });
    }
  }
}


function buildCountRowsSlides(pres, data) {
  if (!data) return;
  const s = data.summary;

  addSectionSlide(pres, "集群大表分析", `共 ${fmtNum(s.total_tables)} 张表 · ${s.snapshot_count} 次采集快照`);

  const slide = pres.addSlide();
  slide.background = { color: THEME.bgLight };

  slide.addText("数据量概览", {
    x: 0.5, y: 0.3, w: 9, h: 0.5,
    fontSize: 22, fontFace: "Microsoft YaHei", bold: true,
    color: THEME.textDark, margin: 0,
  });

  addStatCard(slide, pres, 0.5, 1.0, 2.8, 1.0, "数据表总数", fmtNum(s.total_tables), THEME.primary);
  addStatCard(slide, pres, 3.6, 1.0, 2.8, 1.0, "总行数", fmtNum(s.total_rows), THEME.secondary);
  addStatCard(slide, pres, 6.7, 1.0, 2.8, 1.0, "采集快照", s.snapshot_count + " 次", THEME.success);

  // 数据量分布饼图
  if (s.distribution) {
    const distLabels = Object.keys(s.distribution);
    const distValues = Object.values(s.distribution);
    if (distValues.some(v => v > 0)) {
      slide.addChart(pres.charts.DOUGHNUT, [{
        name: "数据量分布",
        labels: distLabels,
        values: distValues,
      }], {
        x: 0.3, y: 2.3, w: 4.2, h: 3.0,
        chartColors: THEME.chartColors,
        showPercent: true,
        showLegend: true,
        legendPos: "b",
        dataLabelColor: THEME.textBody,
      });
    }
  }

  // 大表 TOP 10 表格
  if (data.tables_top20 && data.tables_top20.length > 0) {
    const top10 = data.tables_top20.slice(0, 8);
    const rows = top10.map((t, i) => [
      i + 1, truncate(t.service || t.database, 15), truncate(t.table, 25), fmtNum(t.row_count)
    ]);

    slide.addText("大表 TOP 排行", {
      x: 4.8, y: 2.3, w: 4.8, h: 0.4,
      fontSize: 13, fontFace: "Microsoft YaHei", bold: true,
      color: THEME.textDark, margin: 0,
    });

    addSimpleTable(slide,
      ["#", "服务/库", "表名", "行数"],
      rows,
      { x: 4.8, y: 2.7, w: 4.8, fontSize: 8, colW: [0.3, 1.2, 2.2, 1.1] }
    );
  }
}


function buildIndexSlides(pres, data) {
  if (!data) return;
  const s = data.summary;

  addSectionSlide(pres, "索引健康度分析", `索引总数 ${fmtNum(s.total_indexes)} · 涉及 ${s.unique_tables} 张表`);

  const slide = pres.addSlide();
  slide.background = { color: THEME.bgLight };

  slide.addText("索引概览", {
    x: 0.5, y: 0.3, w: 9, h: 0.5,
    fontSize: 22, fontFace: "Microsoft YaHei", bold: true,
    color: THEME.textDark, margin: 0,
  });

  // 指标卡片
  addStatCard(slide, pres, 0.5, 1.0, 1.7, 1.0, "索引总数", fmtNum(s.total_indexes), THEME.primary);
  addStatCard(slide, pres, 2.4, 1.0, 1.7, 1.0, "完全重复", s.duplicate_count, s.duplicate_count > 0 ? THEME.danger : THEME.success);
  addStatCard(slide, pres, 4.3, 1.0, 1.7, 1.0, "前缀冗余", s.prefix_redundant_count, s.prefix_redundant_count > 0 ? THEME.warning : THEME.success);
  addStatCard(slide, pres, 6.2, 1.0, 1.7, 1.0, "未使用索引", s.unused_count, s.unused_count > 0 ? THEME.warning : THEME.success);
  addStatCard(slide, pres, 8.1, 1.0, 1.4, 1.0, "碎片表", s.fragmented_tables, THEME.muted);

  // 索引类型分布
  const typeLabels = ["主键", "唯一索引", "普通索引"];
  const typeValues = [s.pk_count, s.unique_count, s.normal_count];
  slide.addChart(pres.charts.PIE, [{
    name: "索引类型",
    labels: typeLabels,
    values: typeValues,
  }], {
    x: 0.3, y: 2.3, w: 3.5, h: 2.8,
    chartColors: [THEME.primary, THEME.secondary, THEME.success],
    showPercent: true,
    showLegend: true,
    legendPos: "b",
  });

  // 影响说明
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 4.2, y: 2.3, w: 5.3, h: 2.8,
    fill: { color: THEME.bgCard },
    shadow: makeShadow(),
  });
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 4.2, y: 2.3, w: 0.06, h: 2.8,
    fill: { color: THEME.warning },
  });

  const totalRedundant = s.duplicate_count + s.prefix_redundant_count;
  slide.addText("问题影响分析", {
    x: 4.5, y: 2.4, w: 4.8, h: 0.4,
    fontSize: 14, fontFace: "Microsoft YaHei", bold: true,
    color: THEME.textDark, margin: 0,
  });
  slide.addText([
    { text: `冗余索引 ${totalRedundant} 组`, options: { bold: true, breakLine: true, fontSize: 11 } },
    { text: `影响写入性能：每次 DML 操作需维护多份索引`, options: { breakLine: true, fontSize: 10, color: THEME.textBody } },
    { text: "", options: { breakLine: true, fontSize: 6 } },
    { text: `浪费存储空间`, options: { bold: true, breakLine: true, fontSize: 11 } },
    { text: `重复数据占用磁盘资源，增加备份成本和恢复时间`, options: { breakLine: true, fontSize: 10, color: THEME.textBody } },
    { text: "", options: { breakLine: true, fontSize: 6 } },
    { text: `未使用索引 ${s.unused_count} 个`, options: { bold: true, breakLine: true, fontSize: 11 } },
    { text: `从未被查询使用，建议评估后清理`, options: { fontSize: 10, color: THEME.textBody } },
  ], {
    x: 4.5, y: 2.9, w: 4.8, h: 2.0,
    fontFace: "Microsoft YaHei", color: THEME.textDark, margin: 0,
  });

  // 重复索引详情表
  if (data.duplicate_indexes && data.duplicate_indexes.length > 0) {
    const slide2 = pres.addSlide();
    slide2.background = { color: THEME.bgLight };
    slide2.addText("重复索引详情", {
      x: 0.5, y: 0.3, w: 9, h: 0.5,
      fontSize: 22, fontFace: "Microsoft YaHei", bold: true,
      color: THEME.textDark, margin: 0,
    });

    const rows = data.duplicate_indexes.slice(0, 12).map(d => [
      truncate(d.schema, 15), truncate(d.table, 20),
      truncate(d.index1, 15), truncate(d.index2, 15),
      truncate(d.columns, 25),
    ]);
    addSimpleTable(slide2,
      ["数据库", "表名", "索引1", "索引2", "列"],
      rows,
      { x: 0.5, y: 1.0, w: 9, fontSize: 8, colW: [1.5, 2.0, 1.5, 1.5, 2.5] }
    );
  }
}


function buildSqlSlides(pres, data) {
  if (!data) return;
  const s = data.summary;

  addSectionSlide(pres, "SQL 分析", `SQL 种类 ${s.total_sql_types || 0} · 全表扫描 ${s.full_scan_count || 0} 个`);

  const slide = pres.addSlide();
  slide.background = { color: THEME.bgLight };

  slide.addText("SQL 执行统计", {
    x: 0.5, y: 0.3, w: 9, h: 0.5,
    fontSize: 22, fontFace: "Microsoft YaHei", bold: true,
    color: THEME.textDark, margin: 0,
  });

  addStatCard(slide, pres, 0.5, 1.0, 2.0, 1.0, "SQL 种类", s.total_sql_types || 0, THEME.primary);
  addStatCard(slide, pres, 2.7, 1.0, 2.0, 1.0, "总调用量", fmtNum(s.total_count || 0), THEME.secondary);
  addStatCard(slide, pres, 4.9, 1.0, 2.0, 1.0, "全表扫描", s.full_scan_count || 0, s.full_scan_count > 0 ? THEME.danger : THEME.success);
  addStatCard(slide, pres, 7.1, 1.0, 2.4, 1.0, "总耗时(s)", fmtNum(s.total_time || 0), THEME.warning);

  // SQL 类型分布
  if (data.type_distribution && Object.keys(data.type_distribution).length > 0) {
    const labels = Object.keys(data.type_distribution);
    const values = Object.values(data.type_distribution);
    slide.addChart(pres.charts.DOUGHNUT, [{
      name: "SQL 类型",
      labels, values,
    }], {
      x: 0.3, y: 2.3, w: 4.0, h: 3.0,
      chartColors: THEME.chartColors,
      showPercent: true,
      showLegend: true,
      legendPos: "b",
    });
  }

  // 高耗时 SQL TOP
  if (data.top_slow_sql && data.top_slow_sql.length > 0) {
    const top5 = data.top_slow_sql.slice(0, 5);
    const rows = top5.map((r, i) => [
      i + 1,
      truncate(r.service || r.schema, 12),
      truncate(r.digest_text, 35),
      r.avg_time ? r.avg_time.toFixed(3) + "s" : "-",
      fmtNum(r.count),
    ]);

    slide.addText("高耗时 SQL TOP 5", {
      x: 4.5, y: 2.3, w: 5.2, h: 0.4,
      fontSize: 13, fontFace: "Microsoft YaHei", bold: true,
      color: THEME.textDark, margin: 0,
    });

    addSimpleTable(slide,
      ["#", "服务", "SQL", "平均耗时", "调用量"],
      rows,
      { x: 4.5, y: 2.7, w: 5.2, fontSize: 7, colW: [0.3, 0.9, 2.2, 0.9, 0.9] }
    );
  }
}


function buildGatewaySlides(pres, data) {
  if (!data) return;
  const s = data.summary;

  addSectionSlide(pres, "Gateway 日志分析", `总请求量 ${fmtNum(s.total_requests || 0)} · ${data.source_file}`);

  const slide = pres.addSlide();
  slide.background = { color: THEME.bgLight };

  slide.addText("Gateway 概览", {
    x: 0.5, y: 0.3, w: 9, h: 0.5,
    fontSize: 22, fontFace: "Microsoft YaHei", bold: true,
    color: THEME.textDark, margin: 0,
  });

  addStatCard(slide, pres, 0.5, 1.0, 2.8, 1.0, "总请求量", fmtNum(s.total_requests || 0), THEME.primary);
  addStatCard(slide, pres, 3.6, 1.0, 2.8, 1.0, "日志天数", s.daily_count || 0, THEME.secondary);
  addStatCard(slide, pres, 6.7, 1.0, 2.8, 1.0, "错误请求", fmtNum(s.error_count || 0), s.error_count > 0 ? THEME.danger : THEME.success);

  // 每日请求量趋势
  if (data.daily_stats && data.daily_stats.length > 1) {
    slide.addChart(pres.charts.LINE, [{
      name: "每日请求量",
      labels: data.daily_stats.map(d => d.date),
      values: data.daily_stats.map(d => d.count),
    }], {
      x: 0.3, y: 2.3, w: 9.4, h: 3.0,
      lineSmooth: true,
      chartColors: [THEME.primary],
      catAxisLabelColor: THEME.textMuted,
      valAxisLabelColor: THEME.textMuted,
      valGridLine: { color: THEME.gridLine, size: 0.5 },
      catGridLine: { style: "none" },
      showLegend: false,
      chartArea: { fill: { color: "FFFFFF" }, roundedCorners: true },
    });
  }

  // 耗时分布
  if (data.timecost_distribution && Object.keys(data.timecost_distribution).length > 0) {
    const slide2 = pres.addSlide();
    slide2.background = { color: THEME.bgLight };

    slide2.addText("耗时分布 & 错误码分析", {
      x: 0.5, y: 0.3, w: 9, h: 0.5,
      fontSize: 22, fontFace: "Microsoft YaHei", bold: true,
      color: THEME.textDark, margin: 0,
    });

    const tbLabels = Object.keys(data.timecost_distribution);
    const tbValues = Object.values(data.timecost_distribution);
    slide2.addChart(pres.charts.BAR, [{
      name: "请求量",
      labels: tbLabels,
      values: tbValues,
    }], {
      x: 0.3, y: 1.0, w: 4.5, h: 4.2,
      barDir: "col",
      chartColors: [THEME.secondary],
      showValue: true,
      dataLabelPosition: "outEnd",
      dataLabelColor: THEME.textBody,
      showLegend: false,
      catAxisLabelColor: THEME.textBody,
      valAxisLabelColor: THEME.textMuted,
      valGridLine: { color: THEME.gridLine, size: 0.5 },
      catGridLine: { style: "none" },
      chartArea: { fill: { color: "FFFFFF" }, roundedCorners: true },
    });

    // 错误码表
    if (data.error_codes && Object.keys(data.error_codes).length > 0) {
      const errRows = Object.entries(data.error_codes)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10)
        .map(([code, cnt]) => [code, fmtNum(cnt)]);

      slide2.addText("错误码 TOP", {
        x: 5.2, y: 1.0, w: 4.3, h: 0.4,
        fontSize: 13, fontFace: "Microsoft YaHei", bold: true,
        color: THEME.textDark, margin: 0,
      });

      addSimpleTable(slide2, ["错误码", "次数"], errRows, {
        x: 5.2, y: 1.5, w: 4.3, fontSize: 9, colW: [2.0, 2.3],
      });
    }
  }
}


function buildSchemaDiffSlides(pres, data) {
  if (!data) return;
  const s = data.summary;

  addSectionSlide(pres, "表结构对比", `生产 ${s.prod_instances} 实例 · ${s.total_tables} 张表 · ${s.status}`);

  const slide = pres.addSlide();
  slide.background = { color: THEME.bgLight };

  slide.addText("环境结构一致性校验", {
    x: 0.5, y: 0.3, w: 9, h: 0.5,
    fontSize: 22, fontFace: "Microsoft YaHei", bold: true,
    color: THEME.textDark, margin: 0,
  });

  addStatCard(slide, pres, 0.5, 1.0, 2.0, 1.0, "生产实例", s.prod_instances, THEME.primary);
  addStatCard(slide, pres, 2.7, 1.0, 2.0, 1.0, "测试实例", s.test_instances, THEME.secondary);
  addStatCard(slide, pres, 4.9, 1.0, 2.0, 1.0, "数据库数", s.total_databases, THEME.success);
  addStatCard(slide, pres, 7.1, 1.0, 2.4, 1.0, "表总数", fmtNum(s.total_tables), THEME.warning);

  // 说明
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 2.3, w: 9, h: 2.5,
    fill: { color: THEME.bgCard },
    shadow: makeShadow(),
  });
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0.5, y: 2.3, w: 0.06, h: 2.5,
    fill: { color: THEME.primary },
  });

  slide.addText([
    { text: "表结构对比功能说明", options: { bold: true, fontSize: 14, breakLine: true } },
    { text: "", options: { breakLine: true, fontSize: 6 } },
    { text: "自动检测生产环境与非功能环境的表结构差异，确保环境一致性。", options: { breakLine: true, fontSize: 11 } },
    { text: "", options: { breakLine: true, fontSize: 6 } },
    { text: "对比维度：", options: { bold: true, breakLine: true, fontSize: 11 } },
    { text: "  表是否存在 · 列定义（名称/类型/默认值）· 索引定义 · 触发器", options: { breakLine: true, fontSize: 10, color: THEME.textBody } },
    { text: "", options: { breakLine: true, fontSize: 6 } },
    { text: "严重程度分级：", options: { bold: true, breakLine: true, fontSize: 11 } },
    { text: "  CRITICAL（应用异常风险）· HIGH（结构不一致）· MEDIUM（配置差异）· INFO（提示）", options: { fontSize: 10, color: THEME.textBody } },
  ], {
    x: 0.8, y: 2.4, w: 8.5, h: 2.3,
    fontFace: "Microsoft YaHei", color: THEME.textDark, margin: 0,
  });
}


function buildSummarySlide(pres, modules) {
  addSectionSlide(pres, "问题汇总与优先级", "系统风险评估报告");

  const slide = pres.addSlide();
  slide.background = { color: THEME.bgLight };

  slide.addText("问题优先级总览", {
    x: 0.5, y: 0.3, w: 9, h: 0.5,
    fontSize: 22, fontFace: "Microsoft YaHei", bold: true,
    color: THEME.textDark, margin: 0,
  });

  let yPos = 1.0;
  const issues = [];

  // 汇总问题
  const insp = modules.daily_inspection;
  if (insp) {
    const cpuAlerts = insp.summary.cpu_alerts || [];
    cpuAlerts.forEach(a => {
      issues.push({ level: "P1", desc: `${a.name} CPU峰值 ${a.value}%`, color: THEME.danger });
    });
  }

  const idx = modules.index_analysis;
  if (idx) {
    if (idx.summary.duplicate_count > 0) {
      issues.push({ level: "P2", desc: `${idx.summary.duplicate_count} 组完全重复索引，影响存储与写入性能`, color: THEME.warning });
    }
    if (idx.summary.prefix_redundant_count > 0) {
      issues.push({ level: "P2", desc: `${idx.summary.prefix_redundant_count} 组前缀冗余索引，需评估后清理`, color: THEME.warning });
    }
  }

  const sql = modules.sql_analysis;
  if (sql && sql.summary.full_scan_count > 0) {
    issues.push({ level: "P2", desc: `${sql.summary.full_scan_count} 个全表扫描 SQL，需优化查询逻辑`, color: THEME.warning });
  }

  if (insp && insp.summary.total_slow_queries > 10000) {
    issues.push({ level: "P2", desc: `慢查询 ${fmtNum(insp.summary.total_slow_queries)} 条，需优化查询`, color: THEME.warning });
  }

  if (issues.length === 0) {
    issues.push({ level: "OK", desc: "当前无严重风险项，系统核心指标监测正常", color: THEME.success });
  }

  issues.forEach((issue, i) => {
    const bgColor = issue.level === "P1" ? "FEF2F2" : (issue.level === "P2" ? "FFFBEB" : "F0FDF4");

    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y: yPos, w: 9, h: 0.55,
      fill: { color: bgColor },
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0.5, y: yPos, w: 0.06, h: 0.55,
      fill: { color: issue.color },
    });

    // 级别标签
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0.8, y: yPos + 0.1, w: 0.5, h: 0.35,
      fill: { color: issue.color },
    });
    slide.addText(issue.level, {
      x: 0.8, y: yPos + 0.1, w: 0.5, h: 0.35,
      fontSize: 10, fontFace: "Microsoft YaHei", bold: true,
      color: "FFFFFF", align: "center", valign: "middle", margin: 0,
    });

    slide.addText(issue.desc, {
      x: 1.5, y: yPos, w: 7.8, h: 0.55,
      fontSize: 11, fontFace: "Microsoft YaHei",
      color: THEME.textDark, valign: "middle", margin: 0,
    });

    yPos += 0.65;
  });

  // 行动计划
  yPos += 0.3;
  if (yPos < 4.5) {
    slide.addText("后续行动建议", {
      x: 0.5, y: yPos, w: 9, h: 0.4,
      fontSize: 14, fontFace: "Microsoft YaHei", bold: true,
      color: THEME.textDark, margin: 0,
    });
    yPos += 0.4;

    const actions = [
      { tag: "紧急", text: "排查 CPU 峰值异常实例，检查索引和慢 SQL", color: THEME.danger },
      { tag: "短期", text: "清理完全重复索引，优化高频慢 SQL", color: THEME.warning },
      { tag: "中期", text: "评估前缀冗余索引，建立大表数据归档机制", color: THEME.primary },
      { tag: "长期", text: "完善自动化巡检体系，持续优化性能基线", color: THEME.success },
    ];

    actions.forEach(a => {
      if (yPos > 5.1) return;
      slide.addShape(pres.shapes.RECTANGLE, {
        x: 0.8, y: yPos + 0.05, w: 0.6, h: 0.3,
        fill: { color: a.color },
      });
      slide.addText(a.tag, {
        x: 0.8, y: yPos + 0.05, w: 0.6, h: 0.3,
        fontSize: 9, fontFace: "Microsoft YaHei", bold: true,
        color: "FFFFFF", align: "center", valign: "middle", margin: 0,
      });
      slide.addText(a.text, {
        x: 1.55, y: yPos, w: 7.9, h: 0.4,
        fontSize: 10, fontFace: "Microsoft YaHei",
        color: THEME.textBody, valign: "middle", margin: 0,
      });
      yPos += 0.45;
    });
  }
}


function buildMonitoringOverviewSlide(pres, modules) {
  addSectionSlide(pres, "主动运维监控体系", "建立全方位监控体系，覆盖日常运行、数据增长、索引优化与SQL性能");

  const slide = pres.addSlide();
  slide.background = { color: THEME.bgLight };

  slide.addText("监控体系总览", {
    x: 0.5, y: 0.3, w: 9, h: 0.5,
    fontSize: 22, fontFace: "Microsoft YaHei", bold: true,
    color: THEME.textDark, margin: 0,
  });

  // 6 个监控模块卡片 (2行3列)
  const items = [
    { title: "每日巡检", freq: "每天", desc: "CPU/内存/磁盘/慢查询/请求量", color: THEME.primary, active: !!modules.daily_inspection },
    { title: "大表分析", freq: "每周", desc: "表行数统计、数据分布、增长趋势", color: THEME.secondary, active: !!modules.count_table_rows },
    { title: "索引分析", freq: "每周", desc: "重复索引/冗余索引/碎片分析", color: THEME.success, active: !!modules.index_analysis },
    { title: "慢SQL分析", freq: "每天", desc: "SQL指纹/执行频次/耗时分布", color: THEME.warning, active: !!modules.sql_analysis },
    { title: "Gateway日志", freq: "按需", desc: "请求趋势/耗时/错误码/高频SQL", color: THEME.accent, active: !!modules.gateway_analysis },
    { title: "表结构对比", freq: "按需", desc: "生产与非功能环境结构差异检测", color: "8B5CF6", active: !!modules.schema_diff },
  ];

  items.forEach((item, i) => {
    const col = i % 3;
    const row = Math.floor(i / 3);
    const x = 0.5 + col * 3.1;
    const y = 1.1 + row * 2.2;
    const w = 2.8;
    const h = 1.9;

    slide.addShape(pres.shapes.RECTANGLE, {
      x, y, w, h,
      fill: { color: THEME.bgCard },
      shadow: makeShadow(),
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x, y, w, h: 0.05,
      fill: { color: item.color },
    });

    // 频率标签
    slide.addShape(pres.shapes.RECTANGLE, {
      x: x + w - 0.8, y: y + 0.15, w: 0.65, h: 0.25,
      fill: { color: item.color },
    });
    slide.addText(item.freq, {
      x: x + w - 0.8, y: y + 0.15, w: 0.65, h: 0.25,
      fontSize: 8, fontFace: "Microsoft YaHei", bold: true,
      color: "FFFFFF", align: "center", valign: "middle", margin: 0,
    });

    slide.addText(item.title, {
      x: x + 0.15, y: y + 0.15, w: w - 1.0, h: 0.35,
      fontSize: 14, fontFace: "Microsoft YaHei", bold: true,
      color: THEME.textDark, margin: 0,
    });

    slide.addText(item.desc, {
      x: x + 0.15, y: y + 0.6, w: w - 0.3, h: 0.6,
      fontSize: 9, fontFace: "Microsoft YaHei",
      color: THEME.textBody, margin: 0,
    });

    // 状态标记
    const statusText = item.active ? "✓ 已接入" : "○ 待接入";
    const statusColor = item.active ? THEME.success : THEME.muted;
    slide.addText(statusText, {
      x: x + 0.15, y: y + h - 0.4, w: w - 0.3, h: 0.3,
      fontSize: 9, fontFace: "Microsoft YaHei",
      color: statusColor, margin: 0,
    });
  });
}


// ── 主入口 ───────────────────────────────────────────────────────────────────

function main() {
  const args = process.argv.slice(2);
  const dataFile = args[0] || path.join(__dirname, "output", "report_data.json");
  const defaultOutput = path.join(__dirname, "output", `report_${new Date().toISOString().slice(0, 10)}.pptx`);
  const outputFile = args[1] || defaultOutput;

  // 读取数据
  if (!fs.existsSync(dataFile)) {
    console.error(`错误: 数据文件不存在 ${dataFile}`);
    console.error(`请先运行: python3 collect_report_data.py`);
    process.exit(1);
  }

  const reportData = JSON.parse(fs.readFileSync(dataFile, "utf-8"));
  const meta = reportData.meta || {};
  const modules = reportData.modules || {};

  console.log(`[生成] 读取数据: ${dataFile}`);
  console.log(`[生成] 活跃模块: ${Object.keys(modules).join(", ")}`);

  // 创建演示文稿
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9";
  pres.author = "tdsql-toolkit/auto_report";
  pres.title = meta.title || "TDSQL 运维报告";

  // 构建幻灯片
  console.log("[生成] 封面...");
  addCoverSlide(pres, meta);

  console.log("[生成] 监控体系总览...");
  buildMonitoringOverviewSlide(pres, modules);

  if (modules.daily_inspection) {
    console.log("[生成] 每日巡检...");
    buildInspectionSlides(pres, modules.daily_inspection);
  }

  if (modules.count_table_rows) {
    console.log("[生成] 大表分析...");
    buildCountRowsSlides(pres, modules.count_table_rows);
  }

  if (modules.index_analysis) {
    console.log("[生成] 索引分析...");
    buildIndexSlides(pres, modules.index_analysis);
  }

  if (modules.sql_analysis) {
    console.log("[生成] SQL 分析...");
    buildSqlSlides(pres, modules.sql_analysis);
  }

  if (modules.gateway_analysis) {
    console.log("[生成] Gateway 日志分析...");
    buildGatewaySlides(pres, modules.gateway_analysis);
  }

  if (modules.schema_diff) {
    console.log("[生成] 表结构对比...");
    buildSchemaDiffSlides(pres, modules.schema_diff);
  }

  console.log("[生成] 问题汇总...");
  buildSummarySlide(pres, modules);

  // 保存
  const outDir = path.dirname(outputFile);
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

  pres.writeFile({ fileName: outputFile }).then(() => {
    console.log(`\n[完成] PPT 已生成: ${outputFile}`);
  }).catch(err => {
    console.error(`[错误] 生成失败: ${err.message}`);
    process.exit(1);
  });
}

main();
