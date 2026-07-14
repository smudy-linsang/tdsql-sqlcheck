#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TDSQL Gateway 日志整合分析脚本 v1.0

作者: lynx,boogqwang

功能:
  读取 analyze_gateway_log.py 导出的 JSON / JSON.GZ 数据文件，
  支持多文件合并（多节点/多时段），生成统一的分析报告。

  典型场景：
  - 多台 Gateway 各自导出 JSON 数据，集中到一台机器统一分析
  - 同一 Gateway 不同时段的数据合并查看趋势

支持的输出格式:
  - 终端文本 (terminal)
  - Markdown (.md)
  - HTML (.html，含火焰图/目录导航)

依赖:
  仅使用 Python 标准库 (Python >= 3.6)

用法:
  python3 merge_gateway_reports.py <file1.json[.gz]> [file2.json[.gz] ...] [选项]
  python3 merge_gateway_reports.py data_15001.json.gz data_15003.json.gz -o report.html
  python3 merge_gateway_reports.py *.json.gz -o merged_report.html
  python3 merge_gateway_reports.py -h
"""

import argparse
import gzip
import io
import json
import os
import sys
from collections import Counter, OrderedDict
from datetime import datetime

VERSION = "1.0"

# ============================================================
# 短 key → 长 key 映射（与 analyze_gateway_log.py 保持一致）
# ============================================================

_DEFAULT_KEY_MAP_REV = {
    "v": "version", "et": "export_time", "hn": "hostname", "ld": "log_dirs",
    "tn": "top_n", "sl": "sample_limit", "df": "date_filter",
    "r": "results",
    "ov": "overview", "dt": "dates", "pt": "port",
    "fc": "file_count", "ts": "total_size",
    "itf": "interf", "ds": "daily_stats", "hc": "hourly_counts",
    "bm": "busiest_minutes", "uc": "user_counts", "dc": "db_counts",
    "stc": "sql_type_counts", "rc": "resultcode_counts",
    "tb": "timecost_bins", "ht": "high_timecost",
    "sp": "sql_patterns", "ncc": "new_conn_count",
    "ed": "error_details", "tl": "total_lines",
    "tts": "total_timecost_sum", "fd": "flame_data",
    "c": "count", "tcs": "timecost_sum", "tcc": "timecost_count",
    "b": "bins",
    "sq": "sql",
    "ss": "slow_sql", "tc": "total_count", "dcs": "daily_counts",
    "tsw": "top_slow", "sc": "schema_counts",
    "qt": "query_time", "lt": "lock_time",
    "rs": "rows_sent", "re": "rows_examined",
    "sch": "schema", "u": "user", "ci": "client_ip",
    "be": "backend", "t": "time",
    "sy": "sys", "ety": "error_types", "zk": "zk_errors",
    "eto": "event_timeout", "sse": "sql_syntax_errors",
    "se": "sample_errors",
    "rt": "route",
}

# ── 常量（与 analyze_gateway_log.py 一致）──────────────────

LOG_TYPES = {
    "interf": "SQL接口层日志",
    "sql": "SQL执行层日志",
    "slow_sql": "慢SQL日志",
    "sys": "系统/错误日志",
    "route": "路由日志",
    "dbfw": "数据库防火墙日志",
}

SQL_TYPE_MAP = {
    "0": "未知", "1": "QUIT/断开", "2": "USE/初始化",
    "3": "查询/DML", "4": "CREATE", "5": "INSERT",
    "6": "UPDATE", "7": "DELETE", "8": "SELECT",
    "9": "SHOW", "10": "SET", "11": "新建连接",
    "14": "PREPARE", "22": "事务(BEGIN)", "23": "事务(COMMIT)", "25": "事务(SET)",
}

TIMECOST_BINS_LABELS = [
    "<1ms", "1-5ms", "5-10ms", "10-50ms", "50-100ms",
    "100-500ms", "500ms-1s", "1-3s", "3-10s", ">10s",
]

RESULTCODE_MAP = {
    "0": "成功", "1046": "未选择数据库", "1062": "唯一键冲突",
    "1064": "SQL语法错误", "1317": "查询被中断",
    "650": "Proxy不支持的操作", "4039": "TDSQL Proxy限制",
}

# ── 颜色 ──────────────────────────────────────────

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

NO_COLOR = not sys.stderr.isatty() or os.environ.get("NO_COLOR")


def c(code, text):
    if NO_COLOR:
        return str(text)
    return f"{code}{text}{RESET}"


def print_banner():
    out = sys.stderr
    if NO_COLOR:
        out.write(f"# TDSQL Gateway Merge Report Tool v{VERSION}\n\n")
    else:
        out.write(f"{CYAN}")
        out.write("╔═══════════════════════════════════════════════════════════╗\n")
        out.write("║                                                           ║\n")
        out.write("║   ████████╗██████╗ ███████╗ ██████╗ ██╗                   ║\n")
        out.write("║   ╚══██╔══╝██╔══██╗██╔════╝██╔═══██╗██║                   ║\n")
        out.write("║      ██║   ██║  ██║███████╗██║   ██║██║                   ║\n")
        out.write("║      ██║   ██║  ██║╚════██║██║▄▄ ██║██║                   ║\n")
        out.write("║      ██║   ██████╔╝███████║╚██████╔╝███████╗              ║\n")
        out.write("║      ╚═╝   ╚═════╝ ╚══════╝ ╚══▀▀═╝ ╚══════╝              ║\n")
        out.write("║                                                           ║\n")
        out.write(f"║        Gateway Merge Report Tool v{VERSION}                       ║\n")
        out.write("╚═══════════════════════════════════════════════════════════╝\n")
        out.write(f"{RESET}\n")


# ============================================================
# 数据加载与还原
# ============================================================


def _expand_keys(obj, rev_map):
    """递归将短 key 还原为长 key"""
    if isinstance(obj, dict):
        return {rev_map.get(k, k): _expand_keys(v, rev_map) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_keys(i, rev_map) for i in obj]
    return obj


def _build_rev_map(embedded_map):
    """从 JSON 中嵌入的 _key_map 构建反向映射。

    analyze_gateway_log.py 的 _compact_keys 会把 _key_map 自身也缩短，
    所以嵌入的映射是 short_key → short_key（已经是短 key 了）。
    我们使用内置的默认映射来还原。
    """
    return dict(_DEFAULT_KEY_MAP_REV)


def load_data_file(filepath):
    """加载 JSON 或 JSON.GZ 数据文件，还原短 key"""
    print(f"  {c(CYAN, '[加载]')} {filepath}", end="", file=sys.stderr)
    is_gz = filepath.lower().endswith(".gz")
    try:
        if is_gz:
            with gzip.open(filepath, "rt", encoding="utf-8") as f:
                raw = json.load(f)
        else:
            with open(filepath, "r", encoding="utf-8") as f:
                raw = json.load(f)
    except Exception as e:
        print(f" {c(RED, f'失败: {e}')}", file=sys.stderr)
        return None

    # 构建反向映射并还原
    rev_map = _build_rev_map(raw.get("_key_map", {}))
    data = _expand_keys(raw, rev_map)

    # 提取元信息
    ver = data.get("version", "?")
    export_time = data.get("export_time", "?")
    hostname = data.get("hostname", "")
    results = data.get("results", {})
    dirs = list(results.keys())
    fsize = os.path.getsize(filepath)
    size_str = _fmt_size(fsize)
    host_info = f", host={hostname}" if hostname else ""
    print(f" → v{ver}, {export_time}{host_info}, {len(dirs)} 个目录 ({size_str})", file=sys.stderr)
    return data


def _fmt_size(size_bytes):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}PB"


# ============================================================
# HTML 模板（与 analyze_gateway_log.py 共享样式）
# ============================================================

import html as html_mod


def _h(text):
    return html_mod.escape(str(text))


HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TDSQL Gateway 整合分析报告</title>
<style>
:root {
  --bg: #f8f9fa; --card: #fff; --border: #e0e0e0; --text: #212529;
  --primary: #0d6efd; --success: #198754; --warning: #ffc107;
  --danger: #dc3545; --info: #0dcaf0; --muted: #6c757d;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
       background: var(--bg); color: var(--text); line-height: 1.6; padding: 20px; }
.container { max-width: 1200px; margin: 0 auto; }
h1 { color: var(--primary); border-bottom: 3px solid var(--primary); padding-bottom: 10px; margin: 30px 0 10px; font-size: 1.8em; }
h2 { color: #333; margin: 28px 0 14px; font-size: 1.35em; border-left: 4px solid var(--primary); padding-left: 12px; }
h3 { color: #444; margin: 18px 0 10px; font-size: 1.1em; }
p.meta { color: var(--muted); font-size: 0.9em; margin-bottom: 20px; }
table { border-collapse: collapse; width: 100%; margin: 10px 0 18px; background: var(--card);
        box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-radius: 6px; overflow: hidden; }
th { background: #f1f3f5; color: #333; font-weight: 600; text-align: left; padding: 10px 14px;
     border-bottom: 2px solid var(--border); font-size: 0.9em; white-space: nowrap; }
td { padding: 8px 14px; border-bottom: 1px solid #f0f0f0; font-size: 0.88em; vertical-align: top; }
tr:hover td { background: #f8f9ff; }
tr:last-child td { border-bottom: none; }
code { background: #e9ecef; padding: 2px 6px; border-radius: 3px; font-size: 0.85em; word-break: break-all; }
.bar-container { display: inline-block; min-width: 200px; }
.bar { display: inline-block; height: 16px; background: linear-gradient(90deg, var(--primary), #4dabf7);
       border-radius: 3px; vertical-align: middle; min-width: 2px; }
.bar-danger { background: linear-gradient(90deg, var(--danger), #e8726c); }
.bar-warning { background: linear-gradient(90deg, var(--warning), #ffda6a); }
.bar-success { background: linear-gradient(90deg, var(--success), #40c057); }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.78em; font-weight: 600; color: #fff; }
.badge-high { background: var(--danger); }
.badge-mid { background: var(--warning); color: #333; }
.badge-low { background: var(--info); color: #333; }
.badge-ok { background: var(--success); }
.alert { padding: 12px 16px; border-radius: 6px; margin: 10px 0; font-size: 0.9em; }
.alert-warning { background: #fff3cd; border: 1px solid #ffc107; color: #664d03; }
.alert-danger { background: #f8d7da; border: 1px solid #f5c2c7; color: #842029; }
.summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin: 12px 0 18px; }
.summary-card { background: var(--card); border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
                text-align: center; }
.summary-card .label { font-size: 0.82em; color: var(--muted); }
.summary-card .value { font-size: 1.5em; font-weight: 700; color: var(--primary); }
.num { text-align: right; font-variant-numeric: tabular-nums; }
hr { border: none; border-top: 2px solid var(--border); margin: 40px 0; }
.report-footer { text-align: center; padding: 30px 0 10px; color: var(--muted); font-size: 0.85em;
                 border-top: 2px solid var(--border); margin-top: 40px; }
.report-footer p { margin: 4px 0; }
.toc { position: fixed; top: 20px; right: 20px; width: 260px; max-height: calc(100vh - 40px);
       background: var(--card); border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,0.12);
       z-index: 1000; overflow: hidden; transition: all 0.3s ease; }
.toc.collapsed { width: 44px; max-height: 44px; border-radius: 22px; cursor: pointer; }
.toc-toggle { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px;
              background: var(--primary); color: #fff; cursor: pointer; font-size: 0.9em; font-weight: 600;
              user-select: none; }
.toc-toggle .toc-icon { font-size: 1.1em; transition: transform 0.3s; }
.toc.collapsed .toc-toggle { justify-content: center; padding: 10px; }
.toc.collapsed .toc-title, .toc.collapsed .toc-icon { display: none; }
.toc.collapsed .toc-toggle::after { content: "\\1F4D1"; font-size: 1.3em; }
.toc-body { overflow-y: auto; max-height: calc(100vh - 84px); padding: 8px 0; }
.toc.collapsed .toc-body { display: none; }
.toc-body a { display: block; padding: 6px 16px; color: var(--text); text-decoration: none;
              font-size: 0.82em; line-height: 1.4; border-left: 3px solid transparent; transition: all 0.15s; }
.toc-body a:hover { background: #f1f3f5; border-left-color: var(--primary); color: var(--primary); }
.toc-body a.active { background: #e7f1ff; border-left-color: var(--primary); color: var(--primary); font-weight: 600; }
.toc-group { padding: 8px 16px 4px; font-size: 0.78em; font-weight: 700; color: var(--primary);
             text-transform: uppercase; letter-spacing: 0.5px; border-top: 1px solid #e9ecef;
             margin-top: 4px; background: #f8f9fa; }
.section { margin-bottom: 4px; }
.section-header { cursor: pointer; user-select: none; position: relative; }
.section-header::after { content: "▾"; position: absolute; right: 12px; top: 50%; transform: translateY(-50%);
                         font-size: 0.8em; color: var(--muted); transition: transform 0.2s; }
.section.collapsed .section-header::after { transform: translateY(-50%) rotate(-90deg); }
.section-content { overflow: hidden; transition: max-height 0.3s ease; }
.section.collapsed .section-content { max-height: 0 !important; }
@media (max-width: 768px) {
  body { padding: 10px; }
  table { font-size: 0.82em; }
  .summary-grid { grid-template-columns: repeat(2, 1fr); }
  .toc { position: static; width: 100%; max-height: none; margin-bottom: 20px; border-radius: 8px; }
  .toc.collapsed { width: 100%; max-height: 44px; border-radius: 8px; }
  .toc-body { max-height: 300px; }
}
</style>
</head>
<body>
<div class="container">
"""

HTML_TAIL = """</div>
<script>
(function(){
  var sections = document.querySelectorAll('.section');
  var tocHtml = '<nav class="toc" id="toc"><div class="toc-toggle" onclick="toggleToc()"><span class="toc-title">目录导航</span><span class="toc-icon">◀</span></div><div class="toc-body" id="toc-body">';
  var lastGroup = null;
  sections.forEach(function(sec){
    var h2 = sec.querySelector('h2');
    if(h2){
      var id = sec.id;
      var group = sec.getAttribute('data-group') || '';
      if(group && group !== lastGroup){
        tocHtml += '<div class="toc-group">' + group + '</div>';
        lastGroup = group;
      }
      tocHtml += '<a href="#'+id+'" data-target="'+id+'">'+h2.textContent+'</a>';
    }
  });
  tocHtml += '</div></nav>';
  document.body.insertAdjacentHTML('beforeend', tocHtml);
  window.toggleToc = function(){ document.getElementById('toc').classList.toggle('collapsed'); };
  sections.forEach(function(sec){
    var header = sec.querySelector('.section-header');
    if(header){
      header.addEventListener('click', function(e){
        if(e.target.tagName === 'A') return;
        sec.classList.toggle('collapsed');
      });
    }
  });
  var tocLinks = document.querySelectorAll('.toc-body a');
  var sectionEls = Array.from(sections);
  function updateActive(){
    var scrollY = window.scrollY || window.pageYOffset;
    var current = '';
    sectionEls.forEach(function(sec){ if(sec.offsetTop - 80 <= scrollY) current = sec.id; });
    tocLinks.forEach(function(a){ a.classList.toggle('active', a.getAttribute('data-target') === current); });
  }
  window.addEventListener('scroll', updateActive);
  updateActive();
  tocLinks.forEach(function(a){
    a.addEventListener('click', function(e){
      e.preventDefault();
      var target = document.getElementById(this.getAttribute('data-target'));
      if(target) target.scrollIntoView({behavior:'smooth', block:'start'});
    });
  });
})();
</script>
</body>
</html>
"""


# ============================================================
# 报告生成器
# ============================================================


class MergeReportGenerator:
    """从已加载的 JSON 数据生成分析报告"""

    def __init__(self, datasets, top_n=20):
        """
        Args:
            datasets: list of loaded data dicts (已还原长 key)
            top_n: Top N 排行数量
        """
        self.datasets = datasets
        self.top_n = top_n
        # 收集所有目录的分析结果: {dir_label: data_dict}
        self.results = OrderedDict()
        self._collect_results()

    def _collect_results(self):
        """从多个数据文件收集所有目录级结果"""
        for ds in self.datasets:
            results = ds.get("results", {})
            hostname = ds.get("hostname", "")
            source = ds.get("export_time", "?")
            for dir_name, dir_data in results.items():
                # 多文件中可能有同名目录，优先用 hostname 区分，其次用导出时间
                label = dir_name
                if label in self.results:
                    suffix = hostname or source
                    label = f"{dir_name} ({suffix})"
                # 仍然冲突时追加完整信息
                if label in self.results:
                    label = f"{dir_name} ({hostname} {source})"
                self.results[label] = dir_data
        print(f"\n  {c(GREEN, '[汇总]')} 共 {len(self.results)} 个分析目录",
              file=sys.stderr)

    # ── 入口 ──────────────────────────────────────

    def generate_report(self, fmt="terminal"):
        if fmt == "markdown":
            return self._report_markdown()
        elif fmt == "html":
            return self._report_html()
        else:
            return self._report_terminal()

    def _report_terminal(self):
        buf = io.StringIO()
        W = 80
        for dir_name, data in self.results.items():
            buf.write("\n" + "=" * W + "\n")
            buf.write(f"  TDSQL Gateway 整合分析报告 - {dir_name}\n")
            buf.write("=" * W + "\n")
            self._write_overview(buf, data, fmt="terminal")
            if "interf" in data:
                self._write_interf_report(buf, data["interf"], fmt="terminal")
            if "sql" in data:
                self._write_sql_report(buf, data["sql"], fmt="terminal")
            if "slow_sql" in data:
                self._write_slow_sql_report(buf, data["slow_sql"], fmt="terminal")
            if "sys" in data:
                self._write_sys_report(buf, data["sys"], fmt="terminal")
            if "interf" in data:
                self._write_conclusions(buf, data, fmt="terminal")
        self._write_footer(buf, fmt="terminal")
        return buf.getvalue()

    def _report_markdown(self):
        buf = io.StringIO()
        for dir_name, data in self.results.items():
            buf.write(f"# TDSQL Gateway 整合分析报告 - {dir_name}\n\n")
            buf.write(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            self._write_overview(buf, data, fmt="md")
            if "interf" in data:
                self._write_interf_report(buf, data["interf"], fmt="md")
            if "sql" in data:
                self._write_sql_report(buf, data["sql"], fmt="md")
            if "slow_sql" in data:
                self._write_slow_sql_report(buf, data["slow_sql"], fmt="md")
            if "sys" in data:
                self._write_sys_report(buf, data["sys"], fmt="md")
            if "interf" in data:
                self._write_conclusions(buf, data, fmt="md")
            buf.write("\n---\n\n")
        self._write_footer(buf, fmt="md")
        return buf.getvalue()

    def _report_html(self):
        buf = io.StringIO()
        buf.write(HTML_HEAD)
        dir_names = list(self.results.keys())
        multi_dir = len(dir_names) > 1
        for idx, (dir_name, data) in enumerate(self.results.items()):
            self._sec_prefix = f"d{idx}-" if multi_dir else ""
            self._sec_group = dir_name if multi_dir else ""
            escaped_name = _h(dir_name)
            group_attr = f' data-group="{escaped_name}"' if multi_dir else ""
            buf.write(f'<h1 id="{self._sec_prefix}top"{group_attr}>'
                      f'TDSQL Gateway 整合分析报告 - {escaped_name}</h1>\n')
            buf.write(f'<p class="meta">生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>\n')
            self._write_overview(buf, data, fmt="html")
            if "interf" in data:
                self._write_interf_report(buf, data["interf"], fmt="html")
            if "sql" in data:
                self._write_sql_report(buf, data["sql"], fmt="html")
            if "slow_sql" in data:
                self._write_slow_sql_report(buf, data["slow_sql"], fmt="html")
            if "sys" in data:
                self._write_sys_report(buf, data["sys"], fmt="html")
            if "interf" in data:
                self._write_conclusions(buf, data, fmt="html")
            buf.write('<hr>\n')
        self._write_footer(buf, fmt="html")
        self._sec_prefix = ""
        self._sec_group = ""
        buf.write(HTML_TAIL)
        return buf.getvalue()

    # ── HTML section 辅助 ─────────────────────────

    def _html_section_start(self, buf, section_id, title):
        prefix = getattr(self, '_sec_prefix', '')
        group = getattr(self, '_sec_group', '')
        full_id = f"{prefix}{section_id}"
        group_attr = f' data-group="{_h(group)}"' if group else ''
        buf.write(f'<div class="section" id="{full_id}"{group_attr}>\n')
        buf.write(f'<h2 class="section-header">{_h(title)}</h2>\n')
        buf.write('<div class="section-content">\n')

    @staticmethod
    def _html_section_end(buf):
        buf.write('</div></div>\n')

    # ── 一、日志概览 ─────────────────────────────

    def _write_overview(self, buf, data, fmt="terminal"):
        ov = data.get("overview", {})
        if not ov:
            return
        is_html = fmt == "html"
        dates = ov.get("dates", [])
        log_types = ov.get("log_types", {})
        total_files = ov.get("total_files", sum(v.get("count", 0) for v in log_types.values()))
        total_size = ov.get("total_size", sum(v.get("size", 0) for v in log_types.values()))

        if fmt == "md":
            buf.write("## 一、日志概览\n\n")
            buf.write("| 项目 | 值 |\n|------|----|\n")
            buf.write(f"| 日期跨度 | {dates[0] if dates else 'N/A'} ~ {dates[-1] if dates else 'N/A'} ({len(dates)} 天) |\n")
            buf.write(f"| 文件总数 | {total_files} |\n")
            buf.write(f"| 总大小 | {_fmt_size(total_size)} |\n\n")
            buf.write("| 日志类型 | 说明 | 文件数 | 大小 | 端口 |\n")
            buf.write("|----------|------|--------|------|------|\n")
            for lt, info in sorted(log_types.items()):
                desc = LOG_TYPES.get(lt, lt)
                buf.write(f"| {lt}_instance | {desc} | {info.get('count', 0)} | {_fmt_size(info.get('size', 0))} | {info.get('port', '')} |\n")
            buf.write("\n")
        elif is_html:
            self._html_section_start(buf, 'sec-overview', '一、日志概览')
            date_range = f"{dates[0]} ~ {dates[-1]}" if dates else 'N/A'
            buf.write('<div class="summary-grid">\n')
            buf.write(f'<div class="summary-card"><div class="label">日期跨度</div><div class="value">{len(dates)} 天</div><div class="label">{_h(date_range)}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">文件总数</div><div class="value">{total_files}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">总大小</div><div class="value">{_fmt_size(total_size)}</div></div>\n')
            buf.write('</div>\n')
            buf.write('<table><tr><th>日志类型</th><th>说明</th><th>文件数</th><th>大小</th><th>端口</th></tr>\n')
            for lt, info in sorted(log_types.items()):
                desc = LOG_TYPES.get(lt, lt)
                buf.write(f'<tr><td><code>{_h(lt)}_instance</code></td><td>{_h(desc)}</td><td class="num">{info.get("count", 0)}</td><td class="num">{_fmt_size(info.get("size", 0))}</td><td>{_h(info.get("port", ""))}</td></tr>\n')
            buf.write('</table>\n')
            self._html_section_end(buf)
        else:
            buf.write(f"\n{'─'*80}\n")
            buf.write("【一、日志概览】\n")
            buf.write(f"  日期跨度: {dates[0] if dates else 'N/A'} ~ {dates[-1] if dates else 'N/A'} ({len(dates)} 天)\n")
            buf.write(f"  文件总数: {total_files}\n")
            buf.write(f"  总大小:   {_fmt_size(total_size)}\n\n")
            buf.write(f"  {'日志类型':<25} {'说明':<15} {'文件数':>5} {'大小':>10} {'端口':>6}\n")
            buf.write(f"  {'─'*70}\n")
            for lt, info in sorted(log_types.items()):
                desc = LOG_TYPES.get(lt, lt)
                buf.write(f"  {lt+'_instance':<25} {desc:<15} {info.get('count', 0):>5} {_fmt_size(info.get('size', 0)):>10} {info.get('port', ''):>6}\n")

    # ── 二~十、interf 报告 ───────────────────────

    def _write_interf_report(self, buf, interf, fmt="terminal"):
        is_md = fmt == "md"
        is_html = fmt == "html"
        sep = "\n" if is_md else f"\n{'─'*80}\n"

        # 二、每日请求量趋势
        daily_stats = interf.get("daily_stats", {})
        if daily_stats:
            if is_html:
                self._html_section_start(buf, 'sec-daily', '二、每日请求量趋势')
                buf.write('<table><tr><th>日期</th><th>请求量</th><th>平均耗时(ms)</th><th>趋势</th></tr>\n')
            elif is_md:
                buf.write("## 二、每日请求量趋势\n\n")
                buf.write("| 日期 | 请求量 | 平均耗时(ms) |\n|------|--------|-------------|\n")
            else:
                buf.write(f"{sep}【二、每日请求量趋势】\n")
                buf.write(f"  {'日期':<12} {'请求量':>10} {'平均耗时(ms)':>14}\n")
                buf.write(f"  {'─'*40}\n")
            max_daily = max((s.get("count", 0) for s in daily_stats.values()), default=1) or 1
            for date in sorted(daily_stats.keys()):
                stats = daily_stats[date]
                cnt = stats.get("count", 0)
                tc_count = stats.get("timecost_count", 0)
                avg = stats.get("timecost_sum", 0) / tc_count if tc_count > 0 else 0
                if is_html:
                    bar_w = int(cnt / max_daily * 150)
                    buf.write(f'<tr><td>{_h(date)}</td><td class="num">{cnt:,}</td><td class="num">{avg:.3f}</td>'
                              f'<td><div class="bar-container"><span class="bar" style="width:{bar_w}px"></span></div></td></tr>\n')
                elif is_md:
                    buf.write(f"| {date} | {cnt:,} | {avg:.3f} |\n")
                else:
                    buf.write(f"  {date:<12} {cnt:>10,} {avg:>14.3f}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

        # 三、每小时请求量分布
        hourly_counts = interf.get("hourly_counts", {})
        if hourly_counts:
            max_hourly = max(hourly_counts.values()) if hourly_counts else 1
            if is_html:
                if daily_stats:
                    self._html_section_end(buf)
                self._html_section_start(buf, 'sec-hourly', '三、每小时请求量分布')
                buf.write('<table><tr><th>时间</th><th>请求量</th><th>分布</th></tr>\n')
            elif is_md:
                buf.write("## 三、每小时请求量分布\n\n")
                buf.write("| 时间 | 请求量 | 柱状图 |\n|------|--------|--------|\n")
            else:
                buf.write(f"{sep}【三、每小时请求量分布】\n")
            for hour in sorted(hourly_counts.keys()):
                cnt = hourly_counts[hour]
                bar_len = int(cnt / max_hourly * 40)
                bar = "█" * bar_len
                if is_html:
                    bar_w = int(cnt / max_hourly * 250)
                    buf.write(f'<tr><td>{_h(hour)}</td><td class="num">{cnt:,}</td>'
                              f'<td><div class="bar-container"><span class="bar" style="width:{bar_w}px"></span></div></td></tr>\n')
                elif is_md:
                    buf.write(f"| {hour} | {cnt:,} | `{bar}` |\n")
                else:
                    buf.write(f"  {hour}  {cnt:>8,}  {bar}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

        # 最繁忙分钟
        busiest_minutes = interf.get("busiest_minutes", [])
        if busiest_minutes:
            if is_html:
                buf.write('<h3>最繁忙的分钟 Top 10</h3>\n')
                buf.write('<table><tr><th>时间(分钟)</th><th>请求量</th></tr>\n')
            elif is_md:
                buf.write("**最繁忙的分钟 Top 10:**\n\n| 时间(分钟) | 请求量 |\n|------------|--------|\n")
            else:
                buf.write("  最繁忙的分钟 Top 10:\n")
            for item in busiest_minutes:
                minute, cnt = item[0], item[1]
                if is_html:
                    buf.write(f'<tr><td>{_h(minute)}</td><td class="num">{cnt:,}</td></tr>\n')
                elif is_md:
                    buf.write(f"| {minute} | {cnt:,} |\n")
                else:
                    buf.write(f"    {minute}  {cnt:>6,}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

        # 四、SQL 耗时分布
        timecost_bins = interf.get("timecost_bins", {})
        if timecost_bins:
            total = sum(timecost_bins.values()) or 1
            bin_colors = {
                "<1ms": "bar bar-success", "1-5ms": "bar bar-success",
                "5-10ms": "bar", "10-50ms": "bar",
                "50-100ms": "bar bar-warning", "100-500ms": "bar bar-warning",
                "500ms-1s": "bar bar-danger", "1-3s": "bar bar-danger",
                "3-10s": "bar bar-danger", ">10s": "bar bar-danger",
            }
            if is_html:
                if hourly_counts:
                    self._html_section_end(buf)
                self._html_section_start(buf, 'sec-timecost', '四、SQL 耗时分布')
                buf.write('<table><tr><th>耗时区间</th><th>数量</th><th>占比</th><th>分布</th></tr>\n')
            elif is_md:
                buf.write("## 四、SQL 耗时分布\n\n")
                buf.write("| 耗时区间 | 数量 | 占比 | 分布 |\n|----------|------|------|------|\n")
            else:
                buf.write(f"{sep}【四、SQL 耗时分布】\n")
            for label in TIMECOST_BINS_LABELS:
                cnt = timecost_bins.get(label, 0)
                pct = cnt / total * 100
                bar = "█" * int(pct / 2)
                if is_html:
                    bar_w = max(int(pct * 2.5), 2) if cnt > 0 else 0
                    cls = bin_colors.get(label, "bar")
                    buf.write(f'<tr><td>{_h(label)}</td><td class="num">{cnt:,}</td><td class="num">{pct:.1f}%</td>'
                              f'<td><div class="bar-container"><span class="{cls}" style="width:{bar_w}px"></span></div></td></tr>\n')
                elif is_md:
                    buf.write(f"| {label} | {cnt:,} | {pct:.1f}% | `{bar}` |\n")
                else:
                    buf.write(f"  {label:<12} {cnt:>10,}  ({pct:>5.1f}%)  {bar}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

        # 五、高耗时 SQL Top N
        high_timecost = interf.get("high_timecost", [])
        if high_timecost:
            if is_html:
                if timecost_bins:
                    self._html_section_end(buf)
                self._html_section_start(buf, 'sec-high-tc', f'五、高耗时 SQL Top {self.top_n}')
                buf.write('<table><tr><th>#</th><th>耗时(ms)</th><th>数据库</th><th>SQL</th><th>时间</th></tr>\n')
            elif is_md:
                buf.write(f"## 五、高耗时 SQL Top {self.top_n}\n\n")
                buf.write("| # | 耗时(ms) | 数据库 | SQL | 时间 |\n|---|----------|--------|-----|------|\n")
            else:
                buf.write(f"{sep}【五、高耗时 SQL Top {self.top_n}】\n")
            for i, item in enumerate(high_timecost[:self.top_n], 1):
                tc, sql, db, ts = item[0], item[1][:100], item[2] if len(item) > 2 else "", item[3] if len(item) > 3 else ""
                sql_short = sql.replace("|", "\\|").replace("\n", " ")
                if is_html:
                    buf.write(f'<tr><td>{i}</td><td class="num">{tc:.1f}</td><td>{_h(db)}</td>'
                              f'<td><code>{_h(sql_short)}</code></td><td>{_h(ts)}</td></tr>\n')
                elif is_md:
                    buf.write(f"| {i} | {tc:.1f} | {db} | `{sql_short}` | {ts} |\n")
                else:
                    buf.write(f"  [{i:>2}] {tc:>10.1f}ms  {db:<20}  {ts}\n")
                    buf.write(f"       {sql_short}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

        # 六、高频 SQL 模式 Top N
        sql_patterns = interf.get("sql_patterns", [])
        if sql_patterns:
            if is_html:
                if high_timecost:
                    self._html_section_end(buf)
                self._html_section_start(buf, 'sec-patterns', f'六、高频 SQL 模式 Top {self.top_n}')
                buf.write('<table><tr><th>#</th><th>次数</th><th>SQL模式</th></tr>\n')
            elif is_md:
                buf.write(f"## 六、高频 SQL 模式 Top {self.top_n}\n\n")
                buf.write("| # | 次数 | SQL模式 |\n|---|------|--------|\n")
            else:
                buf.write(f"{sep}【六、高频 SQL 模式 Top {self.top_n}】\n")
            for i, item in enumerate(sql_patterns[:self.top_n], 1):
                pattern, cnt = item[0], item[1]
                if is_html:
                    buf.write(f'<tr><td>{i}</td><td class="num">{cnt:,}</td><td><code>{_h(pattern)}</code></td></tr>\n')
                elif is_md:
                    buf.write(f"| {i} | {cnt:,} | `{pattern}` |\n")
                else:
                    buf.write(f"  [{i:>2}] {cnt:>8,}  {pattern}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

        # 七、SQL 类型分布
        sql_type_counts = interf.get("sql_type_counts", {})
        if sql_type_counts:
            type_total = sum(sql_type_counts.values()) or 1
            sorted_types = sorted(sql_type_counts.items(), key=lambda x: -x[1])
            if is_html:
                if sql_patterns:
                    self._html_section_end(buf)
                self._html_section_start(buf, 'sec-sql-type', '七、SQL 类型分布')
                buf.write('<table><tr><th>sql_type</th><th>含义</th><th>数量</th><th>占比</th><th>分布</th></tr>\n')
            elif is_md:
                buf.write("## 七、SQL 类型分布\n\n")
                buf.write("| sql_type | 含义 | 数量 | 占比 |\n|----------|------|------|------|\n")
            else:
                buf.write(f"{sep}【七、SQL 类型分布】\n")
            for st, cnt in sorted_types:
                desc = SQL_TYPE_MAP.get(st, f"类型{st}")
                pct = cnt / type_total * 100
                if is_html:
                    bar_w = max(int(pct * 2), 2) if cnt > 0 else 0
                    buf.write(f'<tr><td>{_h(st)}</td><td>{_h(desc)}</td><td class="num">{cnt:,}</td><td class="num">{pct:.1f}%</td>'
                              f'<td><div class="bar-container"><span class="bar" style="width:{bar_w}px"></span></div></td></tr>\n')
                elif is_md:
                    buf.write(f"| {st} | {desc} | {cnt:,} | {pct:.1f}% |\n")
                else:
                    buf.write(f"  sql_type={st:<4}  {desc:<14}  {cnt:>10,}  ({pct:>5.1f}%)\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

        # 八、用户 & 数据库分布
        user_counts = interf.get("user_counts", {})
        db_counts = interf.get("db_counts", {})
        if user_counts or db_counts:
            if is_html:
                if sql_type_counts:
                    self._html_section_end(buf)
                self._html_section_start(buf, 'sec-user-db', '八、用户 & 数据库分布')
            elif is_md:
                buf.write("## 八、用户 & 数据库分布\n\n")
            else:
                buf.write(f"{sep}【八、用户 & 数据库分布】\n")

            if user_counts:
                user_total = sum(user_counts.values()) or 1
                sorted_users = sorted(user_counts.items(), key=lambda x: -x[1])[:10]
                if is_html:
                    buf.write('<h3>用户分布</h3>\n<table><tr><th>用户</th><th>请求数</th><th>占比</th></tr>\n')
                elif is_md:
                    buf.write("**用户分布:**\n\n| 用户 | 请求数 | 占比 |\n|------|--------|------|\n")
                else:
                    buf.write("  用户分布:\n")
                for user, cnt in sorted_users:
                    pct = cnt / user_total * 100
                    if is_html:
                        buf.write(f'<tr><td>{_h(user)}</td><td class="num">{cnt:,}</td><td class="num">{pct:.1f}%</td></tr>\n')
                    elif is_md:
                        buf.write(f"| {user} | {cnt:,} | {pct:.1f}% |\n")
                    else:
                        buf.write(f"    {user:<30} {cnt:>10,}  ({pct:>5.1f}%)\n")
                if is_html:
                    buf.write('</table>\n')
                buf.write("\n")

            if db_counts:
                db_total = sum(db_counts.values()) or 1
                sorted_dbs = sorted(db_counts.items(), key=lambda x: -x[1])[:10]
                if is_html:
                    buf.write('<h3>数据库分布</h3>\n<table><tr><th>数据库</th><th>请求数</th><th>占比</th></tr>\n')
                elif is_md:
                    buf.write("**数据库分布:**\n\n| 数据库 | 请求数 | 占比 |\n|--------|--------|------|\n")
                else:
                    buf.write("  数据库分布:\n")
                for db, cnt in sorted_dbs:
                    pct = cnt / db_total * 100
                    if is_html:
                        buf.write(f'<tr><td>{_h(db)}</td><td class="num">{cnt:,}</td><td class="num">{pct:.1f}%</td></tr>\n')
                    elif is_md:
                        buf.write(f"| {db} | {cnt:,} | {pct:.1f}% |\n")
                    else:
                        buf.write(f"    {db:<30} {cnt:>10,}  ({pct:>5.1f}%)\n")
                if is_html:
                    buf.write('</table>\n')
                buf.write("\n")

        # 九、错误码分析
        resultcode_counts = interf.get("resultcode_counts", {})
        if resultcode_counts:
            rc_total = sum(resultcode_counts.values()) or 1
            sorted_rc = sorted(resultcode_counts.items(), key=lambda x: -x[1])
            if is_html:
                if user_counts or db_counts:
                    self._html_section_end(buf)
                self._html_section_start(buf, 'sec-errors', '九、错误码分析')
                buf.write('<table><tr><th>错误码</th><th>含义</th><th>数量</th><th>占比</th></tr>\n')
            elif is_md:
                buf.write("## 九、错误码分析\n\n")
                buf.write("| 错误码 | 含义 | 数量 | 占比 |\n|--------|------|------|------|\n")
            else:
                buf.write(f"{sep}【九、错误码分析】\n")
            for rc, cnt in sorted_rc:
                desc = RESULTCODE_MAP.get(rc, f"错误{rc}")
                pct = cnt / rc_total * 100
                if is_html:
                    cls = '' if rc == '0' else ' style="color:var(--danger);font-weight:600"'
                    buf.write(f'<tr><td{cls}>{_h(rc)}</td><td>{_h(desc)}</td><td class="num">{cnt:,}</td><td class="num">{pct:.1f}%</td></tr>\n')
                elif is_md:
                    buf.write(f"| {rc} | {desc} | {cnt:,} | {pct:.1f}% |\n")
                else:
                    buf.write(f"  code={rc:<6}  {desc:<20}  {cnt:>10,}  ({pct:>5.1f}%)\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

            # 错误详情
            error_details = interf.get("error_details", [])
            if error_details:
                if is_html:
                    buf.write('<h3>错误请求详情 (最多50条)</h3>\n')
                    buf.write('<table><tr><th>时间</th><th>错误码</th><th>数据库</th><th>错误信息</th><th>SQL</th></tr>\n')
                elif is_md:
                    buf.write("**错误请求详情:**\n\n| 时间 | 错误码 | 数据库 | 错误信息 | SQL |\n|------|--------|--------|----------|-----|\n")
                else:
                    buf.write("  错误请求详情:\n")
                for item in error_details[:20]:
                    rc = item[0]
                    sql = str(item[1])[:80].replace("|", "\\|") if len(item) > 1 else ""
                    db = item[2] if len(item) > 2 else ""
                    errinfo = str(item[3])[:80].replace("|", "\\|") if len(item) > 3 else ""
                    ts = item[4] if len(item) > 4 else ""
                    desc = RESULTCODE_MAP.get(rc, rc)
                    if is_html:
                        buf.write(f'<tr><td>{_h(ts)}</td><td>{_h(rc)}({_h(desc)})</td><td>{_h(db)}</td>'
                                  f'<td><code>{_h(errinfo)}</code></td><td><code>{_h(sql)}</code></td></tr>\n')
                    elif is_md:
                        buf.write(f"| {ts} | {rc}({desc}) | {db} | `{errinfo}` | `{sql}` |\n")
                    else:
                        buf.write(f"    [{ts}] code={rc}({desc}) db={db}\n")
                        buf.write(f"      错误: {errinfo}\n")
                        buf.write(f"      SQL:  {sql}\n")
                if is_html:
                    buf.write('</table>\n')
                buf.write("\n")

        # 十、连接模式分析
        stc = interf.get("sql_type_counts", {})
        conn_count = stc.get("11", 0)
        quit_count = stc.get("1", 0)
        total_lines = interf.get("total_lines", 0)
        new_conn_count = interf.get("new_conn_count", 0)
        if is_html:
            if resultcode_counts:
                self._html_section_end(buf)
            self._html_section_start(buf, 'sec-conn', '十、连接模式分析')
            buf.write('<div class="summary-grid">\n')
            buf.write(f'<div class="summary-card"><div class="label">新建连接</div><div class="value">{conn_count:,}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">断开连接</div><div class="value">{quit_count:,}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">new_connnum 累计</div><div class="value">{new_conn_count:,}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">总请求量</div><div class="value">{total_lines:,}</div></div>\n')
            if total_lines > 0:
                conn_ratio = conn_count / total_lines * 100
                buf.write(f'<div class="summary-card"><div class="label">连接建立占比</div><div class="value">{conn_ratio:.1f}%</div></div>\n')
                if conn_ratio > 10:
                    buf.write(f'<div class="alert alert-warning">⚠ 连接建立占比 {conn_ratio:.1f}%，疑似短连接模式，建议使用连接池。</div>\n')
            buf.write('</div>\n')
            self._html_section_end(buf)
        elif is_md:
            buf.write("## 十、连接模式分析\n\n")
            buf.write("| 指标 | 值 |\n|------|----|\n")
            buf.write(f"| 新建连接(sql_type=11) | {conn_count:,} |\n")
            buf.write(f"| 断开连接(sql_type=1) | {quit_count:,} |\n")
            buf.write(f"| new_connnum 累计 | {new_conn_count:,} |\n")
            buf.write(f"| 总请求量 | {total_lines:,} |\n")
            if total_lines > 0:
                conn_ratio = conn_count / total_lines * 100
                buf.write(f"| 连接建立占比 | {conn_ratio:.1f}% |\n")
                if conn_ratio > 10:
                    buf.write(f"\n> **警告**: 连接建立占比 {conn_ratio:.1f}%，疑似短连接模式，建议使用连接池。\n")
            buf.write("\n")
        else:
            buf.write(f"{sep}【十、连接模式分析】\n")
            buf.write(f"  新建连接(sql_type=11):  {conn_count:>10,}\n")
            buf.write(f"  断开连接(sql_type=1):   {quit_count:>10,}\n")
            buf.write(f"  new_connnum 累计:       {new_conn_count:>10,}\n")
            buf.write(f"  总请求量:               {total_lines:>10,}\n")
            if total_lines > 0:
                conn_ratio = conn_count / total_lines * 100
                buf.write(f"  连接建立占比:           {conn_ratio:>9.1f}%\n")
                if conn_ratio > 10:
                    buf.write(f"\n  ⚠ 警告: 连接建立占比 {conn_ratio:.1f}%，疑似短连接模式，建议使用连接池。\n")
            buf.write("\n")

    # ── SQL 执行层 ────────────────────────────────

    def _write_sql_report(self, buf, sql_data, fmt="terminal"):
        is_md = fmt == "md"
        is_html = fmt == "html"
        high_timecost = sql_data.get("high_timecost", [])
        if not high_timecost:
            return
        if is_html:
            self._html_section_start(buf, 'sec-sql-tc', f'SQL执行层高耗时 Top {self.top_n}')
            buf.write('<table><tr><th>#</th><th>耗时(ms)</th><th>用户</th><th>SQL</th><th>时间</th></tr>\n')
        elif is_md:
            buf.write(f"## SQL执行层高耗时 Top {self.top_n}\n\n")
            buf.write("| # | 耗时(ms) | 用户 | SQL | 时间 |\n|---|----------|------|-----|------|\n")
        else:
            buf.write(f"\n{'─'*80}\n")
            buf.write(f"【SQL执行层高耗时 Top {self.top_n}】\n")
        for i, item in enumerate(high_timecost[:self.top_n], 1):
            tc = item[0]
            sql = str(item[1])[:100].replace("|", "\\|")
            user = item[2] if len(item) > 2 else ""
            ts = item[3] if len(item) > 3 else ""
            if is_html:
                buf.write(f'<tr><td>{i}</td><td class="num">{tc:.1f}</td><td>{_h(user)}</td>'
                          f'<td><code>{_h(sql)}</code></td><td>{_h(ts)}</td></tr>\n')
            elif is_md:
                buf.write(f"| {i} | {tc:.1f} | {user} | `{sql}` | {ts} |\n")
            else:
                buf.write(f"  [{i:>2}] {tc:>10.1f}ms  user={user}  {ts}\n")
                buf.write(f"       {sql}\n")
        if is_html:
            buf.write('</table>\n')
            self._html_section_end(buf)
        buf.write("\n")

    # ── 慢 SQL ────────────────────────────────────

    def _write_slow_sql_report(self, buf, slow_data, fmt="terminal"):
        is_md = fmt == "md"
        is_html = fmt == "html"
        sep = "\n" if is_md else f"\n{'─'*80}\n"
        total_count = slow_data.get("total_count", 0)

        if is_html:
            self._html_section_start(buf, 'sec-slow-sql', '十一、慢SQL日志分析')
            buf.write(f'<p>慢SQL总数: <strong>{total_count}</strong> 条</p>\n')
        elif is_md:
            buf.write("## 十一、慢SQL日志分析\n\n")
            buf.write(f"慢SQL总数: **{total_count}** 条\n\n")
        else:
            buf.write(f"{sep}【十一、慢SQL日志分析】\n")
            buf.write(f"  慢SQL总数: {total_count} 条\n\n")

        # 每日分布
        daily_counts = slow_data.get("daily_counts", {})
        if daily_counts:
            if is_html:
                buf.write('<h3>每日慢SQL数</h3>\n<table><tr><th>日期</th><th>数量</th></tr>\n')
            elif is_md:
                buf.write("**每日慢SQL数:**\n\n| 日期 | 数量 |\n|------|------|\n")
            else:
                buf.write("  每日慢SQL数:\n")
            for date in sorted(daily_counts.keys()):
                cnt = daily_counts[date]
                if is_html:
                    buf.write(f'<tr><td>{_h(date)}</td><td class="num">{cnt}</td></tr>\n')
                elif is_md:
                    buf.write(f"| {date} | {cnt} |\n")
                else:
                    buf.write(f"    {date}: {cnt}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

        # Schema 分布
        schema_counts = slow_data.get("schema_counts", {})
        if schema_counts:
            sorted_schemas = sorted(schema_counts.items(), key=lambda x: -x[1])
            if is_html:
                buf.write('<h3>按Schema分布</h3>\n<table><tr><th>Schema</th><th>数量</th></tr>\n')
            elif is_md:
                buf.write("**按Schema分布:**\n\n| Schema | 数量 |\n|--------|------|\n")
            else:
                buf.write("  按Schema分布:\n")
            for schema, cnt in sorted_schemas:
                if is_html:
                    buf.write(f'<tr><td>{_h(schema)}</td><td class="num">{cnt}</td></tr>\n')
                elif is_md:
                    buf.write(f"| {schema} | {cnt} |\n")
                else:
                    buf.write(f"    {schema}: {cnt}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

        # Top 慢SQL
        top_slow = slow_data.get("top_slow", [])
        if top_slow:
            if is_html:
                buf.write(f'<h3>Top {self.top_n} 慢SQL</h3>\n')
                buf.write('<table><tr><th>#</th><th>耗时(s)</th><th>Schema</th><th>用户</th><th>SQL</th></tr>\n')
            elif is_md:
                buf.write(f"**Top {self.top_n} 慢SQL:**\n\n")
                buf.write("| # | 耗时(s) | Schema | 用户 | SQL |\n|---|---------|--------|------|-----|\n")
            else:
                buf.write(f"  Top {self.top_n} 慢SQL:\n")
            for i, block in enumerate(top_slow[:self.top_n], 1):
                qt = block.get("query_time", 0)
                schema = block.get("schema", "")
                user = block.get("user", "")
                sql = block.get("sql", "")[:120].replace("|", "\\|").replace("\n", " ")
                if is_html:
                    buf.write(f'<tr><td>{i}</td><td class="num">{qt:.3f}</td><td>{_h(schema)}</td>'
                              f'<td>{_h(user)}</td><td><code>{_h(sql)}</code></td></tr>\n')
                elif is_md:
                    buf.write(f"| {i} | {qt:.3f} | {schema} | {user} | `{sql}` |\n")
                else:
                    buf.write(f"    [{i:>2}] {qt:>8.3f}s  schema={schema}  user={user}\n")
                    buf.write(f"         {sql}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")
        if is_html:
            self._html_section_end(buf)

    # ── 系统日志 ──────────────────────────────────

    def _write_sys_report(self, buf, sys_data, fmt="terminal"):
        is_md = fmt == "md"
        is_html = fmt == "html"
        sep = "\n" if is_md else f"\n{'─'*80}\n"

        if is_html:
            self._html_section_start(buf, 'sec-sys', '十二、系统日志异常检测')
        elif is_md:
            buf.write("## 十二、系统日志异常检测\n\n")
        else:
            buf.write(f"{sep}【十二、系统日志异常检测】\n")

        daily_counts = sys_data.get("daily_counts", {})
        if daily_counts:
            counts = list(daily_counts.values())
            avg_count = sum(counts) / len(counts) if counts else 0
            if is_html:
                buf.write('<h3>每日系统日志条数</h3>\n<table><tr><th>日期</th><th>行数</th><th>状态</th></tr>\n')
            elif is_md:
                buf.write("**每日系统日志条数:**\n\n| 日期 | 行数 | 状态 |\n|------|------|------|\n")
            else:
                buf.write("  每日系统日志条数:\n")
            for date in sorted(daily_counts.keys()):
                cnt = daily_counts[date]
                is_abnormal = cnt > avg_count * 3 and cnt > 100
                status = ""
                if is_abnormal:
                    if is_html:
                        status = '<span class="badge badge-high">异常偏高</span>'
                    elif is_md:
                        status = " **异常偏高**"
                    else:
                        status = " ⚠ 异常偏高"
                if is_html:
                    row_style = ' style="background:#fff3cd"' if is_abnormal else ''
                    buf.write(f'<tr{row_style}><td>{_h(date)}</td><td class="num">{cnt:,}</td><td>{status}</td></tr>\n')
                elif is_md:
                    buf.write(f"| {date} | {cnt:,} | {status} |\n")
                else:
                    buf.write(f"    {date}: {cnt:>6,}{status}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

        # 关键异常统计
        zk = sys_data.get("zk_errors", 0)
        eto = sys_data.get("event_timeout", 0)
        sse = sys_data.get("sql_syntax_errors", 0)
        if is_html:
            buf.write('<h3>关键异常统计</h3>\n<div class="summary-grid">\n')
            zk_cls = "danger" if zk > 0 else "muted"
            ev_cls = "danger" if eto > 0 else "muted"
            sq_cls = "danger" if sse > 0 else "muted"
            buf.write(f'<div class="summary-card"><div class="label">ZooKeeper 错误</div><div class="value" style="color:var(--{zk_cls})">{zk}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">事件处理超时</div><div class="value" style="color:var(--{ev_cls})">{eto}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">SQL语法错误</div><div class="value" style="color:var(--{sq_cls})">{sse}</div></div>\n')
            buf.write('</div>\n')
        elif is_md:
            buf.write("**关键异常统计:**\n\n| 异常类型 | 数量 |\n|----------|------|\n")
            buf.write(f"| ZooKeeper 错误 | {zk} |\n| 事件处理超时 | {eto} |\n| SQL语法错误 | {sse} |\n\n")
        else:
            buf.write("  关键异常统计:\n")
            buf.write(f"    ZooKeeper 错误:   {zk:>6}\n    事件处理超时:     {eto:>6}\n    SQL语法错误:      {sse:>6}\n\n")

        # 错误来源 Top
        error_types = sys_data.get("error_types", [])
        if error_types:
            if is_html:
                buf.write('<h3>错误来源 Top</h3>\n<table><tr><th>源文件:函数</th><th>次数</th></tr>\n')
            elif is_md:
                buf.write("**错误来源 Top:**\n\n| 源文件:函数 | 次数 |\n|------------|------|\n")
            else:
                buf.write("  错误来源 Top:\n")
            for item in error_types[:15]:
                src, cnt = item[0], item[1]
                if is_html:
                    buf.write(f'<tr><td><code>{_h(src)}</code></td><td class="num">{cnt}</td></tr>\n')
                elif is_md:
                    buf.write(f"| {src} | {cnt} |\n")
                else:
                    buf.write(f"    {cnt:>6}  {src}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")
        if is_html:
            self._html_section_end(buf)

    # ── 结论 ──────────────────────────────────────

    def _write_conclusions(self, buf, data, fmt="terminal"):
        is_md = fmt == "md"
        is_html = fmt == "html"
        sep = "\n" if is_md else f"\n{'─'*80}\n"

        if is_html:
            self._html_section_start(buf, 'sec-conclusions', '十三、核心结论与建议')
        elif is_md:
            buf.write("## 十三、核心结论与建议\n\n")
            buf.write("| # | 发现 | 严重程度 | 建议 |\n|---|------|----------|------|\n")
        else:
            buf.write(f"{sep}【十三、核心结论与建议】\n\n")

        findings = []
        interf = data.get("interf", {})
        sys_data = data.get("sys", {})
        slow_data = data.get("slow_sql", {})

        # 1. 短连接检测
        if interf:
            stc = interf.get("sql_type_counts", {})
            conn_count = stc.get("11", 0)
            total = interf.get("total_lines", 1)
            if total > 0 and conn_count / total > 0.10:
                ratio = conn_count / total * 100
                findings.append((f"短连接模式 - 连接建立占比 {ratio:.1f}%", "中", "改用连接池或长连接，减少连接开销"))

        # 2. 高耗时SQL
        ht = interf.get("high_timecost", [])
        if ht:
            top_tc = ht[0]
            if top_tc[0] > 1000:
                db = top_tc[2] if len(top_tc) > 2 else ""
                sql = str(top_tc[1])[:60] if len(top_tc) > 1 else ""
                findings.append((f"存在超高耗时SQL: {top_tc[0]:.0f}ms ({db})", "高", f"优化 SQL 或添加索引: {sql}"))
            elif top_tc[0] > 100:
                findings.append((f"存在高耗时SQL: {top_tc[0]:.1f}ms", "中", "检查SQL执行计划，考虑添加索引"))

        # 3. 错误码
        if interf:
            for rc, cnt in interf.get("resultcode_counts", {}).items():
                if rc != "0" and cnt > 0:
                    desc = RESULTCODE_MAP.get(rc, f"错误{rc}")
                    findings.append((f"存在错误请求: code={rc}({desc}), 共{cnt}次", "中" if cnt > 10 else "低", "排查错误原因，修复相关SQL"))

        # 4. 系统日志异常
        if sys_data:
            counts = list(sys_data.get("daily_counts", {}).values())
            if counts:
                avg_c = sum(counts) / len(counts)
                for date, cnt in sys_data.get("daily_counts", {}).items():
                    if cnt > avg_c * 3 and cnt > 100:
                        findings.append((f"{date} 系统日志暴增至 {cnt} 行（平均 {avg_c:.0f} 行）", "高", "排查当日新上线的SQL/操作，检查事件超时"))
                        break
            if sys_data.get("zk_errors", 0) > 0:
                findings.append((f"ZooKeeper 连接异常: {sys_data['zk_errors']} 次", "中", "检查 ZK 集群健康状态和网络连通性"))
            if sys_data.get("event_timeout", 0) > 0:
                findings.append((f"事件处理超时: {sys_data['event_timeout']} 次", "中", "检查 Proxy 负载和后端 DB 响应时间"))

        # 5. 慢SQL
        if slow_data and slow_data.get("total_count", 0) > 0:
            findings.append((f"慢SQL日志共 {slow_data['total_count']} 条", "中" if slow_data["total_count"] > 10 else "低", "优化慢SQL，添加索引或改写查询"))

        # 6. 平均耗时趋势
        if interf:
            daily = interf.get("daily_stats", {})
            avgs = {}
            for date, stats in daily.items():
                tc_count = stats.get("timecost_count", 0)
                if tc_count > 0:
                    avgs[date] = stats.get("timecost_sum", 0) / tc_count
            if len(avgs) >= 3:
                vals = list(avgs.values())
                overall_avg = sum(vals) / len(vals)
                for date in sorted(avgs.keys()):
                    if avgs[date] > overall_avg * 1.3:
                        findings.append((f"{date} 平均耗时 {avgs[date]:.3f}ms（整体均值 {overall_avg:.3f}ms，偏高 {((avgs[date]/overall_avg)-1)*100:.0f}%）", "中", "关联系统日志检查是否有异常事件"))

        # 7. 请求集中度
        if interf:
            hourly = interf.get("hourly_counts", {})
            if hourly:
                vals = list(hourly.values())
                max_h = max(vals)
                min_h = min(vals) if min(vals) > 0 else 1
                if max_h / min_h > 2:
                    max_hour = max(hourly, key=hourly.get)
                    findings.append((f"请求量峰谷比 {max_h/min_h:.1f}x，高峰: {max_hour} ({max_h:,})", "低", "如有定时任务，考虑分散到低峰时段"))

        if not findings:
            findings.append(("各项指标正常，未发现明显异常", "无", "继续保持监控"))

        severity_icon = {"高": "🔴", "中": "⚠️", "低": "ℹ️", "无": "✅"}
        severity_badge = {"高": "badge-high", "中": "badge-mid", "低": "badge-low", "无": "badge-ok"}
        if is_html:
            buf.write('<table><tr><th>#</th><th>发现</th><th>严重程度</th><th>建议</th></tr>\n')
        for i, (finding, severity, advice) in enumerate(findings, 1):
            icon = severity_icon.get(severity, "")
            if is_html:
                badge_cls = severity_badge.get(severity, "badge-ok")
                buf.write(f'<tr><td>{i}</td><td>{_h(finding)}</td>'
                          f'<td><span class="badge {badge_cls}">{icon} {_h(severity)}</span></td>'
                          f'<td>{_h(advice)}</td></tr>\n')
            elif is_md:
                buf.write(f"| {i} | {finding} | {icon} {severity} | {advice} |\n")
            else:
                buf.write(f"  [{i}] [{severity}] {finding}\n")
                buf.write(f"       建议: {advice}\n\n")
        if is_html:
            buf.write('</table>\n')
            self._html_section_end(buf)
        elif not is_md:
            buf.write("\n")

    # ── 底部 ──────────────────────────────────────

    def _write_footer(self, buf, fmt="terminal"):
        gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        src_info = f"来源: {len(self.datasets)} 个数据文件"
        if fmt == "html":
            buf.write('<footer class="report-footer">\n')
            buf.write(f'  <p>Generated by <strong>TDSQL Gateway Merge Report Tool</strong> v{VERSION}</p>\n')
            buf.write(f'  <p>{_h(src_info)} | {_h(gen_time)}</p>\n')
            buf.write('</footer>\n')
        elif fmt == "md":
            buf.write("\n---\n\n")
            buf.write(f"*Generated by TDSQL Gateway Merge Report Tool v{VERSION} | {src_info} | {gen_time}*\n")
        else:
            buf.write(f"\n{'═'*80}\n")
            buf.write(f"  Generated by TDSQL Gateway Merge Report Tool v{VERSION}\n")
            buf.write(f"  {src_info} | {gen_time}\n")
            buf.write(f"{'═'*80}\n")


# ============================================================
# 主程序
# ============================================================


def main():
    print_banner()

    parser = argparse.ArgumentParser(
        description="TDSQL Gateway 日志整合分析 - 读取 JSON/JSON.GZ 数据文件生成报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  用法示例:

  # 从单个 JSON 文件生成终端报告
  python3 merge_gateway_reports.py data.json

  # 多个 JSON.GZ 文件合并生成 HTML 报告
  python3 merge_gateway_reports.py node1.json.gz node2.json.gz -o merged.html

  # 通配符批量加载
  python3 merge_gateway_reports.py *.json.gz -o report.html

  # 输出多种格式
  python3 merge_gateway_reports.py data.json.gz -o report.html -o report.md
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")
    parser.add_argument("files", nargs="+", metavar="FILE",
                        help="JSON 或 JSON.GZ 数据文件（由 analyze_gateway_log.py 导出）")
    parser.add_argument("-o", "--output", action="append", default=[], metavar="FILE",
                        help="输出文件（.html/.md/.txt），可指定多个。不指定则输出到终端")
    parser.add_argument("-f", "--format", choices=["terminal", "markdown", "html"],
                        help="输出格式（仅在扩展名无法识别时生效）")
    parser.add_argument("-n", "--top-n", type=int, default=20,
                        help="Top N 排行数量（默认: 20）")
    parser.add_argument("-v", "--version", action="version",
                        version=f"TDSQL Gateway Merge Report Tool v{VERSION}")

    if len(sys.argv) < 2:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    # 加载所有数据文件
    print(f"\n  {c(BOLD, '加载数据文件...')}", file=sys.stderr)
    datasets = []
    for filepath in args.files:
        if not os.path.exists(filepath):
            print(f"  {c(YELLOW, '[警告]')} 文件不存在: {filepath}", file=sys.stderr)
            continue
        data = load_data_file(filepath)
        if data:
            datasets.append(data)

    if not datasets:
        print(f"\n  {c(RED, '[错误]')} 未加载到任何有效数据文件", file=sys.stderr)
        sys.exit(1)

    # 生成报告
    generator = MergeReportGenerator(datasets, top_n=args.top_n)

    # 扩展名格式映射
    _EXT_FMT = {
        ".html": "html", ".htm": "html",
        ".md": "markdown", ".txt": "terminal",
    }

    def _detect_fmt(path):
        low = path.lower()
        for ext, fmt in _EXT_FMT.items():
            if low.endswith(ext):
                return fmt
        return None

    outputs = args.output or []
    for out_path in outputs:
        fmt = _detect_fmt(out_path) or args.format or "terminal"
        report = generator.generate_report(fmt=fmt)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report)
        fsize = os.path.getsize(out_path)
        print(f"\n  {c(GREEN, '[保存]')} 报告已保存到: {out_path} ({_fmt_size(fsize)})", file=sys.stderr)

    if not outputs:
        fmt = args.format or "terminal"
        report = generator.generate_report(fmt=fmt)
        print(report)

    print("", file=sys.stderr)


if __name__ == "__main__":
    main()
