#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
interf 深度分析报告生成器（本地运行）

读取 interf_deep_analysis.py 生成的 CSV 文件，分析 SQL 性能风险并生成 HTML 报告。
本脚本在本地运行，不需要连接数据库。

输入:
  output/{日期}/ 目录下的:
  - *_sql_explain_schema.csv  (EXPLAIN 执行计划+表结构)
  - *_sql_timecost_detail.csv (耗时区间统计)

输出:
  HTML 报告，包含:
  1. SQL 耗时区间 × 类型统计表（每个实例）
  2. 未走索引的 SQL（全表扫描，风险最高）
  3. 高频 SQL Top N（执行次数最多）
  4. 走了索引但扫描行数过大的 SQL（低效索引）

用法:
  # 分析某天所有实例，汇总到一个 HTML
  python3 interf_report_generator.py -d output/2026-04-01 --mode merge

  # 每个实例单独生成一个 HTML
  python3 interf_report_generator.py -d output/2026-04-01 --mode split

  # 指定输出目录
  python3 interf_report_generator.py -d output/2026-04-01 --mode merge -o /tmp/reports/

作者: lynx
版本: v1.0
"""

import argparse
import csv
import html as html_mod
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

VERSION = "1.0"

def _h(text):
    """HTML 转义"""
    return html_mod.escape(str(text))


# ============================================================
# CSV 解析
# ============================================================

def find_csv_groups(data_dir):
    """扫描目录，按业务实例分组找到 CSV 文件对"""
    groups = {}  # {prefix: {explain: path, timecost: path, name, ip, port, date}}

    for fname in sorted(os.listdir(data_dir)):
        fpath = os.path.join(data_dir, fname)
        if not os.path.isfile(fpath):
            continue

        # 匹配: {业务名}_{IP}_{端口}_{日期}_{时间}_sql_explain_schema.csv
        m = re.match(r"(.+?)_(\d+\.\d+\.\d+\.\d+)_(\d+)_(\d{4}-\d{2}-\d{2})_(\d{6})_(sql_explain_schema|sql_timecost_detail|sql_pattern_summary|report)\.(.+)$", fname)
        if not m:
            continue

        name, ip, port, date, exec_time, file_type, ext = m.groups()
        prefix = f"{name}_{ip}_{port}_{date}_{exec_time}"

        if prefix not in groups:
            groups[prefix] = {
                "name": name, "ip": ip, "port": port,
                "date": date, "exec_time": exec_time,
            }

        if file_type == "sql_explain_schema" and ext == "csv":
            groups[prefix]["explain"] = fpath
        elif file_type == "sql_timecost_detail" and ext == "csv":
            groups[prefix]["timecost"] = fpath

    # 只保留有 explain CSV 的组
    return {k: v for k, v in groups.items() if "explain" in v}


def parse_explain_csv(filepath):
    """解析 explain_schema.csv"""
    rows = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                count = int(row.get("执行次数", "0"))
            except ValueError:
                count = 0
            try:
                avg_tc = float(row.get("平均耗时(ms)", "0"))
            except ValueError:
                avg_tc = 0
            try:
                scan_rows = int(row.get("扫描行数", "0"))
            except ValueError:
                scan_rows = 0

            rows.append({
                "seq": row.get("序号", ""),
                "sql_type": row.get("SQL类型", ""),
                "db": row.get("库名", ""),
                "count": count,
                "avg_tc": avg_tc,
                "use_index": row.get("是否走索引", ""),
                "index_name": row.get("索引名称", ""),
                "scan_rows": scan_rows,
                "scan_rows_raw": row.get("扫描行数", "N/A"),
                "sql_pattern": row.get("归一化SQL", ""),
                "explain_detail": row.get("EXPLAIN执行计划", ""),
                "tables": row.get("涉及表", ""),
                "table_stats": row.get("表数据量", ""),
            })
    return rows


def parse_timecost_csv(filepath):
    """解析 sql_timecost_detail.csv"""
    rows = []
    col_types = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return [], []
        # 第一列是"耗时区间"，最后一列是"合计"，中间是 SQL 类型
        col_types = header[1:-1] if len(header) > 2 else header[1:]
        for row in reader:
            if not row or not row[0]:
                continue
            rows.append(row)
    return rows, col_types


# ============================================================
# 风险分析
# ============================================================

def analyze_risks(explain_rows):
    """分析 SQL 性能风险"""
    # 1. 未走索引（全表扫描）
    no_index = [r for r in explain_rows if r["use_index"] == "否"]
    no_index.sort(key=lambda r: r["count"], reverse=True)

    # 2. 高频 SQL Top 30
    high_freq = sorted(explain_rows, key=lambda r: r["count"], reverse=True)[:30]

    # 3. 走了索引但扫描行数过大（>1000行）
    bad_index = [r for r in explain_rows
                 if r["use_index"] == "是" and r["scan_rows"] > 1000]
    bad_index.sort(key=lambda r: r["scan_rows"], reverse=True)

    return {
        "no_index": no_index,
        "high_freq": high_freq,
        "bad_index": bad_index,
    }


# ============================================================
# HTML 生成
# ============================================================

HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
:root {{ --bg: #f8f9fa; --card: #fff; --border: #e0e0e0; --text: #212529;
  --primary: #0d6efd; --danger: #dc3545; --warning: #ffc107; --success: #198754;
  --muted: #6c757d; --nav-bg: #1a1a2e; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
       background: var(--bg); color: var(--text); line-height: 1.6; padding: 0; }}
/* 顶部导航栏 */
.top-nav {{ position: sticky; top: 0; z-index: 100; background: var(--nav-bg); color: #fff;
  padding: 12px 20px; display: flex; align-items: center; gap: 20px; flex-wrap: wrap;
  box-shadow: 0 2px 10px rgba(0,0,0,0.3); }}
.top-nav h1 {{ font-size: 1.1em; margin: 0; border: none; padding: 0; color: #fff; white-space: nowrap; }}
.nav-links {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.nav-links a {{ color: #ccc; text-decoration: none; padding: 4px 12px; border-radius: 4px;
  font-size: 0.85em; transition: all 0.2s; white-space: nowrap; }}
.nav-links a:hover, .nav-links a.active {{ background: var(--primary); color: #fff; }}
.nav-badge {{ display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 0.75em;
  margin-left: 4px; font-weight: 700; }}
.nb-danger {{ background: var(--danger); color: #fff; }}
.nb-warning {{ background: var(--warning); color: #333; }}
.nb-info {{ background: rgba(255,255,255,0.2); color: #fff; }}
/* 内容区 */
.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
h2 {{ color: #333; margin: 28px 0 12px; border-left: 4px solid var(--primary); padding-left: 12px;
  scroll-margin-top: 70px; }}
h3 {{ color: #444; margin: 20px 0 10px; scroll-margin-top: 70px; }}
p.meta {{ color: var(--muted); font-size: 0.9em; margin-bottom: 16px; }}
/* 实例卡片 */
.instance-block {{ background: var(--card); border-radius: 8px; padding: 20px; margin: 20px 0;
  box-shadow: 0 2px 6px rgba(0,0,0,0.08); scroll-margin-top: 70px; }}
.instance-title {{ color: var(--primary); font-size: 1.3em; margin-bottom: 10px;
  padding-bottom: 8px; border-bottom: 2px solid var(--primary); }}
/* 实例内导航 */
.section-nav {{ display: flex; gap: 10px; margin: 10px 0 15px; flex-wrap: wrap; }}
.section-nav a {{ color: var(--primary); text-decoration: none; padding: 4px 10px;
  border: 1px solid var(--primary); border-radius: 4px; font-size: 0.82em; }}
.section-nav a:hover {{ background: var(--primary); color: #fff; }}
/* 表格 */
table {{ border-collapse: collapse; width: 100%; margin: 10px 0 20px; background: var(--card);
  box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-radius: 6px; overflow: hidden; }}
th {{ background: #f1f3f5; color: #333; font-weight: 600; text-align: center; padding: 10px 12px;
  border-bottom: 2px solid var(--border); font-size: 0.88em; white-space: nowrap;
  cursor: pointer; user-select: none; position: relative; }}
th:hover {{ background: #e2e6ea; }}
th::after {{ content: '⇅'; font-size: 0.7em; margin-left: 4px; opacity: 0.3; }}
th.sort-asc::after {{ content: '▲'; opacity: 1; }}
th.sort-desc::after {{ content: '▼'; opacity: 1; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #f0f0f0; font-size: 0.85em; vertical-align: top; }}
td:first-child {{ text-align: center; }}
tr:hover td {{ background: #f8f9ff; }}
.num {{ text-align: right; }}
/* 标签 */
.tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; font-weight: 600; }}
.tag-danger {{ background: #fce4e4; color: var(--danger); }}
.tag-warning {{ background: #fff3cd; color: #856404; }}
.tag-success {{ background: #d4edda; color: var(--success); }}
.tag-info {{ background: #d1ecf1; color: #0c5460; }}
code {{ background: #f1f3f5; padding: 2px 6px; border-radius: 3px; font-size: 0.82em; word-break: break-all; }}
.sql-cell {{ max-width: 500px; word-break: break-all; }}
/* 风险卡片 */
.risk-summary {{ display: flex; gap: 20px; margin: 15px 0; flex-wrap: wrap; }}
.risk-card {{ flex: 1; min-width: 180px; padding: 15px; border-radius: 8px; text-align: center;
  transition: transform 0.2s; cursor: default; }}
.risk-card:hover {{ transform: translateY(-2px); }}
.risk-card h4 {{ font-size: 2em; margin-bottom: 5px; }}
.risk-card p {{ font-size: 0.9em; }}
.rc-danger {{ background: #fce4e4; color: var(--danger); }}
.rc-warning {{ background: #fff3cd; color: #856404; }}
.rc-info {{ background: #d1ecf1; color: #0c5460; }}
.rc-success {{ background: #d4edda; color: var(--success); }}
/* 柱状条 */
.bar-cell {{ position: relative; }}
.bar {{ position: absolute; left: 0; top: 0; bottom: 0; background: rgba(13,110,253,0.08); z-index: 0; }}
.bar-val {{ position: relative; z-index: 1; }}
.total {{ font-weight: 700; background: #e9ecef !important; }}
.zero {{ color: #ccc; }}
/* 返回顶部 */
.back-top {{ position: fixed; bottom: 30px; right: 30px; width: 44px; height: 44px;
  background: var(--primary); color: #fff; border: none; border-radius: 50%; cursor: pointer;
  font-size: 1.2em; display: none; align-items: center; justify-content: center; z-index: 99;
  box-shadow: 0 2px 8px rgba(0,0,0,0.2); transition: opacity 0.3s; }}
.back-top:hover {{ opacity: 0.8; }}
/* 折叠 */
.collapse-btn {{ background: none; border: 1px solid var(--border); padding: 2px 10px;
  border-radius: 4px; cursor: pointer; font-size: 0.82em; color: var(--muted); margin-left: 10px; }}
.collapse-btn:hover {{ background: #f1f3f5; }}
.collapsible {{ transition: max-height 0.3s ease; overflow: hidden; }}
footer {{ margin-top: 30px; padding: 15px 0; border-top: 1px solid var(--border);
  color: var(--muted); font-size: 0.85em; text-align: center; }}
</style>
</head>
<body>
"""

HTML_FOOT = """
<footer>Generated by interf_report_generator.py v{version} | {time}</footer>
</div>
<button class="back-top" id="backTop" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑</button>
<script>
// 返回顶部按钮
window.addEventListener('scroll', function(){{
  document.getElementById('backTop').style.display = window.scrollY > 300 ? 'flex' : 'none';
}});
// 表格排序
document.querySelectorAll('th').forEach(function(th){{
  th.addEventListener('click', function(){{
    var table = th.closest('table');
    var idx = Array.from(th.parentNode.children).indexOf(th);
    var rows = Array.from(table.querySelectorAll('tr')).slice(1);
    var asc = !th.classList.contains('sort-asc');
    th.parentNode.querySelectorAll('th').forEach(function(h){{ h.classList.remove('sort-asc','sort-desc'); }});
    th.classList.add(asc ? 'sort-asc' : 'sort-desc');
    rows.sort(function(a, b){{
      var av = a.children[idx] ? a.children[idx].textContent.replace(/[,%]/g,'').trim() : '';
      var bv = b.children[idx] ? b.children[idx].textContent.replace(/[,%]/g,'').trim() : '';
      var an = parseFloat(av), bn = parseFloat(bv);
      if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
      return asc ? av.localeCompare(bv) : bv.localeCompare(av);
    }});
    var tbody = table.querySelector('tbody') || table;
    rows.forEach(function(r){{ tbody.appendChild(r); }});
  }});
}});
// 导航高亮
var navLinks = document.querySelectorAll('.nav-links a');
if (navLinks.length > 0) {{
  var sections = [];
  navLinks.forEach(function(a) {{
    var t = document.querySelector(a.getAttribute('href'));
    if (t) sections.push({{el: t, link: a}});
  }});
  window.addEventListener('scroll', function() {{
    var scrollY = window.scrollY + 80;
    var current = null;
    sections.forEach(function(s) {{
      if (s.el.offsetTop <= scrollY) current = s;
    }});
    navLinks.forEach(function(a) {{ a.classList.remove('active'); }});
    if (current) current.link.classList.add('active');
  }});
}}
</script>
</body>
</html>
"""


def render_timecost_table(timecost_rows, col_types):
    """渲染耗时区间 × SQL 类型统计表"""
    if not timecost_rows:
        return "<p>无耗时统计数据</p>"

    buf = ['<table>\n<tr><th>耗时区间</th>']
    for t in col_types:
        buf.append(f'<th>{_h(t)}</th>')
    buf.append('<th>合计</th></tr>\n')

    # 计算最大行合计（用于柱状条）
    row_totals = []
    for row in timecost_rows:
        try:
            total = int(row[-1]) if len(row) > 1 else 0
        except ValueError:
            total = 0
        row_totals.append(total)
    max_total = max(row_totals) if row_totals else 1

    for i, row in enumerate(timecost_rows):
        total = row_totals[i]
        bar_pct = (total / max_total * 100) if max_total > 0 else 0
        buf.append(f'<tr><td><b>{_h(row[0])}</b></td>')
        for j in range(1, len(row) - 1):
            v = row[j] if j < len(row) else "0"
            cls = ' class="zero"' if v == "0" else ' class="num"'
            buf.append(f'<td{cls}>{_h(v)}</td>')
        buf.append(f'<td class="bar-cell"><span class="bar" style="width:{bar_pct:.1f}%"></span>'
                   f'<span class="bar-val"><b>{_h(row[-1])}</b></span></td></tr>\n')

    buf.append('</table>\n')
    return "".join(buf)


def render_risk_table(rows, title, risk_type="danger"):
    """渲染风险 SQL 表格"""
    if not rows:
        return f"<p>无{title}</p>\n"

    tag_cls = {"danger": "tag-danger", "warning": "tag-warning", "info": "tag-info"}.get(risk_type, "tag-info")

    buf = [f'<table>\n<tr><th>#</th><th>SQL类型</th><th>库名</th><th>执行次数</th>'
           f'<th>平均耗时(ms)</th><th>是否走索引</th><th>索引名</th><th>扫描行数</th>'
           f'<th>涉及表</th><th>SQL模式</th></tr>\n']

    for i, r in enumerate(rows, 1):
        # 索引标签
        if r["use_index"] == "否":
            idx_tag = f'<span class="tag tag-danger">否</span>'
        elif r["use_index"] == "是":
            idx_tag = f'<span class="tag tag-success">是</span>'
        else:
            idx_tag = f'<span class="tag tag-info">{_h(r["use_index"])}</span>'

        # 扫描行数标签
        if r["scan_rows"] > 10000:
            rows_tag = f'<span class="tag tag-danger">{r["scan_rows"]:,}</span>'
        elif r["scan_rows"] > 1000:
            rows_tag = f'<span class="tag tag-warning">{r["scan_rows"]:,}</span>'
        else:
            rows_tag = f'{_h(r["scan_rows_raw"])}'

        buf.append(f'<tr><td>{i}</td>'
                   f'<td>{_h(r["sql_type"])}</td>'
                   f'<td>{_h(r["db"])}</td>'
                   f'<td class="num"><b>{r["count"]:,}</b></td>'
                   f'<td class="num">{r["avg_tc"]:.2f}</td>'
                   f'<td>{idx_tag}</td>'
                   f'<td>{_h(r["index_name"])}</td>'
                   f'<td class="num">{rows_tag}</td>'
                   f'<td>{_h(r["tables"])}</td>'
                   f'<td class="sql-cell"><code>{_h(r["sql_pattern"][:200])}</code></td>'
                   f'</tr>\n')

    buf.append('</table>\n')
    return "".join(buf)


def render_instance_report(group, explain_rows, timecost_rows, col_types, risks, instance_id):
    """渲染单个实例的报告内容"""
    buf = []
    name = group["name"]
    ip = group["ip"]
    port = group["port"]
    iid = instance_id

    buf.append(f'<div class="instance-block" id="{iid}">\n')
    buf.append(f'<div class="instance-title">{_h(name)} ({_h(ip)}:{_h(port)})</div>\n')

    # 实例内导航
    buf.append('<div class="section-nav">\n')
    buf.append(f'  <a href="#{iid}-timecost">耗时统计</a>\n')
    buf.append(f'  <a href="#{iid}-noscan">全表扫描({len(risks["no_index"])})</a>\n')
    buf.append(f'  <a href="#{iid}-highfreq">高频SQL(Top30)</a>\n')
    buf.append(f'  <a href="#{iid}-badidx">低效索引({len(risks["bad_index"])})</a>\n')
    buf.append('</div>\n')

    # 风险概览卡片
    buf.append('<div class="risk-summary">\n')
    buf.append(f'<div class="risk-card rc-danger"><h4>{len(risks["no_index"])}</h4>'
               f'<p>全表扫描 SQL</p></div>\n')
    buf.append(f'<div class="risk-card rc-warning"><h4>{len(risks["bad_index"])}</h4>'
               f'<p>低效索引 SQL<br>(扫描行数&gt;1000)</p></div>\n')
    buf.append(f'<div class="risk-card rc-info"><h4>{len(explain_rows)}</h4>'
               f'<p>业务 SQL 模式总数</p></div>\n')
    buf.append('</div>\n')

    # 1. 耗时区间统计
    buf.append(f'<h3 id="{iid}-timecost">SQL 耗时区间 × 类型统计</h3>\n')
    buf.append(render_timecost_table(timecost_rows, col_types))

    # 2. 全表扫描 SQL
    buf.append(f'<h3 id="{iid}-noscan"><span class="tag tag-danger">风险</span> 未走索引的 SQL（全表扫描）</h3>\n')
    buf.append(render_risk_table(risks["no_index"], "全表扫描 SQL", "danger"))

    # 3. 高频 SQL Top 30
    buf.append(f'<h3 id="{iid}-highfreq"><span class="tag tag-info">关注</span> 高频 SQL Top 30</h3>\n')
    buf.append(render_risk_table(risks["high_freq"], "高频 SQL", "info"))

    # 4. 低效索引
    buf.append(f'<h3 id="{iid}-badidx"><span class="tag tag-warning">优化</span> 走了索引但扫描行数过大（&gt;1000行）</h3>\n')
    buf.append(render_risk_table(risks["bad_index"], "低效索引 SQL", "warning"))

    buf.append('</div>\n')
    return "".join(buf)


def generate_html_report(groups_data, title, output_path):
    """生成 HTML 报告"""
    buf = [HTML_HEAD.format(title=_h(title))]

    # 顶部导航栏
    buf.append('<nav class="top-nav">\n')
    buf.append(f'<h1>{_h(title)}</h1>\n')
    buf.append('<div class="nav-links">\n')
    for i, (prefix, data) in enumerate(groups_data.items()):
        g = data["group"]
        risks = data["risks"]
        iid = f"inst-{i}"
        danger_cnt = len(risks["no_index"])
        warn_cnt = len(risks["bad_index"])
        badge = ""
        if danger_cnt > 0:
            badge += f'<span class="nav-badge nb-danger">{danger_cnt}</span>'
        if warn_cnt > 0:
            badge += f'<span class="nav-badge nb-warning">{warn_cnt}</span>'
        buf.append(f'  <a href="#{iid}">{_h(g["name"])} ({_h(g["port"])}){badge}</a>\n')
    buf.append('</div>\n</nav>\n')

    buf.append('<div class="container">\n')
    buf.append(f'<p class="meta">生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | '
               f'实例数: {len(groups_data)}</p>\n')

    for i, (prefix, data) in enumerate(groups_data.items()):
        iid = f"inst-{i}"
        buf.append(render_instance_report(
            data["group"], data["explain_rows"],
            data["timecost_rows"], data["col_types"],
            data["risks"], iid,
        ))

    buf.append(HTML_FOOT.format(version=VERSION, time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("".join(buf))
    print(f"  [输出] {output_path}", file=sys.stderr)


# ============================================================
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="interf 深度分析报告生成器 — 读取 CSV 文件生成 HTML 性能风险报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-d", "--data-dir", required=True,
                        help="CSV 文件所在目录（如 output/2026-04-01）")
    parser.add_argument("--mode", choices=["merge", "split"], default="merge",
                        help="merge=所有实例汇总到一个 HTML（默认），split=每个实例单独一个 HTML")
    parser.add_argument("-o", "--output-dir", default=None,
                        help="输出目录（默认: 与 data-dir 相同）")
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args()

    data_dir = os.path.abspath(args.data_dir)
    if not os.path.isdir(data_dir):
        print(f"错误: 目录不存在: {data_dir}", file=sys.stderr)
        sys.exit(1)

    output_dir = os.path.abspath(args.output_dir) if args.output_dir else data_dir
    os.makedirs(output_dir, exist_ok=True)

    # 扫描 CSV 文件
    groups = find_csv_groups(data_dir)
    if not groups:
        print(f"错误: {data_dir} 中未找到 *_sql_explain_schema.csv 文件", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  interf 报告生成器 v{VERSION}", file=sys.stderr)
    print(f"  数据目录: {data_dir}", file=sys.stderr)
    print(f"  模式: {args.mode}", file=sys.stderr)
    print(f"  发现 {len(groups)} 个实例:", file=sys.stderr)
    for prefix, g in groups.items():
        has_tc = "timecost" in g
        print(f"    {g['name']} ({g['ip']}:{g['port']}) "
              f"[explain: ✓, timecost: {'✓' if has_tc else '✗'}]", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    # 解析所有 CSV
    groups_data = {}
    for prefix, group in groups.items():
        print(f"  [解析] {group['name']} ({group['ip']}:{group['port']}) ...", file=sys.stderr)

        explain_rows = parse_explain_csv(group["explain"])
        timecost_rows, col_types = [], []
        if "timecost" in group:
            timecost_rows, col_types = parse_timecost_csv(group["timecost"])

        risks = analyze_risks(explain_rows)

        print(f"    SQL 模式: {len(explain_rows)}, "
              f"全表扫描: {len(risks['no_index'])}, "
              f"低效索引: {len(risks['bad_index'])}", file=sys.stderr)

        groups_data[prefix] = {
            "group": group,
            "explain_rows": explain_rows,
            "timecost_rows": timecost_rows,
            "col_types": col_types,
            "risks": risks,
        }

    # 生成报告
    if args.mode == "merge":
        # 获取日期
        first_group = next(iter(groups_data.values()))["group"]
        date_str = first_group["date"]
        title = f"interf SQL 性能风险分析报告 — {date_str}"
        output_path = os.path.join(output_dir, f"interf_risk_report_{date_str}.html")
        generate_html_report(groups_data, title, output_path)
    else:
        for prefix, data in groups_data.items():
            g = data["group"]
            title = f"interf SQL 性能风险分析 — {g['name']} ({g['ip']}:{g['port']})"
            output_path = os.path.join(output_dir,
                f"{g['name']}_{g['ip']}_{g['port']}_{g['date']}_risk_report.html")
            generate_html_report({prefix: data}, title, output_path)

    print(f"\n  [完成] 报告已生成到: {output_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
