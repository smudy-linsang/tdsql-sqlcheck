#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TDSQL Gateway 日志综合分析脚本 v3.2

作者: lynx,boogqwang

功能:
  解析 TDSQL Gateway 产生的多种日志文件，自动生成全面的分析报告，包括:
  1. 日志概览 (文件类型/数量/日期跨度/总量)
  2. 每日请求量趋势
  3. 每小时请求量分布
  4. SQL 耗时分布分析
  5. 每日平均耗时趋势
  6. 高耗时 SQL Top N
  7. 高频 SQL 模式 Top N
  8. SQL 类型分布
  9. 用户 & 数据库分布
  10. 错误码分析
  11. 慢 SQL 日志分析 (slow_sql_instance)
  12. 系统日志异常检测 (sys_instance)
  13. 连接模式分析
  14. SQL 执行耗时火焰图（散点图，横轴时间，纵轴耗时）
  15. 核心结论与建议

数据导出:
  支持将分析数据导出为 JSON 文件（紧凑格式 + 短 key），便于后续多节点/多时段
  数据整合分析或横向对比:
  python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log -o data.json
  输出 .json.gz 可自动 gzip 压缩，体积缩小到原始的 10~20%:
  python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log -o data.json.gz

日期过滤:
  支持天级 (YYYY-MM-DD) 和小时级 (YYYY-MM-DDTHH) 两种精度:
  python3 analyze_gateway_log.py -d ... --dates 2026-02-20
  python3 analyze_gateway_log.py -d ... --dates 2026-02-20T14 2026-02-20T15

输出文件名:
  -o 支持 {date} 占位符，自动替换为 --dates 日期或当前日期时间:
  python3 analyze_gateway_log.py -d ... --dates 2026-02-20 -o report_{date}.html

支持的日志类型:
  - interf_instance  : SQL 接口层日志 (主要分析对象)
  - sql_instance     : SQL 执行层日志
  - slow_sql_instance: 慢 SQL 日志
  - sys_instance     : 系统/错误日志
  - route_instance   : 路由日志
  - dbfw_instance    : 数据库防火墙日志

依赖:
  仅使用 Python 标准库 (Python >= 3.6)

用法:
  python3 analyze_gateway_log.py -d <日志目录> [选项]
  python3 analyze_gateway_log.py -p <端口号表达式> [选项]
  python3 analyze_gateway_log.py -p 15001:15020
  python3 analyze_gateway_log.py -p 15001:15010,15012,15015~15020
  python3 analyze_gateway_log.py -p 15001:15020 --base-path /data1/tdengine/data/{port}/gateway/log
  python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log
  python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log --dates 2026-02-20 2026-02-21
  python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log --dates 2026-02-20T14
  python3 analyze_gateway_log.py -d /data/tdsql_run/15001/gateway/log -o report_{date}.html
  python3 analyze_gateway_log.py -h
"""

import argparse
import os
import sys
import re
import html as html_mod
try:
    import resource
except ImportError:
    class MockResource:
        RUSAGE_SELF = 0
        RLIMIT_AS = 0
        error = Exception
        def getrlimit(self, limit):
            return (0, 0)
        def setrlimit(self, limit, val):
            pass
        def getrusage(self, self_val):
            class MockUsage:
                ru_maxrss = 0
            return MockUsage()
    resource = MockResource()
import time
from collections import defaultdict, Counter, OrderedDict
from datetime import datetime, timedelta
import json
import io
import heapq
import atexit
import signal

# ============================================================
# 常量
# ============================================================
VERSION = "3.4"

# ── 默认路径模板 ─────────────────────────────────────────
DEFAULT_LOG_PATH_TEMPLATE = "/data/tdsql_run/{port}/gateway/log"

# ── 资源限制 ────────────────────────────────────────────
MAX_MEMORY_MB = 1024  # 最大内存限制 1GB
IO_BATCH_SIZE = 8192  # 文件读取缓冲行数
CPU_YIELD_INTERVAL = 50000  # 每处理 N 行让出 CPU

LOCK_FILE = "/tmp/tdsql_gateway_analyzer.lock"

def _acquire_lock():
    """获取进程锁，确保同一台服务器上只有一个实例运行。
    使用 PID 锁文件，自动检测持锁进程是否存活（防止残留锁文件）。
    """
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                old_pid = int(f.read().strip())
            # 检查进程是否存活
            os.kill(old_pid, 0)
            # 进程存在，拒绝启动
            print(f"\n  {c(RED, '[错误]')} 另一个分析进程正在运行 (PID: {old_pid})", file=sys.stderr)
            print(f"  {c(DIM, '如果确认没有其他进程在运行，请手动删除锁文件:')} {LOCK_FILE}", file=sys.stderr)
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            # 进程不存在或 PID 无效 → 残留锁文件，清理后继续
            pass
        except PermissionError:
            # 进程存在但无权限发信号（属于其他用户），视为仍在运行
            print(f"\n  {c(RED, '[错误]')} 另一个分析进程正在运行 (PID: {old_pid})", file=sys.stderr)
            sys.exit(1)
    # 写入当前 PID
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

def _release_lock():
    """释放进程锁"""
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE, "r") as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(LOCK_FILE)
    except (ValueError, OSError):
        pass

def _apply_resource_limits():
    """设置进程资源限制，防止独占服务器资源"""
    try:
        # 限制最大内存使用
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        limit = MAX_MEMORY_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    except (ValueError, resource.error):
        pass
    try:
        # 设置 nice 值，降低 CPU 优先级
        os.nice(10)
    except (OSError, AttributeError):
        pass

def _get_mem_mb():
    """获取当前进程内存占用(MB)"""
    try:
        ru = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == "darwin":
            return ru.ru_maxrss / 1024 / 1024
        return ru.ru_maxrss / 1024
    except Exception:
        return 0

# ── 颜色定义 ────────────────────────────────────────────
CYAN    = "\033[36m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RESET   = "\033[0m"

NO_COLOR = not sys.stderr.isatty() or os.environ.get("NO_COLOR")

def c(code, text):
    """带颜色包装，NO_COLOR 时返回纯文本"""
    if NO_COLOR:
        return str(text)
    return f"{code}{text}{RESET}"

def print_banner():
    """打印启动 Banner"""
    out = sys.stderr
    if NO_COLOR:
        out.write(f"# TDSQL Gateway Log Analyzer v{VERSION}\n\n")
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
        out.write(f"║        Gateway Log Analyzer v{VERSION}                          ║\n")
        out.write("╚═══════════════════════════════════════════════════════════╝\n")
        out.write(f"{RESET}\n")

LOG_TYPES = {
    "interf": "SQL接口层日志",
    "sql": "SQL执行层日志",
    "slow_sql": "慢SQL日志",
    "sys": "系统/错误日志",
    "route": "路由日志",
    "dbfw": "数据库防火墙日志",
}

SQL_TYPE_MAP = {
    "0": "未知",
    "1": "QUIT/断开",
    "2": "USE/初始化",
    "3": "查询/DML",
    "4": "CREATE",
    "5": "INSERT",
    "6": "UPDATE",
    "7": "DELETE",
    "8": "SELECT",
    "9": "SHOW",
    "10": "SET",
    "11": "新建连接",
    "14": "PREPARE",
    "22": "事务(BEGIN)",
    "23": "事务(COMMIT)",
    "25": "事务(SET)",
}

TIMECOST_BINS = [
    (0, 1, "<1ms"),
    (1, 5, "1-5ms"),
    (5, 10, "5-10ms"),
    (10, 50, "10-50ms"),
    (50, 100, "50-100ms"),
    (100, 500, "100-500ms"),
    (500, 1000, "500ms-1s"),
    (1000, 3000, "1-3s"),
    (3000, 10000, "3-10s"),
    (10000, float("inf"), ">10s"),
]

RESULTCODE_MAP = {
    "0": "成功",
    "1046": "未选择数据库",
    "1062": "唯一键冲突",
    "1064": "SQL语法错误",
    "1317": "查询被中断",
    "650": "Proxy不支持的操作",
    "4039": "TDSQL Proxy限制",
}

# ============================================================
# HTML 模板
# ============================================================

def _h(text):
    """HTML 转义"""
    return html_mod.escape(str(text))

HTML_TEMPLATE_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TDSQL Gateway 日志分析报告</title>
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
/* TOC 目录 */
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
/* 章节折叠 */
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

HTML_TEMPLATE_TAIL = """</div>
<script>
(function(){
  // 构建 TOC（多目录时按目录分组）
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

  // 折叠/展开 TOC
  window.toggleToc = function(){
    document.getElementById('toc').classList.toggle('collapsed');
  };

  // 点击章节标题折叠/展开内容
  sections.forEach(function(sec){
    var header = sec.querySelector('.section-header');
    if(header){
      header.addEventListener('click', function(e){
        if(e.target.tagName === 'A') return;
        sec.classList.toggle('collapsed');
      });
    }
  });

  // TOC 高亮当前章节
  var tocLinks = document.querySelectorAll('.toc-body a');
  var sectionEls = Array.from(sections);
  function updateActive(){
    var scrollY = window.scrollY || window.pageYOffset;
    var current = '';
    sectionEls.forEach(function(sec){
      if(sec.offsetTop - 80 <= scrollY) current = sec.id;
    });
    tocLinks.forEach(function(a){
      a.classList.toggle('active', a.getAttribute('data-target') === current);
    });
  }
  window.addEventListener('scroll', updateActive);
  updateActive();

  // 平滑滚动
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
# 解析函数
# ============================================================


def _unique_dir_names(abs_dirs):
    """为一组绝对路径生成唯一的显示名称。

    优先使用 basename；若有重复，则向上扩展路径层级直到唯一。
    同时尝试从日志文件名中提取端口号作为后缀（如 log:15001）。
    """
    basenames = [os.path.basename(d) for d in abs_dirs]
    # 如果 basename 都不重复，直接返回
    if len(set(basenames)) == len(basenames):
        return dict(zip(abs_dirs, basenames))

    # 尝试从路径中向上取更多层级来区分
    # 例如 /data/tdsql_run/15001/gateway/log → 15001/gateway/log
    parts_list = [d.rstrip("/").split("/") for d in abs_dirs]
    for depth in range(2, max(len(p) for p in parts_list)):
        names = ["/".join(p[-depth:]) if len(p) >= depth else "/".join(p)
                 for p in parts_list]
        if len(set(names)) == len(names):
            return dict(zip(abs_dirs, names))

    # 最后兜底：使用完整路径
    return dict(zip(abs_dirs, abs_dirs))


def _date_filter_days(date_filter):
    """从 date_filter 集合中提取天维度的日期集合（去掉小时部分）。

    date_filter 中的元素可能是 'YYYY-MM-DD' 或 'YYYY-MM-DDTHH'，
    本函数统一提取前 10 字符作为天维度日期。
    """
    if not date_filter:
        return None
    return {d[:10] for d in date_filter}


def discover_log_files(dirs, date_filter=None):
    """扫描目录，发现并分类所有日志文件

    Args:
        dirs: 日志目录列表
        date_filter: 日期过滤集合，支持天级 'YYYY-MM-DD' 或小时级 'YYYY-MM-DDTHH'，
            None 表示全部。
            注意：由于日志文件未写满时会跨日（如 01-01 的文件中含 01-02 的日志），
            扫描时会自动扩展包含每个目标日期的前一天文件。
    """
    result = {}  # {dir_name: {log_type: [(filepath, date, port), ...]}}
    abs_dirs = [os.path.abspath(d) for d in dirs]
    # 提取天维度日期用于文件名过滤（文件名只有天精度）
    day_filter = _date_filter_days(date_filter)
    # 扩展日期过滤：自动包含每个目标日期的前一天
    expanded_filter = None
    if day_filter:
        expanded_filter = set(day_filter)
        for d in day_filter:
            try:
                prev_day = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
                expanded_filter.add(prev_day)
            except ValueError:
                pass
    name_map = _unique_dir_names(abs_dirs)
    for d in abs_dirs:
        dir_name = name_map[d]
        if not os.path.isdir(d):
            print(f"  {c(YELLOW, '[警告]')} 目录不存在: {d}", file=sys.stderr)
            continue
        files_by_type = defaultdict(list)
        for fname in sorted(os.listdir(d)):
            fpath = os.path.join(d, fname)
            if not os.path.isfile(fpath):
                continue
            # 匹配: <type>_instance_<port>.<date>.0 或 <type>_instance_<port>
            m = re.match(
                r"(interf|sql|slow_sql|sys|route|dbfw|retry|update)_instance_(\d+)"
                r"(?:\.(\d{4}-\d{2}-\d{2})\.(\d+))?$",
                fname,
            )
            if m:
                log_type = m.group(1)
                port = m.group(2)
                date = m.group(3) or "unknown"
                # 日期过滤（使用扩展后的日期集合，包含前一天）
                if expanded_filter and date != "unknown" and date not in expanded_filter:
                    continue
                files_by_type[log_type].append((fpath, date, port))
        if not files_by_type:
            print(f"  {c(YELLOW, '[警告]')} 目录 {dir_name} 中无匹配日志"
                  + (f"（日期过滤: {', '.join(sorted(date_filter))}）" if date_filter else ""),
                  file=sys.stderr)
        result[dir_name] = dict(files_by_type)
    return result


def parse_interf_kv(line):
    """解析 interf_instance 的 key=value 对"""
    # 去掉时间戳前缀: [2026-02-26 00:00:00 002408] INFO topic=...
    m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \d+\]\s+\w+\s+(.*)", line)
    if not m:
        return None
    timestamp_str = m.group(1)
    body = m.group(2)
    fields = {}
    fields["_timestamp"] = timestamp_str
    fields["_hour"] = timestamp_str[:13]  # "2026-02-26 00"
    fields["_minute"] = timestamp_str[:16]  # "2026-02-26 00:00"
    for part in body.split("&"):
        if "=" in part:
            k, _, v = part.partition("=")
            fields[k] = v
    return fields


def parse_sql_instance_line(line):
    """解析 sql_instance 行"""
    m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \d+\]\s+\w+\s+(.*)", line)
    if not m:
        return None
    timestamp_str = m.group(1)
    body = m.group(2)
    fields = {"_timestamp": timestamp_str}
    # 提取 timecost
    tc_m = re.search(r"timecost:([\d.]+)\(ms\)", body)
    if tc_m:
        fields["timecost"] = tc_m.group(1)
    # 提取 sql
    sql_m = re.search(r'sql:\d+,\d+\s+"(.+)"$', body)
    if sql_m:
        fields["sql"] = sql_m.group(1)
    # 提取 user
    user_m = re.search(r"user:(\S+)", body)
    if user_m:
        fields["user"] = user_m.group(1)
    return fields


def parse_slow_sql_blocks(filepath):
    """解析慢SQL日志文件，返回慢SQL条目列表"""
    blocks = []
    try:
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            return blocks
    except (FileNotFoundError, OSError):
        return blocks
    current = {}
    sql_lines = []
    in_sql = False
    try:
        f = open(filepath, "r", errors="replace")
    except (FileNotFoundError, OSError):
        return blocks
    with f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("# Time:"):
                if current and (sql_lines or current.get("sql")):
                    if sql_lines:
                        current["sql"] = " ".join(sql_lines).strip()
                    blocks.append(current)
                current = {"time": line}
                sql_lines = []
                in_sql = False
            elif line.startswith("# User@Host:"):
                m = re.search(r"(\S+)\[", line)
                if m:
                    current["user"] = m.group(1)
                ip_m = re.search(r"\[([\d.:]+)\]", line)
                if ip_m:
                    current["client_ip"] = ip_m.group(1)
            elif line.startswith("# Backend_host:"):
                current["backend"] = line.split(":", 1)[1].strip()
            elif line.startswith("# Thread_id:"):
                schema_m = re.search(r"Schema:\s*(\S+)", line)
                if schema_m:
                    current["schema"] = schema_m.group(1)
            elif line.startswith("# Query_time:"):
                qt_m = re.search(r"Query_time:\s*([\d.]+)", line)
                lt_m = re.search(r"Lock_time:\s*([\d.]+)", line)
                rs_m = re.search(r"Rows_sent:\s*(\d+)", line)
                re_m = re.search(r"Rows_examined:\s*(\d+)", line)
                if qt_m:
                    current["query_time"] = float(qt_m.group(1))
                if lt_m:
                    current["lock_time"] = float(lt_m.group(1))
                if rs_m:
                    current["rows_sent"] = int(rs_m.group(1))
                if re_m:
                    current["rows_examined"] = int(re_m.group(1))
                in_sql = True
            elif in_sql and not line.startswith("#"):
                sql_lines.append(line)
    # 最后一条
    if current and (sql_lines or current.get("sql")):
        if sql_lines:
            current["sql"] = " ".join(sql_lines).strip()
        blocks.append(current)
    return blocks


def parse_sys_instance(filepath, max_lines=10000):
    """解析系统日志，统计错误类型"""
    errors = []
    try:
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            return errors
    except (FileNotFoundError, OSError):
        return errors
    try:
        f = open(filepath, "r", errors="replace")
    except (FileNotFoundError, OSError):
        return errors
    count = 0
    with f:
        for line in f:
            count += 1
            if count > max_lines:
                break
            line = line.rstrip("\n")
            errors.append(line)
    return errors


def normalize_sql(sql_text):
    """将SQL中的具体值替换为占位符，提取SQL模式"""
    if not sql_text:
        return ""
    s = sql_text[:200]
    # URL 解码
    s = s.replace("%3D", "=").replace("%0A", " ").replace("%20", " ")
    # 替换数字
    s = re.sub(r"(?<![a-zA-Z_])\d+(?:\.\d+)?", "?", s)
    # 替换字符串
    s = re.sub(r"'[^']*'", "'?'", s)
    # 压缩空白
    s = re.sub(r"\s+", " ", s).strip()
    return s[:120]


# ============================================================
# 分析类
# ============================================================


class GatewayLogAnalyzer:
    def __init__(self, log_dirs, top_n=20, sample_limit=0, date_filter=None, log_types=None, specific_files=None):
        self.log_dirs = log_dirs
        self.top_n = top_n
        self.sample_limit = sample_limit  # 0=全量
        self.date_filter = date_filter
        self.log_types = log_types  # 指定的日志类型
        self.specific_files = specific_files  # 指定的具体文件
        # 天维度日期集合，用于文件级判断和行级日期比较
        self.date_filter_days = _date_filter_days(date_filter)
        # 是否包含小时级过滤条件
        self._has_hour_filter = bool(
            date_filter and any(len(d) > 10 for d in date_filter)
        )
        
        if specific_files:
            # 如果指定了具体文件，直接使用这些文件
            self.all_files = self._organize_specific_files(specific_files)
        else:
            # 否则按目录扫描
            self.all_files = discover_log_files(log_dirs, date_filter=date_filter)
        self.results = {}  # 每个目录的分析结果
        self._line_counter = 0  # 全局行计数器，用于资源控制
    
    def _organize_specific_files(self, specific_files):
        """组织指定的具体文件"""
        result = {}  # {dir_name: {log_type: [(filepath, date, port), ...]}}
        
        for filepath in specific_files:
            filepath = os.path.abspath(filepath)
            if not os.path.exists(filepath):
                print(f"    {c(YELLOW, '[警告]')} 文件不存在，跳过: {filepath}", file=sys.stderr)
                continue
                
            fname = os.path.basename(filepath)
            # 匹配: <type>_instance_<port>.<date>.0 或 <type>_instance_<port>
            m = re.match(
                r"(interf|sql|slow_sql|sys|route|dbfw|retry|update)_instance_(\d+)"
                r"(?:\.(\d{4}-\d{2}-\d{2})\.(\d+))?$",
                fname,
            )
            if not m:
                print(f"    {c(YELLOW, '[警告]')} 文件名不符合日志命名规则，跳过: {fname}", file=sys.stderr)
                continue
                
            log_type, port, date_str, seq = m.groups()
            
            # 如果指定了日志类型过滤，检查是否匹配
            if self.log_types and log_type not in self.log_types:
                continue
                
            # 提取日期
            if date_str:
                file_date = date_str
            else:
                # 无日期后缀的文件（当前正在写入的日志），从 mtime 推断
                try:
                    mtime = os.path.getmtime(filepath)
                    file_date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
                except Exception:
                    file_date = "unknown"
            
            # 组织到结果中，使用目录路径作为 key
            dir_name = os.path.dirname(filepath)
            if not dir_name:
                dir_name = os.getcwd()
            if dir_name not in result:
                result[dir_name] = {}
            if log_type not in result[dir_name]:
                result[dir_name][log_type] = []
                
            result[dir_name][log_type].append((filepath, file_date, port))
            
        return result

    def _is_extra_file(self, file_date):
        """判断文件是否是因跨日扩展而额外纳入的（文件名日期不在用户指定日期中）"""
        return (self.date_filter_days and file_date != "unknown"
                and file_date not in self.date_filter_days)

    def _match_line_filter(self, timestamp_str):
        """判断行内时间戳是否匹配用户指定的日期/小时过滤条件。

        Args:
            timestamp_str: 行内时间戳字符串，格式 'YYYY-MM-DD HH:MM:SS'
        Returns:
            (match, actual_date): match 为 True 表示命中过滤条件；
                actual_date 为行的实际日期 'YYYY-MM-DD'
        """
        if not timestamp_str:
            return False, None
        line_date = timestamp_str[:10]
        if self._has_hour_filter:
            # 有小时级过滤条件：先检查天级命中，再检查小时级
            line_hour = timestamp_str[:10] + "T" + timestamp_str[11:13]  # 'YYYY-MM-DDTHH'
            # 如果 date_filter 中有该天的天级条目，直接命中
            if line_date in self.date_filter:
                return True, line_date
            # 检查小时级条目
            if line_hour in self.date_filter:
                return True, line_date
            return False, line_date
        else:
            # 纯天级过滤
            if line_date in self.date_filter_days:
                return True, line_date
            return False, line_date

    def _yield_cpu(self):
        """每处理 N 行让出 CPU，避免独占"""
        self._line_counter += 1
        if self._line_counter % CPU_YIELD_INTERVAL == 0:
            time.sleep(0.01)  # 让出 10ms

    def analyze_all(self):
        for dir_name, files_by_type in self.all_files.items():
            # 目录扫描模式下，按 --log-types 过滤文件类型
            if self.log_types:
                files_by_type = {k: v for k, v in files_by_type.items() if k in self.log_types}
                if not files_by_type:
                    print(f"\n  {c(YELLOW, '[跳过]')} {dir_name} 中无匹配的日志类型", file=sys.stderr)
                    continue
            print(f"\n  {c(BOLD, '='*56)}", file=sys.stderr)
            print(f"  {c(CYAN, '[分析]')} {c(BOLD, dir_name)}", file=sys.stderr)
            if self.date_filter:
                hint = "小时级过滤" if self._has_hour_filter else "自动包含前一天文件以覆盖跨日日志"
                print(f"  {c(DIM, '日期过滤:')} {', '.join(sorted(self.date_filter))}"
                      f" {c(DIM, '(' + hint + ')')}", file=sys.stderr)
            print(f"  {c(BOLD, '='*56)}", file=sys.stderr)
            t0 = time.time()
            self.results[dir_name] = self._analyze_dir(dir_name, files_by_type)
            elapsed = time.time() - t0
            mem = _get_mem_mb()
            print(f"  {c(GREEN, '[完成]')} {dir_name} 分析耗时 {elapsed:.1f}s, 内存 {mem:.0f}MB",
                  file=sys.stderr)

    def _analyze_dir(self, dir_name, files_by_type):
        result = {
            "dir_name": dir_name,
            "overview": self._analyze_overview(files_by_type),
        }

        analyzed_count = 0
        total_types = len([t for t in files_by_type.keys() if not self.log_types or t in self.log_types])
        
        def _progress():
            return f"[{analyzed_count}/{total_types}]"
        
        # 根据指定的日志类型过滤分析
        if not self.log_types or "interf" in self.log_types:
            if "interf" in files_by_type:
                analyzed_count += 1
                print(f"  {c(CYAN, _progress())} 分析 interf_instance ...", file=sys.stderr)
                result["interf"] = self._analyze_interf(files_by_type["interf"])
            else:
                print(f"  {c(DIM, '[跳过]')} 无 interf_instance 日志", file=sys.stderr)

        if not self.log_types or "sql" in self.log_types:
            if "sql" in files_by_type:
                analyzed_count += 1
                print(f"  {c(CYAN, _progress())} 分析 sql_instance ...", file=sys.stderr)
                result["sql"] = self._analyze_sql_instance(files_by_type["sql"])

        if not self.log_types or "slow_sql" in self.log_types:
            if "slow_sql" in files_by_type:
                analyzed_count += 1
                print(f"  {c(CYAN, _progress())} 分析 slow_sql_instance ...", file=sys.stderr)
                result["slow_sql"] = self._analyze_slow_sql(files_by_type["slow_sql"])

        if not self.log_types or "sys" in self.log_types:
            if "sys" in files_by_type:
                analyzed_count += 1
                print(f"  {c(CYAN, _progress())} 分析 sys_instance ...", file=sys.stderr)
                result["sys"] = self._analyze_sys(files_by_type["sys"])

        if not self.log_types and "route" in files_by_type:
            analyzed_count += 1
            print(f"  {c(CYAN, _progress())} 分析 route_instance ...", file=sys.stderr)
            result["route"] = self._analyze_route(files_by_type["route"])

        return result

    def _analyze_overview(self, files_by_type):
        """日志概览"""
        overview = {"log_types": {}, "dates": set(), "total_files": 0, "total_size": 0}
        for log_type, file_list in files_by_type.items():
            type_size = 0
            type_dates = set()
            removed = []
            for i, (fpath, date, port) in enumerate(file_list):
                try:
                    sz = os.path.getsize(fpath)
                except (FileNotFoundError, OSError):
                    print(f"    {c(YELLOW, '[警告]')} 文件已被删除，跳过: {os.path.basename(fpath)}", file=sys.stderr)
                    removed.append(i)
                    continue
                type_size += sz
                overview["total_size"] += sz
                overview["total_files"] += 1
                if date != "unknown":
                    type_dates.add(date)
                    overview["dates"].add(date)
            # 从 file_list 中移除已删除的文件（倒序删除避免索引偏移）
            for i in reversed(removed):
                file_list.pop(i)
            overview["log_types"][log_type] = {
                "count": len(file_list),
                "size": type_size,
                "dates": sorted(type_dates),
                "port": file_list[0][2] if file_list else "",
            }
        overview["dates"] = sorted(overview["dates"])
        return overview

    def _analyze_interf(self, file_list):
        """分析 interf_instance 日志"""
        daily_stats = {}  # date -> {count, timecost_sum, timecost_bins, ...}
        hourly_counts = Counter()
        minute_counts = Counter()
        user_counts = Counter()
        db_counts = Counter()
        sql_type_counts = Counter()
        resultcode_counts = Counter()
        timecost_bins_total = OrderedDict([(b[2], 0) for b in TIMECOST_BINS])
        # 使用 heapq 维护 top_n，避免无界增长（最小堆，按 timecost 排序）
        heap_size = max(self.top_n * 2, 100)  # 稍大于 top_n 以保留余量
        high_timecost_heap = []  # heapq: (timecost, seq, sql, db, timestamp)
        _heap_seq = 0  # 序列号，避免元组比较时比到字符串
        # 修改为记录 (pattern, db) 组合的统计
        sql_patterns = Counter()
        _sql_patterns_max_keys = 100000  # SQL 模式 key 数量上限，防止内存爆炸
        _sql_patterns_pruned = False
        new_conn_count = 0
        error_details_heap = []  # heapq: 最多保留 50 条最新的错误
        _err_seq = 0
        error_details_max = 50
        total_lines = 0
        total_timecost_sum = 0.0
        flame_data = []  # (timestamp_str, timecost_ms) 用于火焰图
        flame_sample_rate = 1  # 采样率，数据太多时自动降采样

        for fpath, date, port in sorted(file_list, key=lambda x: x[1]):
            try:
                fsize = os.path.getsize(fpath)
            except (FileNotFoundError, OSError):
                print(f"    {c(YELLOW, '[警告]')} 文件已被删除，跳过: {os.path.basename(fpath)}", file=sys.stderr)
                continue
            if fsize == 0:
                continue
            extra_file = self._is_extra_file(date)
            need_line_filter = extra_file or self._has_hour_filter
            label = f"{'(跨日)' if extra_file else ''}"
            print(f"    {c(DIM, '解析')} {os.path.basename(fpath)} ({self._fmt_size(fsize)}) {label}...", file=sys.stderr)
            ft0 = time.time()
            line_count = 0
            skipped_lines = 0

            try:
                fh = open(fpath, "r", errors="replace")
            except (FileNotFoundError, OSError):
                print(f"    {c(YELLOW, '[警告]')} 文件已被删除，跳过: {os.path.basename(fpath)}", file=sys.stderr)
                continue
            with fh as f:
                for line in f:
                    if self.sample_limit > 0 and line_count >= self.sample_limit:
                        break
                    fields = parse_interf_kv(line)
                    if not fields:
                        continue
                    # 行级过滤：跨日文件或小时级过滤时，按行内时间戳精确过滤
                    if need_line_filter:
                        ts = fields.get("_timestamp", "")
                        match, line_date = self._match_line_filter(ts)
                        if ts and not match:
                            skipped_lines += 1
                            continue
                        actual_date = line_date if line_date else date
                    else:
                        actual_date = date
                    line_count += 1
                    self._yield_cpu()

                    # 按实际日期累积统计
                    if actual_date not in daily_stats:
                        daily_stats[actual_date] = {
                            "count": 0,
                            "timecost_sum": 0.0,
                            "timecost_count": 0,
                            "bins": OrderedDict([(b[2], 0) for b in TIMECOST_BINS]),
                        }
                    ds = daily_stats[actual_date]
                    ds["count"] += 1

                    # 用户
                    if "user" in fields:
                        user_counts[fields["user"]] += 1
                    # 数据库
                    if "db" in fields:
                        db_counts[fields["db"]] += 1
                    # SQL 类型
                    if "sql_type" in fields:
                        sql_type_counts[fields["sql_type"]] += 1
                    # 结果码
                    if "resultcode" in fields:
                        rc = fields["resultcode"]
                        resultcode_counts[rc] += 1
                        if rc != "0":
                            errinfo = fields.get("errinfo", "")
                            err_item = (
                                rc,
                                fields.get("sql", "")[:150],
                                fields.get("db", ""),
                                errinfo[:200],
                                fields.get("_timestamp", ""),
                            )
                            # 用序列号作堆排序键，保留最新的 N 条
                            _err_seq += 1
                            if len(error_details_heap) < error_details_max:
                                heapq.heappush(error_details_heap, (_err_seq, err_item))
                            else:
                                heapq.heapreplace(error_details_heap, (_err_seq, err_item))
                    # 耗时
                    if "timecost" in fields:
                        try:
                            tc = float(fields["timecost"])
                            ds["timecost_sum"] += tc
                            ds["timecost_count"] += 1
                            total_timecost_sum += tc
                            for low, high, label in TIMECOST_BINS:
                                if low <= tc < high:
                                    timecost_bins_total[label] += 1
                                    ds["bins"][label] += 1
                                    break
                            if tc > 50:
                                _heap_seq += 1
                                item = (tc, _heap_seq, fields.get("sql", "")[:200],
                                        fields.get("db", ""), fields.get("_timestamp", ""))
                                if len(high_timecost_heap) < heap_size:
                                    heapq.heappush(high_timecost_heap, item)
                                elif tc > high_timecost_heap[0][0]:
                                    heapq.heapreplace(high_timecost_heap, item)
                            # 火焰图数据（降采样控制内存）
                            if line_count % flame_sample_rate == 0:
                                flame_data.append((
                                    fields.get("_timestamp", ""),
                                    tc,
                                    fields.get("sql", "")[:300],
                                    fields.get("db", ""),
                                ))
                        except ValueError:
                            pass
                    # 每小时/分钟
                    if "_hour" in fields:
                        hourly_counts[fields["_hour"]] += 1
                    if "_minute" in fields:
                        minute_counts[fields["_minute"]] += 1
                    # 新连接
                    if fields.get("new_connnum", "0") != "0":
                        try:
                            new_conn_count += int(fields["new_connnum"])
                        except ValueError:
                            pass
                    # SQL 模式及数据库统计
                    if "sql" in fields and "db" in fields:
                        pattern = normalize_sql(fields["sql"])
                        db = fields["db"]
                        if pattern:
                            # 记录 (pattern, db) 组合
                            pattern_db_key = f"{pattern} | {db}"
                            if pattern_db_key in sql_patterns or len(sql_patterns) < _sql_patterns_max_keys:
                                sql_patterns[pattern_db_key] += 1
                            elif not _sql_patterns_pruned:
                                # 达到上限后只更新已有 key，不再新增
                                _sql_patterns_pruned = True

            total_lines += line_count
            ft_elapsed = time.time() - ft0
            ft_mem = _get_mem_mb()
            skip_info = f", 跳过 {skipped_lines:,} 行(非目标日期)" if skipped_lines else ""
            print(f"           ✓ {line_count:,} 行{skip_info}, 耗时 {ft_elapsed:.1f}s, 内存 {ft_mem:.0f}MB", file=sys.stderr)
            # 动态调整火焰图采样率，限制总数据点 <= 50000
            if len(flame_data) > 50000:
                flame_sample_rate = max(flame_sample_rate * 2, 2)
                flame_data = flame_data[::2]  # 对已有数据也降采样

        # 从堆中提取 high_timecost top N（降序排列）
        high_timecost = sorted(high_timecost_heap, key=lambda x: -x[0])[:self.top_n]
        # 去掉序列号：(tc, seq, sql, db, ts) → (tc, sql, db, ts)
        high_timecost = [(tc, sql, db, ts) for tc, _seq, sql, db, ts in high_timecost]

        # 从堆中提取 error_details（按序列号降序 = 最新的在前）
        error_details = [item for _seq, item in sorted(error_details_heap, key=lambda x: -x[0])]

        # 最繁忙分钟
        busiest_minutes = minute_counts.most_common(10)

        # 火焰图数据最终截断
        if len(flame_data) > 50000:
            flame_data = flame_data[:50000]

        return {
            "daily_stats": daily_stats,
            "hourly_counts": OrderedDict(sorted(hourly_counts.items())),
            "busiest_minutes": busiest_minutes,
            "user_counts": user_counts,
            "db_counts": db_counts,
            "sql_type_counts": sql_type_counts,
            "resultcode_counts": resultcode_counts,
            "timecost_bins": timecost_bins_total,
            "high_timecost": high_timecost,
            "sql_patterns": sql_patterns.most_common(self.top_n),
            "new_conn_count": new_conn_count,
            "error_details": error_details,
            "total_lines": total_lines,
            "total_timecost_sum": total_timecost_sum,
            "flame_data": flame_data,
        }

    def _analyze_sql_instance(self, file_list):
        """分析 sql_instance 日志 (提取高耗时SQL详情)"""
        # 使用 heapq 维护 top_n，避免无界增长
        heap_size = max(self.top_n * 2, 100)
        high_timecost_heap = []
        _heap_seq = 0
        for fpath, date, port in sorted(file_list, key=lambda x: x[1]):
            try:
                fsize = os.path.getsize(fpath)
            except (FileNotFoundError, OSError):
                print(f"    {c(YELLOW, '[警告]')} 文件已被删除，跳过: {os.path.basename(fpath)}", file=sys.stderr)
                continue
            if fsize == 0:
                continue
            extra_file = self._is_extra_file(date)
            need_line_filter = extra_file or self._has_hour_filter
            label = f"{'(跨日)' if extra_file else ''}"
            print(f"    {c(DIM, '解析')} {os.path.basename(fpath)} ({self._fmt_size(fsize)}) {label}...", file=sys.stderr)
            ft0 = time.time()
            line_count = 0
            skipped_lines = 0
            try:
                fh = open(fpath, "r", errors="replace")
            except (FileNotFoundError, OSError):
                print(f"    {c(YELLOW, '[警告]')} 文件已被删除，跳过: {os.path.basename(fpath)}", file=sys.stderr)
                continue
            with fh as f:
                for line in f:
                    if self.sample_limit > 0 and line_count >= self.sample_limit:
                        break
                    fields = parse_sql_instance_line(line)
                    if not fields or "timecost" not in fields:
                        continue
                    # 行级过滤：跨日文件或小时级过滤
                    if need_line_filter:
                        ts = fields.get("_timestamp", "")
                        match, _ = self._match_line_filter(ts)
                        if ts and not match:
                            skipped_lines += 1
                            continue
                    line_count += 1
                    try:
                        tc = float(fields["timecost"])
                        if tc > 20:
                            _heap_seq += 1
                            item = (tc, _heap_seq, fields.get("sql", "")[:200],
                                    fields.get("user", ""), fields.get("_timestamp", ""))
                            if len(high_timecost_heap) < heap_size:
                                heapq.heappush(high_timecost_heap, item)
                            elif tc > high_timecost_heap[0][0]:
                                heapq.heapreplace(high_timecost_heap, item)
                    except ValueError:
                        pass
            ft_elapsed = time.time() - ft0
            ft_mem = _get_mem_mb()
            skip_info = f", 跳过 {skipped_lines:,} 行(非目标日期)" if skipped_lines else ""
            print(f"           ✓ {line_count:,} 行{skip_info}, 耗时 {ft_elapsed:.1f}s, 内存 {ft_mem:.0f}MB", file=sys.stderr)
        # 从堆中提取 top N（降序）
        high_timecost = sorted(high_timecost_heap, key=lambda x: -x[0])[:self.top_n]
        high_timecost = [(tc, sql, user, ts) for tc, _seq, sql, user, ts in high_timecost]
        return {"high_timecost": high_timecost}

    def _analyze_slow_sql(self, file_list):
        """分析慢SQL日志"""
        total_count = 0
        daily_counts = Counter()
        schema_counts = Counter()
        user_counts = Counter()
        # 使用 heapq 维护 top_n 最慢的 SQL（按 query_time 最小堆）
        top_slow_heap = []
        _heap_seq = 0
        for fpath, date, port in sorted(file_list, key=lambda x: x[1]):
            try:
                fsize = os.path.getsize(fpath)
            except (FileNotFoundError, OSError):
                print(f"    {c(YELLOW, '[警告]')} 文件已被删除，跳过: {os.path.basename(fpath)}", file=sys.stderr)
                continue
            extra_file = self._is_extra_file(date)
            need_line_filter = extra_file or self._has_hour_filter
            label = f"{'(跨日)' if extra_file else ''}"
            print(f"    {c(DIM, '解析')} {os.path.basename(fpath)} ({self._fmt_size(fsize)}) {label}...", file=sys.stderr)
            ft0 = time.time()
            blocks = parse_slow_sql_blocks(fpath)
            # 行级过滤：跨日文件或小时级过滤
            skipped = 0
            if need_line_filter:
                orig_count = len(blocks)
                filtered = []
                for b in blocks:
                    time_str = b.get("time", "")
                    tm = re.search(r"(\d{4}-\d{2}-\d{2})", time_str)
                    if tm:
                        block_date = tm.group(1)
                        if self._has_hour_filter:
                            hm = re.search(r"(\d{4}-\d{2}-\d{2})[T ](\d{2})", time_str)
                            block_hour = block_date + "T" + hm.group(2) if hm else None
                            if block_date in self.date_filter or (block_hour and block_hour in self.date_filter):
                                filtered.append(b)
                                daily_counts[block_date] += 1
                        else:
                            if block_date in self.date_filter_days:
                                filtered.append(b)
                                daily_counts[block_date] += 1
                    else:
                        filtered.append(b)
                        daily_counts[date] += 1
                skipped = orig_count - len(filtered)
                blocks = filtered
            else:
                daily_counts[date] += len(blocks)
            # 边遍历边统计 + heapq 维护 top N，避免全量保留
            for b in blocks:
                total_count += 1
                if "schema" in b:
                    schema_counts[b["schema"]] += 1
                if "user" in b:
                    user_counts[b["user"]] += 1
                qt = b.get("query_time", 0)
                _heap_seq += 1
                if len(top_slow_heap) < self.top_n:
                    heapq.heappush(top_slow_heap, (qt, _heap_seq, b))
                elif qt > top_slow_heap[0][0]:
                    heapq.heapreplace(top_slow_heap, (qt, _heap_seq, b))
            ft_elapsed = time.time() - ft0
            ft_mem = _get_mem_mb()
            skip_info = f", 跳过 {skipped:,} 条(非目标日期)" if need_line_filter and skipped else ""
            print(f"           ✓ {len(blocks):,} 条慢SQL{skip_info}, 耗时 {ft_elapsed:.1f}s, 内存 {ft_mem:.0f}MB", file=sys.stderr)
        # 从堆中提取 top N（降序）
        top_slow = [b for _qt, _seq, b in sorted(top_slow_heap, key=lambda x: -x[0])]
        return {
            "total_count": total_count,
            "daily_counts": OrderedDict(sorted(daily_counts.items())),
            "top_slow": top_slow,
            "schema_counts": schema_counts,
            "user_counts": user_counts,
        }

    def _analyze_sys(self, file_list):
        """分析系统日志"""
        daily_counts = Counter()
        error_types = Counter()
        zk_errors = 0
        event_timeout = 0
        sql_syntax_errors = 0
        sample_errors = []

        for fpath, date, port in sorted(file_list, key=lambda x: x[1]):
            try:
                fsize = os.path.getsize(fpath)
            except (FileNotFoundError, OSError):
                print(f"    {c(YELLOW, '[警告]')} 文件已被删除，跳过: {os.path.basename(fpath)}", file=sys.stderr)
                continue
            if fsize == 0:
                daily_counts[date] = 0
                continue
            extra_file = self._is_extra_file(date)
            need_line_filter = extra_file or self._has_hour_filter
            label = f"{'(跨日)' if extra_file else ''}"
            print(f"    {c(DIM, '解析')} {os.path.basename(fpath)} ({self._fmt_size(fsize)}) {label}...", file=sys.stderr)
            ft0 = time.time()
            lines = parse_sys_instance(fpath, max_lines=50000)
            # 行级过滤：跨日文件或小时级过滤
            if need_line_filter:
                orig_count = len(lines)
                filtered_lines = []
                for line in lines:
                    lm = re.match(r"\[(\d{4}-\d{2}-\d{2}) (\d{2})", line)
                    if lm:
                        line_date = lm.group(1)
                        if self._has_hour_filter:
                            line_hour = line_date + "T" + lm.group(2)
                            if line_date in self.date_filter or line_hour in self.date_filter:
                                filtered_lines.append(line)
                                daily_counts[line_date] += 1
                        else:
                            if line_date in self.date_filter_days:
                                filtered_lines.append(line)
                                daily_counts[line_date] += 1
                    else:
                        filtered_lines.append(line)  # 无法解析日期时保留
                        daily_counts[date] += 1
                skipped = orig_count - len(filtered_lines)
                lines = filtered_lines
            else:
                daily_counts[date] = len(lines)
            for line in lines:
                if "ZOO_ERROR" in line or "zk is null" in line:
                    zk_errors += 1
                if "event_timecost" in line and "more than event_threshold" in line:
                    event_timeout += 1
                if "errocode:1064" in line or "err packet,err info:You have an error" in line:
                    sql_syntax_errors += 1
                # 提取源文件
                src_m = re.search(r"/(\w+\.(?:cpp|h)):\d+:(\w+)", line)
                if src_m:
                    error_types[f"{src_m.group(1)}:{src_m.group(2)}"] += 1
                if len(sample_errors) < 20 and "ERROR" in line:
                    sample_errors.append(line[:300])
            ft_elapsed = time.time() - ft0
            ft_mem = _get_mem_mb()
            skip_info = f", 跳过 {skipped:,} 行(非目标日期)" if extra_file and skipped else ""
            print(f"           ✓ {len(lines):,} 行{skip_info}, 耗时 {ft_elapsed:.1f}s, 内存 {ft_mem:.0f}MB", file=sys.stderr)

        return {
            "daily_counts": OrderedDict(sorted(daily_counts.items())),
            "error_types": error_types.most_common(self.top_n),
            "zk_errors": zk_errors,
            "event_timeout": event_timeout,
            "sql_syntax_errors": sql_syntax_errors,
            "sample_errors": sample_errors,
        }

    def _analyze_route(self, file_list):
        """分析路由日志"""
        daily_counts = Counter()
        for fpath, date, port in sorted(file_list, key=lambda x: x[1]):
            try:
                fsize = os.path.getsize(fpath)
            except (FileNotFoundError, OSError):
                print(f"    {c(YELLOW, '[警告]')} 文件已被删除，跳过: {os.path.basename(fpath)}", file=sys.stderr)
                continue
            if fsize == 0:
                daily_counts[date] = 0
                continue
            extra_file = self._is_extra_file(date)
            need_line_filter = extra_file or self._has_hour_filter
            label = f"{'(跨日)' if extra_file else ''}"
            print(f"    {c(DIM, '解析')} {os.path.basename(fpath)} ({self._fmt_size(fsize)}) {label}...", file=sys.stderr)
            ft0 = time.time()
            count = 0
            skipped = 0
            try:
                fh = open(fpath, "r", errors="replace")
            except (FileNotFoundError, OSError):
                print(f"    {c(YELLOW, '[警告]')} 文件已被删除，跳过: {os.path.basename(fpath)}", file=sys.stderr)
                continue
            with fh as f:
                for line in f:
                    # 行级过滤：跨日文件或小时级过滤
                    if need_line_filter:
                        lm = re.match(r"\[(\d{4}-\d{2}-\d{2}) (\d{2})", line)
                        if lm:
                            line_date = lm.group(1)
                            if self._has_hour_filter:
                                line_hour = line_date + "T" + lm.group(2)
                                if line_date not in self.date_filter and line_hour not in self.date_filter:
                                    skipped += 1
                                    continue
                            else:
                                if line_date not in self.date_filter_days:
                                    skipped += 1
                                    continue
                            daily_counts[line_date] += 1
                        else:
                            daily_counts[date] += 1
                    else:
                        daily_counts[date] += 1
                    count += 1
            ft_elapsed = time.time() - ft0
            ft_mem = _get_mem_mb()
            skip_info = f", 跳过 {skipped:,} 行(非目标日期)" if skipped else ""
            print(f"           ✓ {count:,} 行{skip_info}, 耗时 {ft_elapsed:.1f}s, 内存 {ft_mem:.0f}MB", file=sys.stderr)
        return {"daily_counts": OrderedDict(sorted(daily_counts.items()))}

    # ============================================================
    # 数据导出
    # ============================================================

    # key 缩写映射（减小 JSON 文件体积）
    _KEY_MAP = {
        # 顶层元数据
        "version": "v", "export_time": "et", "hostname": "hn", "log_dirs": "ld",
        "top_n": "tn", "sample_limit": "sl", "date_filter": "df",
        "results": "r",
        # overview
        "overview": "ov", "dates": "dt", "port": "pt",
        "file_count": "fc", "total_size": "ts",
        # interf
        "interf": "itf", "daily_stats": "ds", "hourly_counts": "hc",
        "busiest_minutes": "bm", "user_counts": "uc", "db_counts": "dc",
        "sql_type_counts": "stc", "resultcode_counts": "rc",
        "timecost_bins": "tb", "high_timecost": "ht",
        "sql_patterns": "sp", "new_conn_count": "ncc",
        "error_details": "ed", "total_lines": "tl",
        "total_timecost_sum": "tts", "flame_data": "fd",
        "count": "c", "timecost_sum": "tcs", "timecost_count": "tcc",
        "bins": "b",
        # sql_instance
        "sql": "sq",
        # slow_sql
        "slow_sql": "ss", "total_count": "tc", "daily_counts": "dcs",
        "top_slow": "tsw", "schema_counts": "sc",
        "query_time": "qt", "lock_time": "lt",
        "rows_sent": "rs", "rows_examined": "re",
        "schema": "sch", "user": "u", "client_ip": "ci",
        "backend": "be", "time": "t",
        # sys
        "sys": "sy", "error_types": "ety", "zk_errors": "zk",
        "event_timeout": "eto", "sql_syntax_errors": "sse",
        "sample_errors": "se",
        # route
        "route": "rt",
    }

    # 反向映射（用于数据还原）
    _KEY_MAP_REV = {v: k for k, v in _KEY_MAP.items()}

    def _compact_keys(self, obj):
        """递归缩短 dict key 以减小 JSON 体积"""
        km = self._KEY_MAP
        if isinstance(obj, dict):
            return {km.get(k, k): self._compact_keys(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._compact_keys(i) for i in obj]
        return obj

    def export_data(self, name, export_dir, compress=False):
        """将分析结果导出为 JSON 数据文件（紧凑格式、短 key），支持后续整合分析。

        使用分块序列化策略：逐目录序列化后立即释放中间变量，避免整体深拷贝
        导致内存峰值翻倍。

        Args:
            name: 数据文件名称（不含扩展名）
            export_dir: 保存目录
            compress: 是否使用 gzip 压缩
        Returns:
            导出文件的完整路径
        """
        os.makedirs(export_dir, exist_ok=True)
        ext = ".json.gz" if compress else ".json"
        filepath = os.path.join(export_dir, f"{name}{ext}")

        def _serialize(obj):
            """将分析结果中的特殊类型转为 JSON 可序列化格式"""
            if isinstance(obj, (Counter, OrderedDict)):
                return dict(obj)
            if isinstance(obj, set):
                return sorted(obj)
            if isinstance(obj, datetime):
                return obj.strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(obj, float):
                if obj == float("inf"):
                    return "Infinity"
                if obj == float("-inf"):
                    return "-Infinity"
            raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

        # 分块写入：先写元数据，再逐目录写 results，避免一次性深拷贝
        import socket as _socket
        meta = self._compact_keys({
            "version": VERSION,
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "hostname": _socket.gethostname(),
            "log_dirs": self.log_dirs,
            "top_n": self.top_n,
            "sample_limit": self.sample_limit,
            "date_filter": sorted(self.date_filter) if self.date_filter else None,
        })
        km = self._KEY_MAP
        results_key = km.get("results", "results")
        keymap_key = "_key_map"

        chunks = []
        # 元数据部分
        meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"), default=_serialize)
        # 去掉末尾的 }，准备拼接 results
        chunks.append(meta_json[:-1])

        # results 部分：逐目录序列化
        chunks.append(f',"{results_key}":{{')
        dir_items = list(self.results.items())
        for idx, (dir_name, dir_result) in enumerate(dir_items):
            compact_result = self._compact_keys(dir_result)
            dir_json = json.dumps(
                compact_result, ensure_ascii=False, separators=(",", ":"), default=_serialize
            )
            del compact_result  # 立即释放
            sep = "," if idx > 0 else ""
            dir_key_json = json.dumps(dir_name, ensure_ascii=False)
            chunks.append(f'{sep}{dir_key_json}:{dir_json}')

        # _key_map 部分
        keymap_json = json.dumps(self._KEY_MAP, ensure_ascii=False, separators=(",", ":"))
        chunks.append(f'}},"{keymap_key}":{keymap_json}}}')

        json_bytes = "".join(chunks).encode("utf-8")
        del chunks

        if compress:
            import gzip as _gzip
            with _gzip.open(filepath, "wb", compresslevel=6) as f:
                f.write(json_bytes)
        else:
            with open(filepath, "wb") as f:
                f.write(json_bytes)

        return filepath

    # ============================================================
    # 报告生成
    # ============================================================

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
            buf.write(f"  TDSQL Gateway 日志分析报告 - {dir_name}\n")
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
            buf.write(f"# TDSQL Gateway 日志分析报告 - {dir_name}\n\n")
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
        buf.write(HTML_TEMPLATE_HEAD)
        self._flame_modal_written = False
        dir_names = list(self.results.keys())
        multi_dir = len(dir_names) > 1
        for idx, (dir_name, data) in enumerate(self.results.items()):
            # 多目录时给 section ID 加前缀避免重复
            self._sec_prefix = f"d{idx}-" if multi_dir else ""
            self._sec_group = dir_name if multi_dir else ""
            escaped_name = _h(dir_name)
            group_attr = f' data-group="{escaped_name}"' if multi_dir else ""
            buf.write(f'<h1 id="{self._sec_prefix}top"{group_attr}>'
                      f'TDSQL Gateway 日志分析报告 - {escaped_name}</h1>\n')
            buf.write(f'<p class="meta">生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>\n')
            self._write_overview(buf, data, fmt="html")
            if "interf" in data:
                self._write_interf_report(buf, data["interf"], fmt="html")
                self._write_flame_chart(buf, data["interf"], fmt="html")
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
        buf.write(HTML_TEMPLATE_TAIL)
        return buf.getvalue()

    # ---------- HTML section 辅助 ----------

    def _html_section_start(self, buf, section_id, title):
        """开始一个可折叠的 HTML 章节（多目录时自动加前缀避免 ID 冲突）"""
        prefix = getattr(self, '_sec_prefix', '')
        group = getattr(self, '_sec_group', '')
        full_id = f"{prefix}{section_id}"
        group_attr = f' data-group="{_h(group)}"' if group else ''
        buf.write(f'<div class="section" id="{full_id}"{group_attr}>\n')
        buf.write(f'<h2 class="section-header">{_h(title)}</h2>\n')
        buf.write('<div class="section-content">\n')

    @staticmethod
    def _html_section_end(buf):
        """结束一个可折叠的 HTML 章节"""
        buf.write('</div></div>\n')

    # ---------- 各章节输出 ----------

    def _write_overview(self, buf, data, fmt="terminal"):
        ov = data["overview"]
        is_html = fmt == "html"
        if fmt == "md":
            buf.write("## 一、日志概览\n\n")
            buf.write(f"| 项目 | 值 |\n|------|----|\n")
            buf.write(f"| 日期跨度 | {ov['dates'][0] if ov['dates'] else 'N/A'} ~ {ov['dates'][-1] if ov['dates'] else 'N/A'} ({len(ov['dates'])} 天) |\n")
            buf.write(f"| 文件总数 | {ov['total_files']} |\n")
            buf.write(f"| 总大小 | {self._fmt_size(ov['total_size'])} |\n\n")
            buf.write("| 日志类型 | 说明 | 文件数 | 大小 | 端口 |\n")
            buf.write("|----------|------|--------|------|------|\n")
            for lt, info in sorted(ov["log_types"].items()):
                desc = LOG_TYPES.get(lt, lt)
                buf.write(f"| {lt}_instance | {desc} | {info['count']} | {self._fmt_size(info['size'])} | {info['port']} |\n")
            buf.write("\n")
        elif is_html:
            self._html_section_start(buf, 'sec-overview', '一、日志概览')
            date_range = f"{ov['dates'][0]} ~ {ov['dates'][-1]}" if ov['dates'] else 'N/A'
            buf.write('<div class="summary-grid">\n')
            buf.write(f'<div class="summary-card"><div class="label">日期跨度</div><div class="value">{len(ov["dates"])} 天</div><div class="label">{_h(date_range)}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">文件总数</div><div class="value">{ov["total_files"]}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">总大小</div><div class="value">{self._fmt_size(ov["total_size"])}</div></div>\n')
            buf.write('</div>\n')
            buf.write('<table><tr><th>日志类型</th><th>说明</th><th>文件数</th><th>大小</th><th>端口</th></tr>\n')
            for lt, info in sorted(ov["log_types"].items()):
                desc = LOG_TYPES.get(lt, lt)
                buf.write(f'<tr><td><code>{_h(lt)}_instance</code></td><td>{_h(desc)}</td><td class="num">{info["count"]}</td><td class="num">{self._fmt_size(info["size"])}</td><td>{_h(info["port"])}</td></tr>\n')
            buf.write('</table>\n')
            self._html_section_end(buf)
        else:
            buf.write(f"\n{'─'*80}\n")
            buf.write("【一、日志概览】\n")
            buf.write(f"  日期跨度: {ov['dates'][0] if ov['dates'] else 'N/A'} ~ {ov['dates'][-1] if ov['dates'] else 'N/A'} ({len(ov['dates'])} 天)\n")
            buf.write(f"  文件总数: {ov['total_files']}\n")
            buf.write(f"  总大小:   {self._fmt_size(ov['total_size'])}\n\n")
            buf.write(f"  {'日志类型':<25} {'说明':<15} {'文件数':>5} {'大小':>10} {'端口':>6}\n")
            buf.write(f"  {'─'*70}\n")
            for lt, info in sorted(ov["log_types"].items()):
                desc = LOG_TYPES.get(lt, lt)
                buf.write(f"  {lt+'_instance':<25} {desc:<15} {info['count']:>5} {self._fmt_size(info['size']):>10} {info['port']:>6}\n")

    def _write_interf_report(self, buf, interf, fmt="terminal"):
        is_md = fmt == "md"
        is_html = fmt == "html"
        sep = "\n" if is_md else f"\n{'─'*80}\n"

        # -- 2. 每日请求量趋势 --
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
        max_daily = max((s["count"] for s in interf["daily_stats"].values()), default=1) or 1
        for date, stats in sorted(interf["daily_stats"].items()):
            cnt = stats["count"]
            avg = stats["timecost_sum"] / stats["timecost_count"] if stats["timecost_count"] > 0 else 0
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

        # -- 3. 每小时请求量分布 --
        max_hourly = max(interf["hourly_counts"].values()) if interf["hourly_counts"] else 1
        if is_html:
            self._html_section_end(buf)
            self._html_section_start(buf, 'sec-hourly', '三、每小时请求量分布')
            buf.write('<table><tr><th>时间</th><th>请求量</th><th>分布</th></tr>\n')
        elif is_md:
            buf.write("## 三、每小时请求量分布\n\n")
            buf.write("| 时间 | 请求量 | 柱状图 |\n|------|--------|--------|\n")
        else:
            buf.write(f"{sep}【三、每小时请求量分布】\n")
        for hour, cnt in interf["hourly_counts"].items():
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
        if interf["busiest_minutes"]:
            if is_html:
                buf.write('<h3>最繁忙的分钟 Top 10</h3>\n')
                buf.write('<table><tr><th>时间(分钟)</th><th>请求量</th></tr>\n')
            elif is_md:
                buf.write("**最繁忙的分钟 Top 10:**\n\n")
                buf.write("| 时间(分钟) | 请求量 |\n|------------|--------|\n")
            else:
                buf.write("  最繁忙的分钟 Top 10:\n")
            for minute, cnt in interf["busiest_minutes"]:
                if is_html:
                    buf.write(f'<tr><td>{_h(minute)}</td><td class="num">{cnt:,}</td></tr>\n')
                elif is_md:
                    buf.write(f"| {minute} | {cnt:,} |\n")
                else:
                    buf.write(f"    {minute}  {cnt:>6,}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

        # -- 4. SQL 耗时分布 --
        total = sum(interf["timecost_bins"].values()) or 1
        if is_html:
            self._html_section_end(buf)
            self._html_section_start(buf, 'sec-timecost', '四、SQL 耗时分布')
            buf.write('<table><tr><th>耗时区间</th><th>数量</th><th>占比</th><th>分布</th></tr>\n')
        elif is_md:
            buf.write("## 四、SQL 耗时分布\n\n")
            buf.write("| 耗时区间 | 数量 | 占比 | 分布 |\n|----------|------|------|------|\n")
        else:
            buf.write(f"{sep}【四、SQL 耗时分布】\n")
        bin_colors = {
            "<1ms": "bar bar-success",
            "1-5ms": "bar bar-success",
            "5-10ms": "bar",
            "10-50ms": "bar",
            "50-100ms": "bar bar-warning",
            "100-500ms": "bar bar-warning",
            "500ms-1s": "bar bar-danger",
            "1-3s": "bar bar-danger",
            "3-10s": "bar bar-danger",
            ">10s": "bar bar-danger",
        }
        for label, cnt in interf["timecost_bins"].items():
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

        # -- 5. 高耗时 SQL --
        if interf["high_timecost"]:
            if is_html:
                self._html_section_end(buf)
                self._html_section_start(buf, 'sec-high-tc', f'五、高耗时 SQL Top {self.top_n}')
                buf.write('<table><tr><th>#</th><th>耗时(ms)</th><th>数据库</th><th>SQL</th><th>时间</th></tr>\n')
            elif is_md:
                buf.write(f"## 五、高耗时 SQL Top {self.top_n}\n\n")
                buf.write("| # | 耗时(ms) | 数据库 | SQL | 时间 |\n|---|----------|--------|-----|------|\n")
            else:
                buf.write(f"{sep}【五、高耗时 SQL Top {self.top_n}】\n")
            for i, (tc, sql, db, ts) in enumerate(interf["high_timecost"], 1):
                sql_short = sql[:100].replace("|", "\\|").replace("\n", " ")
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

        # -- 6. 高频 SQL 模式 --
        if interf["sql_patterns"]:
            if is_html:
                self._html_section_end(buf)
                self._html_section_start(buf, 'sec-patterns', f'六、高频 SQL 模式 Top {self.top_n}')
                buf.write('<table><tr><th>#</th><th>次数</th><th>数据库</th><th>SQL模式</th></tr>\n')
            elif is_md:
                buf.write(f"## 六、高频 SQL 模式 Top {self.top_n}\n\n")
                buf.write("| # | 次数 | 数据库 | SQL模式 |\n|---|------|--------|---------|\n")
            else:
                buf.write(f"{sep}【六、高频 SQL 模式 Top {self.top_n}】\n")
            for i, (pattern_db_key, cnt) in enumerate(interf["sql_patterns"], 1):
                # 解析 pattern 和 db
                if ' | ' in pattern_db_key:
                    pattern, db = pattern_db_key.rsplit(' | ', 1)
                else:
                    pattern = pattern_db_key
                    db = "unknown"
                if is_html:
                    buf.write(f'<tr><td>{i}</td><td class="num">{cnt:,}</td><td class="db">{_h(db)}</td><td><code>{_h(pattern)}</code></td></tr>\n')
                elif is_md:
                    buf.write(f"| {i} | {cnt:,} | `{db}` | `{pattern}` |\n")
                else:
                    buf.write(f"    {i:2d}. {cnt:>8,} 次 | {db:<15} | {pattern[:80]}{'...' if len(pattern)>80 else ''}\n")
                    buf.write(f"  [{i:>2}] {cnt:>8,}  {pattern}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

        # -- 7. SQL 类型分布 --
        type_total = sum(interf["sql_type_counts"].values()) or 1
        if is_html:
            self._html_section_end(buf)
            self._html_section_start(buf, 'sec-sql-type', '七、SQL 类型分布')
            buf.write('<table><tr><th>sql_type</th><th>含义</th><th>数量</th><th>占比</th><th>分布</th></tr>\n')
        elif is_md:
            buf.write("## 七、SQL 类型分布\n\n")
            buf.write("| sql_type | 含义 | 数量 | 占比 |\n|----------|------|------|------|\n")
        else:
            buf.write(f"{sep}【七、SQL 类型分布】\n")
        for st, cnt in interf["sql_type_counts"].most_common():
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

        # -- 8. 用户 & 数据库分布 --
        user_total = sum(interf["user_counts"].values()) or 1
        db_total = sum(interf["db_counts"].values()) or 1
        if is_html:
            self._html_section_end(buf)
            self._html_section_start(buf, 'sec-user-db', '八、用户 & 数据库分布')
            buf.write('<h3>用户分布</h3>\n')
            buf.write('<table><tr><th>用户</th><th>请求数</th><th>占比</th></tr>\n')
        elif is_md:
            buf.write("## 八、用户 & 数据库分布\n\n")
            buf.write("**用户分布:**\n\n| 用户 | 请求数 | 占比 |\n|------|--------|------|\n")
        else:
            buf.write(f"{sep}【八、用户 & 数据库分布】\n")
            buf.write("  用户分布:\n")
        for user, cnt in interf["user_counts"].most_common(10):
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

        if is_html:
            buf.write('<h3>数据库分布</h3>\n')
            buf.write('<table><tr><th>数据库</th><th>请求数</th><th>占比</th></tr>\n')
        elif is_md:
            buf.write("**数据库分布:**\n\n| 数据库 | 请求数 | 占比 |\n|--------|--------|------|\n")
        else:
            buf.write("  数据库分布:\n")
        for db, cnt in interf["db_counts"].most_common(10):
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

        # -- 9. 错误码分析 --
        rc_total = sum(interf["resultcode_counts"].values()) or 1
        if is_html:
            self._html_section_end(buf)
            self._html_section_start(buf, 'sec-errors', '九、错误码分析')
            buf.write('<table><tr><th>错误码</th><th>含义</th><th>数量</th><th>占比</th></tr>\n')
        elif is_md:
            buf.write("## 九、错误码分析\n\n")
            buf.write("| 错误码 | 含义 | 数量 | 占比 |\n|--------|------|------|------|\n")
        else:
            buf.write(f"{sep}【九、错误码分析】\n")
        for rc, cnt in interf["resultcode_counts"].most_common():
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
        non_zero = [e for e in interf["error_details"]]
        if non_zero:
            if is_html:
                buf.write('<h3>错误请求详情 (最多50条)</h3>\n')
                buf.write('<table><tr><th>时间</th><th>错误码</th><th>数据库</th><th>错误信息</th><th>SQL</th></tr>\n')
            elif is_md:
                buf.write("**错误请求详情 (最多50条):**\n\n")
                buf.write("| 时间 | 错误码 | 数据库 | 错误信息 | SQL |\n|------|--------|--------|----------|-----|\n")
            else:
                buf.write("  错误请求详情:\n")
            for rc, sql, db, errinfo, ts in non_zero[:20]:
                desc = RESULTCODE_MAP.get(rc, rc)
                sql_s = sql[:80].replace("|", "\\|")
                errinfo_s = errinfo[:80].replace("|", "\\|")
                if is_html:
                    buf.write(f'<tr><td>{_h(ts)}</td><td>{_h(rc)}({_h(desc)})</td><td>{_h(db)}</td>'
                              f'<td><code>{_h(errinfo_s)}</code></td><td><code>{_h(sql_s)}</code></td></tr>\n')
                elif is_md:
                    buf.write(f"| {ts} | {rc}({desc}) | {db} | `{errinfo_s}` | `{sql_s}` |\n")
                else:
                    buf.write(f"    [{ts}] code={rc}({desc}) db={db}\n")
                    buf.write(f"      错误: {errinfo[:120]}\n")
                    buf.write(f"      SQL:  {sql[:120]}\n")
            if is_html:
                buf.write('</table>\n')
            buf.write("\n")

        # -- 10. 连接模式分析 --
        conn_count = interf["sql_type_counts"].get("11", 0)
        quit_count = interf["sql_type_counts"].get("1", 0)
        if is_html:
            self._html_section_end(buf)
            self._html_section_start(buf, 'sec-conn', '十、连接模式分析')
            buf.write('<div class="summary-grid">\n')
            buf.write(f'<div class="summary-card"><div class="label">新建连接</div><div class="value">{conn_count:,}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">断开连接</div><div class="value">{quit_count:,}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">new_connnum 累计</div><div class="value">{interf["new_conn_count"]:,}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">总请求量</div><div class="value">{interf["total_lines"]:,}</div></div>\n')
            if interf["total_lines"] > 0:
                conn_ratio = conn_count / interf["total_lines"] * 100
                buf.write(f'<div class="summary-card"><div class="label">连接建立占比</div><div class="value">{conn_ratio:.1f}%</div></div>\n')
            buf.write('</div>\n')
            if interf["total_lines"] > 0 and conn_ratio > 10:
                buf.write(f'<div class="alert alert-warning">⚠ 连接建立占比 {conn_ratio:.1f}%，疑似短连接模式，建议使用连接池。</div>\n')
            self._html_section_end(buf)
        elif is_md:
            buf.write("## 十、连接模式分析\n\n")
            buf.write(f"| 指标 | 值 |\n|------|----|\n")
            buf.write(f"| 新建连接(sql_type=11) | {conn_count:,} |\n")
            buf.write(f"| 断开连接(sql_type=1) | {quit_count:,} |\n")
            buf.write(f"| new_connnum 累计 | {interf['new_conn_count']:,} |\n")
            buf.write(f"| 总请求量 | {interf['total_lines']:,} |\n")
            if interf["total_lines"] > 0:
                conn_ratio = conn_count / interf["total_lines"] * 100
                buf.write(f"| 连接建立占比 | {conn_ratio:.1f}% |\n")
                if conn_ratio > 10:
                    buf.write(f"\n> **警告**: 连接建立占比 {conn_ratio:.1f}%，疑似短连接模式，建议使用连接池。\n")
            buf.write("\n")
        else:
            buf.write(f"{sep}【十、连接模式分析】\n")
            buf.write(f"  新建连接(sql_type=11):  {conn_count:>10,}\n")
            buf.write(f"  断开连接(sql_type=1):   {quit_count:>10,}\n")
            buf.write(f"  new_connnum 累计:       {interf['new_conn_count']:>10,}\n")
            buf.write(f"  总请求量:               {interf['total_lines']:>10,}\n")
            if interf["total_lines"] > 0:
                conn_ratio = conn_count / interf["total_lines"] * 100
                buf.write(f"  连接建立占比:           {conn_ratio:>9.1f}%\n")
                if conn_ratio > 10:
                    buf.write(f"\n  ⚠ 警告: 连接建立占比 {conn_ratio:.1f}%，疑似短连接模式，建议使用连接池。\n")
            buf.write("\n")

    def _write_sql_report(self, buf, sql_data, fmt="terminal"):
        is_md = fmt == "md"
        is_html = fmt == "html"
        if not sql_data["high_timecost"]:
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
        for i, (tc, sql, user, ts) in enumerate(sql_data["high_timecost"], 1):
            sql_short = sql[:100].replace("|", "\\|")
            if is_html:
                buf.write(f'<tr><td>{i}</td><td class="num">{tc:.1f}</td><td>{_h(user)}</td>'
                          f'<td><code>{_h(sql_short)}</code></td><td>{_h(ts)}</td></tr>\n')
            elif is_md:
                buf.write(f"| {i} | {tc:.1f} | {user} | `{sql_short}` | {ts} |\n")
            else:
                buf.write(f"  [{i:>2}] {tc:>10.1f}ms  user={user}  {ts}\n")
                buf.write(f"       {sql_short}\n")
        if is_html:
            buf.write('</table>\n')
            self._html_section_end(buf)
        buf.write("\n")

    def _write_slow_sql_report(self, buf, slow_data, fmt="terminal"):
        is_md = fmt == "md"
        is_html = fmt == "html"
        sep = "\n" if is_md else f"\n{'─'*80}\n"

        if is_html:
            self._html_section_start(buf, 'sec-slow-sql', '十一、慢SQL日志分析')
            buf.write(f'<p>慢SQL总数: <strong>{slow_data["total_count"]}</strong> 条</p>\n')
        elif is_md:
            buf.write("## 十一、慢SQL日志分析\n\n")
            buf.write(f"慢SQL总数: **{slow_data['total_count']}** 条\n\n")
        else:
            buf.write(f"{sep}【十一、慢SQL日志分析】\n")
            buf.write(f"  慢SQL总数: {slow_data['total_count']} 条\n\n")

        # 每日分布
        if is_html:
            buf.write('<h3>每日慢SQL数</h3>\n<table><tr><th>日期</th><th>数量</th></tr>\n')
        elif is_md:
            buf.write("**每日慢SQL数:**\n\n| 日期 | 数量 |\n|------|------|\n")
        else:
            buf.write("  每日慢SQL数:\n")
        for date, cnt in slow_data["daily_counts"].items():
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
        if slow_data["schema_counts"]:
            if is_html:
                buf.write('<h3>按Schema分布</h3>\n<table><tr><th>Schema</th><th>数量</th></tr>\n')
            elif is_md:
                buf.write("**按Schema分布:**\n\n| Schema | 数量 |\n|--------|------|\n")
            else:
                buf.write("  按Schema分布:\n")
            for schema, cnt in slow_data["schema_counts"].most_common():
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
        if slow_data["top_slow"]:
            if is_html:
                buf.write(f'<h3>Top {self.top_n} 慢SQL</h3>\n')
                buf.write('<table><tr><th>#</th><th>耗时(s)</th><th>Schema</th><th>用户</th><th>SQL</th></tr>\n')
            elif is_md:
                buf.write(f"**Top {self.top_n} 慢SQL:**\n\n")
                buf.write("| # | 耗时(s) | Schema | 用户 | SQL |\n|---|---------|--------|------|-----|\n")
            else:
                buf.write(f"  Top {self.top_n} 慢SQL:\n")
            for i, block in enumerate(slow_data["top_slow"][: self.top_n], 1):
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

        # 每日错误数
        if is_html:
            buf.write('<h3>每日系统日志条数</h3>\n<table><tr><th>日期</th><th>行数</th><th>状态</th></tr>\n')
        elif is_md:
            buf.write("**每日系统日志条数:**\n\n| 日期 | 行数 | 状态 |\n|------|------|------|\n")
        else:
            buf.write("  每日系统日志条数:\n")
        counts = list(sys_data["daily_counts"].values())
        avg_count = sum(counts) / len(counts) if counts else 0
        for date, cnt in sys_data["daily_counts"].items():
            status = ""
            is_abnormal = cnt > avg_count * 3 and cnt > 100
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
        if is_html:
            buf.write('<h3>关键异常统计</h3>\n')
            buf.write('<div class="summary-grid">\n')
            zk_cls = "danger" if sys_data['zk_errors'] > 0 else "muted"
            ev_cls = "danger" if sys_data['event_timeout'] > 0 else "muted"
            sq_cls = "danger" if sys_data['sql_syntax_errors'] > 0 else "muted"
            buf.write(f'<div class="summary-card"><div class="label">ZooKeeper 错误</div><div class="value" style="color:var(--{zk_cls})">{sys_data["zk_errors"]}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">事件处理超时</div><div class="value" style="color:var(--{ev_cls})">{sys_data["event_timeout"]}</div></div>\n')
            buf.write(f'<div class="summary-card"><div class="label">SQL语法错误</div><div class="value" style="color:var(--{sq_cls})">{sys_data["sql_syntax_errors"]}</div></div>\n')
            buf.write('</div>\n')
        elif is_md:
            buf.write("**关键异常统计:**\n\n")
            buf.write(f"| 异常类型 | 数量 |\n|----------|------|\n")
            buf.write(f"| ZooKeeper 错误 | {sys_data['zk_errors']} |\n")
            buf.write(f"| 事件处理超时 | {sys_data['event_timeout']} |\n")
            buf.write(f"| SQL语法错误 | {sys_data['sql_syntax_errors']} |\n\n")
        else:
            buf.write("  关键异常统计:\n")
            buf.write(f"    ZooKeeper 错误:   {sys_data['zk_errors']:>6}\n")
            buf.write(f"    事件处理超时:     {sys_data['event_timeout']:>6}\n")
            buf.write(f"    SQL语法错误:      {sys_data['sql_syntax_errors']:>6}\n\n")

        # 错误来源 Top
        if sys_data["error_types"]:
            if is_html:
                buf.write('<h3>错误来源 Top</h3>\n<table><tr><th>源文件:函数</th><th>次数</th></tr>\n')
            elif is_md:
                buf.write("**错误来源 Top:**\n\n| 源文件:函数 | 次数 |\n|------------|------|\n")
            else:
                buf.write("  错误来源 Top:\n")
            for src, cnt in sys_data["error_types"][:15]:
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

    def _write_flame_chart(self, buf, interf, fmt="terminal"):
        """生成 SQL 执行耗时火焰图（散点图：横轴时间，纵轴耗时，点击显示 SQL 详情）"""
        flame_data = interf.get("flame_data", [])
        if not flame_data:
            return

        is_html = fmt == "html"
        is_md = fmt == "md"

        if is_html:
            pfx = getattr(self, '_sec_prefix', '')
            uid = pfx.replace('-', '_').rstrip('_') or 'f0'  # JS-safe unique id
            self._html_section_start(buf, 'sec-flame', 'SQL 执行耗时火焰图')
            buf.write(f'<p>数据点: <strong>{len(flame_data):,}</strong> 个 '
                      f'<span style="color:#888;font-size:0.85em;">（拖拽框选放大时间段 | 滚轮缩放 | 双击重置 | '
                      f'点击散点查看 SQL 详情 | 放大后可横向滚动）</span></p>\n')

            # 时间范围选择器
            buf.write(f'<div id="{pfx}flameTimeRange" style="display:flex;align-items:center;gap:8px;'
                      'margin:10px 0;flex-wrap:wrap;font-size:0.85em;">\n')
            buf.write(f'<span style="color:#64748b;font-weight:600;">时间范围:</span>\n')
            buf.write(f'<input type="datetime-local" id="{pfx}flameStart" step="1" '
                      'style="padding:4px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:0.9em;'
                      'color:#334155;background:#fff;">\n')
            buf.write(f'<span style="color:#94a3b8;">~</span>\n')
            buf.write(f'<input type="datetime-local" id="{pfx}flameEnd" step="1" '
                      'style="padding:4px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:0.9em;'
                      'color:#334155;background:#fff;">\n')
            buf.write(f'<button id="{pfx}flameApplyRange" style="background:#0d6efd;color:#fff;border:none;'
                      'padding:5px 14px;border-radius:6px;font-size:0.85em;cursor:pointer;font-weight:600;"'
                      f' onclick="document.dispatchEvent(new CustomEvent(\'flameApplyRange_{uid}\'))">'
                      '应用</button>\n')
            buf.write(f'<button id="{pfx}flameQuick1h" style="background:#e2e8f0;color:#334155;border:none;'
                      'padding:5px 10px;border-radius:6px;font-size:0.82em;cursor:pointer;"'
                      f' onclick="document.dispatchEvent(new CustomEvent(\'flameQuick_{uid}\',{{detail:3600000}}))">1h</button>\n')
            buf.write(f'<button id="{pfx}flameQuick6h" style="background:#e2e8f0;color:#334155;border:none;'
                      'padding:5px 10px;border-radius:6px;font-size:0.82em;cursor:pointer;"'
                      f' onclick="document.dispatchEvent(new CustomEvent(\'flameQuick_{uid}\',{{detail:21600000}}))">6h</button>\n')
            buf.write(f'<button id="{pfx}flameQuick1d" style="background:#e2e8f0;color:#334155;border:none;'
                      'padding:5px 10px;border-radius:6px;font-size:0.82em;cursor:pointer;"'
                      f' onclick="document.dispatchEvent(new CustomEvent(\'flameQuick_{uid}\',{{detail:86400000}}))">1d</button>\n')
            buf.write('</div>\n')

            # 图表容器（外层可横向滚动）
            buf.write(f'<div id="{pfx}flameOuter" style="width:100%;background:var(--card);'
                      'border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.06);padding:20px;margin:10px 0;">\n')
            # 图例
            buf.write('<div style="display:flex;gap:16px;margin-bottom:12px;font-size:0.8em;color:#888;flex-wrap:wrap;">'
                      '<span>● <span style="color:#22c55e">■</span> &lt;1ms</span>'
                      '<span>● <span style="color:#3b82f6">■</span> 1-10ms</span>'
                      '<span>● <span style="color:#f59e0b">■</span> 10-100ms</span>'
                      '<span>● <span style="color:#ef4444">■</span> 100ms-1s</span>'
                      '<span>● <span style="color:#7c1d1d">■</span> &gt;1s</span></div>\n')
            # 缩放工具栏
            buf.write(f'<div id="{pfx}flameToolbar" style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">'
                      f'<button id="{pfx}flameReset" style="display:none;background:#0d6efd;color:#fff;border:none;'
                      'padding:4px 12px;border-radius:6px;font-size:0.78em;cursor:pointer;"'
                      f' onclick="document.dispatchEvent(new CustomEvent(\'flameReset_{uid}\'))">'
                      '↩ 重置缩放 Reset Zoom</button>'
                      f'<span id="{pfx}flameZoomInfo" style="display:none;font-size:0.78em;color:#64748b;"></span>'
                      '</div>\n')
            # 可滚动的画布区域
            buf.write(f'<div id="{pfx}flameScroll" style="width:100%;overflow-x:auto;position:relative;">\n')
            buf.write(f'<div id="{pfx}flameWrap" style="position:relative;min-width:100%;">\n')
            buf.write(f'<canvas id="{pfx}flameChart" style="width:100%;cursor:crosshair;"></canvas>\n')
            # 选区覆盖层
            buf.write(f'<canvas id="{pfx}flameOverlay" style="position:absolute;top:0;left:0;width:100%;height:100%;'
                      'pointer-events:none;"></canvas>\n')
            buf.write(f'<div id="{pfx}flameTip" style="display:none;position:absolute;background:rgba(30,30,30,0.92);'
                      'color:#fff;padding:8px 14px;border-radius:8px;font-size:0.82em;pointer-events:none;'
                      'z-index:10;box-shadow:0 4px 12px rgba(0,0,0,0.3);max-width:400px;'
                      'line-height:1.5;backdrop-filter:blur(4px);"></div>\n')
            buf.write('</div></div>\n')  # close flameWrap + flameScroll
            buf.write('</div>\n')  # close flameOuter

            # SQL 详情弹窗 (Modal) - 共享一个即可，用 JS 填充内容
            # 只在第一个火焰图输出 modal
            if not getattr(self, '_flame_modal_written', False):
                self._flame_modal_written = True
                buf.write('''<div id="sqlModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;
background:rgba(0,0,0,0.5);z-index:9999;backdrop-filter:blur(2px);"
onclick="if(event.target===this)this.style.display='none'">
<div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
background:#fff;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,0.3);
width:min(90vw,720px);max-height:80vh;overflow:hidden;">
<div style="display:flex;justify-content:space-between;align-items:center;padding:16px 24px;
border-bottom:1px solid #eee;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border-radius:16px 16px 0 0;">
<div><strong style="font-size:1.1em;">SQL 详情</strong>
<span id="sqlModalTime" style="margin-left:12px;opacity:0.85;font-size:0.9em;"></span></div>
<button onclick="document.getElementById('sqlModal').style.display='none'"
style="background:rgba(255,255,255,0.2);border:none;color:#fff;width:32px;height:32px;
border-radius:50%;cursor:pointer;font-size:16px;display:flex;align-items:center;
justify-content:center;">✕</button></div>
<div style="padding:20px 24px;overflow-y:auto;max-height:calc(80vh - 120px);">
<div style="display:flex;gap:16px;margin-bottom:16px;">
<div style="flex:1;background:#f0f9ff;border-radius:10px;padding:12px 16px;text-align:center;">
<div style="color:#64748b;font-size:0.8em;margin-bottom:4px;">耗时 Latency</div>
<div id="sqlModalCost" style="font-size:1.4em;font-weight:700;color:#1e40af;"></div></div>
<div style="flex:1;background:#fefce8;border-radius:10px;padding:12px 16px;text-align:center;">
<div style="color:#64748b;font-size:0.8em;margin-bottom:4px;">数据库 Database</div>
<div id="sqlModalDb" style="font-size:1.1em;font-weight:600;color:#854d0e;word-break:break-all;"></div></div></div>
<div style="background:#1e293b;border-radius:10px;padding:16px;position:relative;">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
<span style="color:#94a3b8;font-size:0.8em;">SQL Statement</span>
<button onclick="var t=document.getElementById('sqlModalText').textContent;navigator.clipboard.writeText(t)
.then(function(){this.textContent='\\u2713 Copied'}.bind(this))"
style="background:#334155;border:1px solid #475569;color:#e2e8f0;padding:4px 12px;border-radius:6px;
cursor:pointer;font-size:0.78em;">复制 Copy</button></div>
<pre id="sqlModalText" style="margin:0;color:#e2e8f0;font-family:'SF Mono',Consolas,'Courier New',monospace;
font-size:0.88em;line-height:1.6;white-space:pre-wrap;word-break:break-all;max-height:300px;overflow-y:auto;"></pre>
</div></div></div></div>\n''')

            # 嵌入数据 (4 个数组，使用唯一变量名)
            ts_list, tc_list, sql_list, db_list = [], [], [], []
            for row in flame_data:
                ts_list.append(row[0])
                tc_list.append(round(row[1], 2))
                sql_list.append(row[2] if len(row) > 2 else "")
                db_list.append(row[3] if len(row) > 3 else "")
            buf.write(f'<script>var _fTs_{uid}={json.dumps(ts_list, ensure_ascii=False)},'
                      f'_fTc_{uid}={json.dumps(tc_list)},'
                      f'_fSql_{uid}={json.dumps(sql_list, ensure_ascii=False)},'
                      f'_fDb_{uid}={json.dumps(db_list, ensure_ascii=False)};</script>\n')

            # 火焰图 JS (使用唯一 ID) — 支持拖拽框选/滚轮缩放/双击重置/横向滚动/时间范围选择
            buf.write(f'''<script>
(function(){{
  var cv=document.getElementById('{pfx}flameChart');if(!cv)return;
  var overlay=document.getElementById('{pfx}flameOverlay');
  var tip=document.getElementById('{pfx}flameTip'),
      modal=document.getElementById('sqlModal'),
      mTime=document.getElementById('sqlModalTime'),
      mCost=document.getElementById('sqlModalCost'),
      mDb=document.getElementById('sqlModalDb'),
      mSql=document.getElementById('sqlModalText'),
      resetBtn=document.getElementById('{pfx}flameReset'),
      zoomInfo=document.getElementById('{pfx}flameZoomInfo'),
      scrollBox=document.getElementById('{pfx}flameScroll'),
      wrapBox=document.getElementById('{pfx}flameWrap'),
      outerBox=document.getElementById('{pfx}flameOuter'),
      startInput=document.getElementById('{pfx}flameStart'),
      endInput=document.getElementById('{pfx}flameEnd');
  var dpr=window.devicePixelRatio||1;
  var ts=_fTs_{uid},tc=_fTc_{uid},sql=_fSql_{uid},db=_fDb_{uid},n=ts.length;
  if(!n)return;

  function parseT(s){{var p=s.split(/[- :]/);return new Date(+p[0],+p[1]-1,+p[2],+p[3]||0,+p[4]||0,+p[5]||0).getTime();}}

  var tV=new Float64Array(n),cV=new Float64Array(n);
  var tMinAll=Infinity,tMaxAll=-Infinity,cMax=0;
  for(var i=0;i<n;i++){{
    var t=parseT(ts[i]);tV[i]=t;cV[i]=tc[i];
    if(t<tMinAll)tMinAll=t;if(t>tMaxAll)tMaxAll=t;if(tc[i]>cMax)cMax=tc[i];
  }}
  if(tMaxAll===tMinAll)tMaxAll=tMinAll+1;if(cMax<1)cMax=1;
  var logMax=Math.log10(1+cMax);

  var vMin=tMinAll,vMax=tMaxAll;
  var zoomStack=[];

  // 初始化时间输入框
  function toLocalISO(t){{
    var d=new Date(t);
    return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2)+'T'+
           ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)+':'+('0'+d.getSeconds()).slice(-2);
  }}
  function syncInputs(){{
    if(startInput) startInput.value=toLocalISO(vMin);
    if(endInput) endInput.value=toLocalISO(vMax);
  }}
  syncInputs();

  function buildYTicks(maxVal){{
    var ticks=[0];
    var candidates=[0.1,0.2,0.5,1,2,5,10,20,50,100,200,500,1000,2000,5000,10000,30000,60000];
    for(var i=0;i<candidates.length;i++){{
      if(candidates[i]<=maxVal*1.3) ticks.push(candidates[i]);
    }}
    if(ticks[ticks.length-1]<maxVal) ticks.push(Math.ceil(maxVal));
    return ticks;
  }}
  var yTicks=buildYTicks(cMax);

  function dotColor(ms){{
    if(ms<1) return {{r:34,g:197,b:94,a:0.7}};
    if(ms<10) return {{r:59,g:130,b:246,a:0.65}};
    if(ms<100) return {{r:245,g:158,b:11,a:0.7}};
    if(ms<1000) return {{r:239,g:68,b:68,a:0.7}};
    return {{r:153,g:27,b:27,a:0.8}};
  }}
  function dotRadius(ms){{
    if(ms<1) return 1.5;
    var r=Math.log10(1+ms)/logMax;
    return Math.min(Math.max(r*5,2),8);
  }}
  function fmtMs(v){{
    if(v>=60000) return (v/60000).toFixed(1)+'min';
    if(v>=1000) return (v/1000).toFixed(1)+'s';
    if(v<0.1) return v.toFixed(3)+'ms';
    if(v<1) return v.toFixed(2)+'ms';
    return v.toFixed(1)+'ms';
  }}
  function fmtLabel(v){{
    if(v>=60000) return (v/60000).toFixed(0)+'min';
    if(v>=1000) return (v/1000).toFixed(v>=10000?0:1)+'s';
    return v+'ms';
  }}
  function fmtDate(t){{
    var d=new Date(t);
    return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2)+' '+
           ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2);
  }}

  var baseW,H=460,pad={{t:30,r:30,b:60,l:80}},pw,ph,W;

  function calcCanvasWidth(){{
    var containerW=outerBox.clientWidth-40;
    baseW=Math.max(containerW,300);
    var totalSpan=tMaxAll-tMinAll;
    var viewSpan=vMax-vMin;
    if(viewSpan<=0) viewSpan=1;
    var zoomRatio=totalSpan/viewSpan;
    if(zoomRatio>1.1){{
      W=Math.max(Math.round(baseW*zoomRatio),baseW);
    }}else{{
      W=baseW;
    }}
  }}

  function toY(v){{return pad.t+ph-(Math.log10(1+v)/logMax)*ph;}}
  function toX(t){{return pad.l+((t-vMin)/(vMax-vMin))*pw;}}
  function fromX(px){{return vMin+(px-pad.l)/pw*(vMax-vMin);}}

  function updateUI(){{
    var isZoomed=(vMin>tMinAll+1||vMax<tMaxAll-1);
    resetBtn.style.display=isZoomed?'inline-block':'none';
    if(isZoomed){{
      zoomInfo.style.display='inline';
      zoomInfo.textContent=fmtDate(vMin)+' ~ '+fmtDate(vMax);
    }} else {{
      zoomInfo.style.display='none';
    }}
    syncInputs();
  }}

  function draw(){{
    calcCanvasWidth();
    cv.width=W*dpr;cv.height=H*dpr;
    cv.style.width=W+'px';cv.style.height=H+'px';
    wrapBox.style.width=W+'px';
    if(overlay){{overlay.width=W*dpr;overlay.height=H*dpr;overlay.style.width=W+'px';overlay.style.height=H+'px';}}
    var ctx=cv.getContext('2d');ctx.scale(dpr,dpr);
    pw=W-pad.l-pad.r;ph=H-pad.t-pad.b;
    ctx.fillStyle='#fafbfc';ctx.fillRect(0,0,W,H);
    ctx.fillStyle='#fff';ctx.fillRect(pad.l,pad.t,pw,ph);
    ctx.textBaseline='middle';
    yTicks.forEach(function(v){{
      var y=toY(v);
      ctx.strokeStyle=v===0?'#ccc':'#f0f0f0';ctx.lineWidth=v===0?1:0.5;
      ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(pad.l+pw,y);ctx.stroke();
      ctx.fillStyle='#64748b';ctx.font='11px -apple-system,system-ui,sans-serif';ctx.textAlign='right';
      ctx.fillText(fmtLabel(v),pad.l-10,y);
    }});
    var xSteps=Math.max(Math.min(Math.ceil(pw/120),30),4);
    ctx.textBaseline='top';
    for(var i=0;i<=xSteps;i++){{
      var t=vMin+(vMax-vMin)*i/xSteps,x=toX(t),d=new Date(t);
      ctx.strokeStyle='#f0f0f0';ctx.lineWidth=0.5;
      ctx.beginPath();ctx.moveTo(x,pad.t);ctx.lineTo(x,pad.t+ph);ctx.stroke();
      ctx.fillStyle='#64748b';ctx.font='11px -apple-system,system-ui,sans-serif';ctx.textAlign='center';
      var lbl=(d.getMonth()+1)+'/'+d.getDate()+' '+('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2);
      ctx.fillText(lbl,x,pad.t+ph+10);
    }}
    ctx.strokeStyle='#e2e8f0';ctx.lineWidth=1;
    ctx.strokeRect(pad.l,pad.t,pw,ph);
    ctx.save();ctx.beginPath();ctx.rect(pad.l,pad.t,pw,ph);ctx.clip();
    var idx=new Array(n);for(var i=0;i<n;i++)idx[i]=i;
    idx.sort(function(a,b){{return cV[a]-cV[b];}});
    for(var j=0;j<n;j++){{
      var i=idx[j];
      if(tV[i]<vMin||tV[i]>vMax) continue;
      var x=toX(tV[i]),y=toY(cV[i]);
      var c=dotColor(cV[i]),rad=dotRadius(cV[i]);
      if(cV[i]>=100){{ctx.shadowColor='rgba(239,68,68,0.3)';ctx.shadowBlur=6;}}
      else{{ctx.shadowColor='transparent';ctx.shadowBlur=0;}}
      ctx.fillStyle='rgba('+c.r+','+c.g+','+c.b+','+c.a+')';
      ctx.beginPath();ctx.arc(x,y,rad,0,Math.PI*2);ctx.fill();
    }}
    ctx.restore();
    ctx.shadowColor='transparent';ctx.shadowBlur=0;
    ctx.fillStyle='#334155';ctx.font='bold 12px -apple-system,system-ui,sans-serif';
    ctx.textAlign='center';ctx.textBaseline='top';
    ctx.fillText('时间 Time',pad.l+pw/2,H-18);
    ctx.save();ctx.translate(18,pad.t+ph/2);ctx.rotate(-Math.PI/2);
    ctx.textBaseline='middle';ctx.textAlign='center';
    ctx.fillText('SQL 耗时 Latency (log)',0,0);ctx.restore();
    updateUI();
  }}

  function findNearest(mx,my){{
    var best=-1,bestD=Infinity;
    for(var i=0;i<n;i++){{
      if(tV[i]<vMin||tV[i]>vMax) continue;
      var x=toX(tV[i]),y=toY(cV[i]);
      var dx=x-mx,dy=y-my,d=dx*dx+dy*dy;
      if(d<bestD&&d<400){{bestD=d;best=i;}}
    }}
    return best;
  }}

  // --- 拖拽框选放大 ---
  var dragStart=null,isDragging=false;
  function clearOverlay(){{
    if(!overlay) return;
    var octx=overlay.getContext('2d');
    octx.clearRect(0,0,overlay.width,overlay.height);
  }}
  function drawSelection(x1,x2){{
    if(!overlay) return;
    var octx=overlay.getContext('2d');
    octx.setTransform(dpr,0,0,dpr,0,0);
    octx.clearRect(0,0,W,H);
    var left=Math.max(Math.min(x1,x2),pad.l),right=Math.min(Math.max(x1,x2),pad.l+pw);
    if(right-left<2) return;
    octx.fillStyle='rgba(0,0,0,0.15)';
    octx.fillRect(pad.l,pad.t,left-pad.l,ph);
    octx.fillRect(right,pad.t,pad.l+pw-right,ph);
    octx.strokeStyle='#0d6efd';octx.lineWidth=1.5;
    octx.setLineDash([4,3]);
    octx.strokeRect(left,pad.t,right-left,ph);
    octx.setLineDash([]);
    octx.fillStyle='rgba(13,110,253,0.9)';octx.font='bold 11px -apple-system,system-ui,sans-serif';
    octx.textBaseline='bottom';
    var t1=fromX(left),t2=fromX(right);
    octx.textAlign='left';octx.fillText(fmtDate(t1),left,pad.t-4);
    octx.textAlign='right';octx.fillText(fmtDate(t2),right,pad.t-4);
  }}

  cv.addEventListener('mousedown',function(e){{
    if(e.button!==0) return;
    var rect=cv.getBoundingClientRect();
    var mx=e.clientX-rect.left;
    if(mx>=pad.l&&mx<=pad.l+pw){{
      dragStart=mx;isDragging=false;
    }}
  }});
  cv.addEventListener('mousemove',function(e){{
    var rect=cv.getBoundingClientRect();
    var mx=e.clientX-rect.left,my=e.clientY-rect.top;
    if(dragStart!==null){{
      if(Math.abs(mx-dragStart)>5) isDragging=true;
      if(isDragging){{
        tip.style.display='none';
        cv.style.cursor='col-resize';
        drawSelection(dragStart,mx);
        return;
      }}
    }}
    var i=findNearest(mx,my);
    if(i>=0){{
      cv.style.cursor='pointer';
      var c=dotColor(cV[i]);
      tip.innerHTML='<div style="margin-bottom:3px;"><strong style="color:rgb('+c.r+','+c.g+','+c.b+');">'+fmtMs(cV[i])+'</strong>'+
        '<span style="margin-left:8px;opacity:0.8;">'+ts[i]+'</span></div>'+
        (db[i]?'<div style="opacity:0.7;font-size:0.9em;">DB: '+db[i].replace(/</g,'&lt;')+'</div>':'')+
        (sql[i]?'<div style="opacity:0.7;font-size:0.85em;max-width:350px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'+
        sql[i].replace(/</g,'&lt;').substring(0,80)+(sql[i].length>80?'...':'')+'</div>':'')+
        '<div style="margin-top:3px;opacity:0.5;font-size:0.78em;">点击查看完整 SQL / Click for details</div>';
      var tx=toX(tV[i])+12,ty=toY(cV[i])-10;
      if(tx+tip.offsetWidth>W-20) tx=toX(tV[i])-tip.offsetWidth-12;
      if(ty<10) ty=toY(cV[i])+15;
      tip.style.left=tx+'px';tip.style.top=ty+'px';
      tip.style.display='block';
    }} else {{
      cv.style.cursor='crosshair';
      tip.style.display='none';
    }}
  }});
  cv.addEventListener('mouseup',function(e){{
    if(dragStart!==null&&isDragging){{
      var rect=cv.getBoundingClientRect();
      var mx=e.clientX-rect.left;
      var x1=Math.max(Math.min(dragStart,mx),pad.l),x2=Math.min(Math.max(dragStart,mx),pad.l+pw);
      if(x2-x1>5){{
        var t1=fromX(x1),t2=fromX(x2);
        zoomStack.push({{min:vMin,max:vMax}});
        vMin=t1;vMax=t2;
        clearOverlay();draw();
      }}
    }}
    dragStart=null;isDragging=false;
    cv.style.cursor='crosshair';
    clearOverlay();
  }});
  cv.addEventListener('mouseleave',function(){{
    tip.style.display='none';cv.style.cursor='crosshair';
    if(isDragging) clearOverlay();
    dragStart=null;isDragging=false;
  }});

  // --- 点击查看 SQL ---
  cv.addEventListener('click',function(e){{
    if(isDragging) return;
    var rect=cv.getBoundingClientRect();
    var mx=e.clientX-rect.left,my=e.clientY-rect.top;
    var i=findNearest(mx,my);
    if(i<0) return;
    tip.style.display='none';
    mTime.textContent=ts[i];
    mCost.textContent=fmtMs(cV[i]);
    mDb.textContent=db[i]||'-';
    mSql.textContent=sql[i]||'(无 SQL 记录 / No SQL recorded)';
    modal.style.display='block';
  }});

  // --- 滚轮缩放 ---
  cv.addEventListener('wheel',function(e){{
    e.preventDefault();
    var rect=cv.getBoundingClientRect();
    var mx=e.clientX-rect.left;
    if(mx<pad.l||mx>pad.l+pw) return;
    var ratio=(mx-pad.l)/pw;
    var tCenter=vMin+ratio*(vMax-vMin);
    var span=vMax-vMin;
    var factor=e.deltaY>0?1.3:1/1.3;
    var newSpan=Math.min(span*factor,tMaxAll-tMinAll);
    if(newSpan<1000) newSpan=1000;
    var newMin=tCenter-ratio*newSpan;
    var newMax=tCenter+(1-ratio)*newSpan;
    if(newMin<tMinAll){{newMin=tMinAll;newMax=newMin+newSpan;}}
    if(newMax>tMaxAll){{newMax=tMaxAll;newMin=newMax-newSpan;}}
    if(newMin<tMinAll) newMin=tMinAll;
    zoomStack.push({{min:vMin,max:vMax}});
    vMin=newMin;vMax=newMax;
    draw();
  }},{{passive:false}});

  // --- 双击重置 ---
  cv.addEventListener('dblclick',function(e){{
    e.preventDefault();
    zoomStack=[];
    vMin=tMinAll;vMax=tMaxAll;
    draw();
  }});

  // --- 重置按钮 ---
  document.addEventListener('flameReset_{uid}',function(){{
    zoomStack=[];
    vMin=tMinAll;vMax=tMaxAll;
    draw();
  }});

  // --- 时间范围应用 ---
  document.addEventListener('flameApplyRange_{uid}',function(){{
    if(!startInput||!endInput) return;
    var sv=startInput.value,ev=endInput.value;
    if(!sv||!ev) return;
    var sT=new Date(sv).getTime(),eT=new Date(ev).getTime();
    if(isNaN(sT)||isNaN(eT)||sT>=eT) return;
    sT=Math.max(sT,tMinAll);eT=Math.min(eT,tMaxAll);
    if(sT>=eT) return;
    zoomStack.push({{min:vMin,max:vMax}});
    vMin=sT;vMax=eT;
    draw();
  }});

  // --- 快捷时间范围 ---
  document.addEventListener('flameQuick_{uid}',function(e){{
    var span=e.detail;
    var mid=(vMin+vMax)/2;
    var newMin=mid-span/2,newMax=mid+span/2;
    if(newMin<tMinAll){{newMin=tMinAll;newMax=newMin+span;}}
    if(newMax>tMaxAll){{newMax=tMaxAll;newMin=newMax-span;}}
    if(newMin<tMinAll) newMin=tMinAll;
    if(newMax<=newMin) return;
    zoomStack.push({{min:vMin,max:vMax}});
    vMin=newMin;vMax=newMax;
    draw();
  }});

  document.addEventListener('keydown',function(e){{
    if(e.key==='Escape'&&modal.style.display==='block') modal.style.display='none';
  }});

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',draw);
  else setTimeout(draw,50);
  window.addEventListener('resize',draw);
}})();
</script>\n''')
            self._html_section_end(buf)

        elif is_md:
            buf.write("## SQL 执行耗时火焰图\n\n")
            buf.write(f"数据点: **{len(flame_data):,}** 个（火焰图仅在 HTML 报告中显示）\n\n")

    def _write_conclusions(self, buf, data, fmt="terminal"):
        """自动生成结论与建议"""
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
            conn_count = interf.get("sql_type_counts", {}).get("11", 0)
            total = interf.get("total_lines", 1)
            if total > 0 and conn_count / total > 0.10:
                ratio = conn_count / total * 100
                findings.append((
                    f"短连接模式 - 连接建立占比 {ratio:.1f}%",
                    "中",
                    "改用连接池或长连接，减少连接开销",
                ))

        # 2. 高耗时SQL
        if interf.get("high_timecost"):
            top_tc = interf["high_timecost"][0]
            if top_tc[0] > 1000:
                findings.append((
                    f"存在超高耗时SQL: {top_tc[0]:.0f}ms ({top_tc[2]})",
                    "高",
                    f"优化 SQL 或添加索引: {top_tc[1][:60]}",
                ))
            elif top_tc[0] > 100:
                findings.append((
                    f"存在高耗时SQL: {top_tc[0]:.1f}ms",
                    "中",
                    "检查SQL执行计划，考虑添加索引",
                ))

        # 3. 错误码
        if interf:
            for rc, cnt in interf.get("resultcode_counts", {}).items():
                if rc != "0" and cnt > 0:
                    desc = RESULTCODE_MAP.get(rc, f"错误{rc}")
                    findings.append((
                        f"存在错误请求: code={rc}({desc}), 共{cnt}次",
                        "中" if cnt > 10 else "低",
                        f"排查错误原因，修复相关SQL",
                    ))

        # 4. 系统日志异常
        if sys_data:
            counts = list(sys_data.get("daily_counts", {}).values())
            if counts:
                avg_c = sum(counts) / len(counts)
                for date, cnt in sys_data.get("daily_counts", {}).items():
                    if cnt > avg_c * 3 and cnt > 100:
                        findings.append((
                            f"{date} 系统日志暴增至 {cnt} 行（平均 {avg_c:.0f} 行）",
                            "高",
                            "排查当日新上线的SQL/操作，检查事件超时",
                        ))
                        break
            if sys_data.get("zk_errors", 0) > 0:
                findings.append((
                    f"ZooKeeper 连接异常: {sys_data['zk_errors']} 次",
                    "中",
                    "检查 ZK 集群健康状态和网络连通性",
                ))
            if sys_data.get("event_timeout", 0) > 0:
                findings.append((
                    f"事件处理超时: {sys_data['event_timeout']} 次",
                    "中",
                    "检查 Proxy 负载和后端 DB 响应时间",
                ))

        # 5. 慢SQL
        if slow_data and slow_data.get("total_count", 0) > 0:
            findings.append((
                f"慢SQL日志共 {slow_data['total_count']} 条",
                "中" if slow_data["total_count"] > 10 else "低",
                "优化慢SQL，添加索引或改写查询",
            ))

        # 6. 平均耗时趋势
        if interf:
            daily = interf.get("daily_stats", {})
            avgs = {}
            for date, stats in daily.items():
                if stats["timecost_count"] > 0:
                    avgs[date] = stats["timecost_sum"] / stats["timecost_count"]
            if len(avgs) >= 3:
                vals = list(avgs.values())
                overall_avg = sum(vals) / len(vals)
                for date in sorted(avgs.keys()):
                    if avgs[date] > overall_avg * 1.3:
                        findings.append((
                            f"{date} 平均耗时 {avgs[date]:.3f}ms（整体均值 {overall_avg:.3f}ms，偏高 {((avgs[date]/overall_avg)-1)*100:.0f}%）",
                            "中",
                            "关联系统日志检查是否有异常事件",
                        ))

        # 7. 请求集中度
        if interf:
            hourly = interf.get("hourly_counts", {})
            if hourly:
                vals = list(hourly.values())
                max_h = max(vals)
                min_h = min(vals) if min(vals) > 0 else 1
                if max_h / min_h > 2:
                    max_hour = max(hourly, key=hourly.get)
                    findings.append((
                        f"请求量峰谷比 {max_h/min_h:.1f}x，高峰: {max_hour} ({max_h:,})",
                        "低",
                        "如有定时任务，考虑分散到低峰时段",
                    ))

        # 8. 无问题
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

    # ---------- 报告底部 ----------

    def _write_footer(self, buf, fmt="terminal"):
        """在报告底部输出工具信息"""
        gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if fmt == "html":
            buf.write('<footer class="report-footer">\n')
            buf.write(f'  <p>Generated by <strong>TDSQL Gateway Log Analyzer</strong> v{VERSION}</p>\n')
            buf.write(f'  <p>{_h(gen_time)}</p>\n')
            buf.write('</footer>\n')
        elif fmt == "md":
            buf.write("\n---\n\n")
            buf.write(f"*Generated by TDSQL Gateway Log Analyzer v{VERSION} | {gen_time}*\n")
        else:
            buf.write(f"\n{'═'*80}\n")
            buf.write(f"  Generated by TDSQL Gateway Log Analyzer v{VERSION} | {gen_time}\n")
            buf.write(f"{'═'*80}\n")

    # ---------- 工具方法 ----------

    @staticmethod
    def _fmt_size(size_bytes):
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if abs(size_bytes) < 1024:
                return f"{size_bytes:.1f}{unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f}PB"


# ============================================================
# 端口号解析
# ============================================================


def parse_port_expr(expr):
    """解析端口号表达式，支持以下格式:
    - 单个端口: 15001
    - 逗号分隔: 15001,15003,15005
    - 连续范围（冒号）: 15001:15010 （包含首尾，即 15001~15010）
    - 连续范围（波浪号）: 15015~15020 （包含首尾）
    - 混合: 15001:15010,15012,15015~15020
    返回排序后的端口号列表（int），出错时 raise ValueError
    """
    ports = set()
    # 先按逗号分割
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        # 判断是否为范围表达式
        sep = None
        if ":" in part:
            sep = ":"
        elif "~" in part:
            sep = "~"

        if sep:
            pieces = part.split(sep, 1)
            if len(pieces) != 2:
                raise ValueError(f"无效的范围表达式: {part}")
            try:
                start = int(pieces[0].strip())
                end = int(pieces[1].strip())
            except ValueError:
                raise ValueError(f"端口号必须为数字: {part}")
            if start > end:
                raise ValueError(f"范围起始不能大于结束: {part}")
            if end - start > 1000:
                raise ValueError(f"范围过大（最多 1000 个端口）: {part}")
            for p in range(start, end + 1):
                ports.add(p)
        else:
            try:
                ports.add(int(part))
            except ValueError:
                raise ValueError(f"端口号必须为数字: {part}")
    return sorted(ports)


def expand_ports_to_dirs(ports, template):
    """将端口号列表展开为日志目录路径列表"""
    dirs = []
    for port in ports:
        d = template.replace("{port}", str(port))
        dirs.append(d)
    return dirs


# ============================================================
# 主程序
# ============================================================


def main():
    print_banner()

    parser = argparse.ArgumentParser(
        description="TDSQL Gateway 日志综合分析脚本 / TDSQL Gateway Log Comprehensive Analysis Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  参数说明 / Arguments:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  -d DIR [DIR ...]        日志目录（可多个）
                          Log directory path(s)
                          例: -d /data/tdsql_run/15001/gateway/log

  -p PORTS                端口号表达式（与 -d 二选一或组合使用）
                          Port expression, auto-expand to log directory paths
                          支持格式:
                            单个端口:   -p 15001
                            逗号分隔:   -p 15001,15003,15005
                            连续范围:   -p 15001:15010  或  -p 15001~15010
                            混合格式:   -p 15001:15010,15012,15015~15020
                          展开路径模板（默认）:
                            /data/tdsql_run/{port}/gateway/log
                          可通过 --base-path 自定义模板

  --base-path TEMPLATE    端口号展开路径模板（配合 -p 使用）
                          Path template for port expansion (used with -p)
                          默认: /data/tdsql_run/{port}/gateway/log
                          例: --base-path /data1/tdengine/data/{port}/gateway/log

  --dates DATE ...        按日期过滤日志（可多个），支持两种精度:
                          Filter logs by date/hour, multiple values allowed
                          YYYY-MM-DD    天级过滤（自动包含前一天文件覆盖跨日日志）
                          YYYY-MM-DDTHH 小时级过滤（按行内时间戳精确到小时）
                          例: --dates 2026-02-20
                              --dates 2026-02-20T14 2026-02-20T15

  -f FORMAT               输出格式（仅在扩展名无法识别时生效）
                          Output format override
                          terminal  终端彩色表格（默认）
                          markdown  Markdown 文本
                          html      独立网页（含火焰图/目录导航）

  -o FILE [FILE ...]      输出到文件，可同时指定多个，按扩展名自动识别格式:
                          Output to file(s), format auto-detected by extension:
                            .html/.htm  → HTML 报告
                            .md         → Markdown 报告
                            .txt        → 终端文本报告
                            .json       → 导出分析数据（JSON 紧凑格式，支持后续整合分析）
                            .json.gz    → 导出分析数据（gzip 压缩，级别 6，支持后续整合分析）
                          文件名支持占位符，自动替换:
                            {date}  → 分析日期（--dates 日期范围或当前日期时间）
                            {host}  → 当前主机名（多服务器场景避免文件名冲突）
                          不指定则打印到终端 / default: stdout
                          例: -o report_{date}.html data_{host}_{date}.json.gz

  -n N                    Top N 排行数量（默认: 20）
                          Number of top entries (default: 20)

  --sample LINES          每文件采样行数上限（默认: 0=全量）
                          Max lines per file, 0=full (default: 0)

  --log-types TYPE ...    只分析指定类型的日志（可多个），默认分析所有类型
                          Only analyze specified log types (default: all)
                          可选值: interf, sql, slow_sql, sys
                          例: --log-types interf
                              --log-types interf sql

  --files FILE ...        指定具体日志文件路径进行分析（可多个）
                          Analyze specific log files directly
                          可与 --log-types 组合使用进行二次过滤
                          例: --files /path/to/interf_instance_15001.2026-04-01.0
                              --files interf_instance_15001 interf_instance_15002

  -v                      显示版本号 / Show version
  -h                      显示帮助信息 / Show this help

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  使用示例 / Examples:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  python3 %(prog)s -d /data/tdsql_run/15001/gateway/log
  python3 %(prog)s -d /data/tdsql_run/15001/gateway/log /data/tdsql_run/15003/gateway/log
  python3 %(prog)s -p 15001
  python3 %(prog)s -p 15001:15020
  python3 %(prog)s -p 15001:15010,15012,15015~15020
  python3 %(prog)s -p 15001:15020 --base-path /data1/tdengine/data/{port}/gateway/log
  python3 %(prog)s -p 15001:15020 -o report_{date}.html
  python3 %(prog)s -d /data/tdsql_run/15001/gateway/log --dates 2026-02-20 2026-02-21
  python3 %(prog)s -d /data/tdsql_run/15001/gateway/log --dates 2026-02-20T14 2026-02-20T15
  python3 %(prog)s -d /data/tdsql_run/15001/gateway/log -o report.html
  python3 %(prog)s -d /data/tdsql_run/15001/gateway/log -o report.md
  python3 %(prog)s -d /data/tdsql_run/15001/gateway/log --sample 100000 -n 30
  python3 %(prog)s -d /data/tdsql_run/15001/gateway/log -o report_{date}.html data_{date}.json.gz
  python3 %(prog)s -d /data/tdsql_run/15001/gateway/log --dates 2026-02-20 -o report_{date}.html
  python3 %(prog)s -p 15001:15020 --dates 2026-03-01 -o data_{host}_{date}.json.gz
  python3 %(prog)s -p 15001:15020 --log-types interf -o report.html
  python3 %(prog)s -p 15001:15020 --log-types interf sql --dates 2026-03-01
  python3 %(prog)s --files /data/tdsql_run/15001/gateway/log/interf_instance_15001.2026-04-01.0 -o report.html
  python3 %(prog)s --files interf_instance_15001 interf_instance_15002 -o report.html

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  日志类型 / Log Types:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  interf_instance     SQL 接口层日志 (主分析)
  sql_instance        SQL 执行层日志
  slow_sql_instance   慢 SQL 日志
  sys_instance        系统/错误日志
  route_instance      路由日志
  dbfw_instance       防火墙日志

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  报告章节 / Report Sections (15):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   1. 日志概览              6. 高耗时 SQL Top N     11. 连接模式分析
   2. 每日请求量趋势        7. 高频 SQL 模式        12. 慢SQL日志分析
   3. 每小时请求量分布      8. SQL 类型分布         13. 系统日志异常检测
   4. SQL 耗时分布          9. 用户 & 数据库分布    14. SQL 执行耗时火焰图 (HTML)
   5. 每日平均耗时趋势     10. 错误码分析           15. 核心结论与建议

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  资源保护（可安全在生产服务器运行）:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  内存    最多占用 1GB，超出自动终止，不会吃光服务器内存
  CPU     优先级降低 10 级，让业务进程优先使用 CPU
          每处理 5 万行日志暂停 10ms，避免长时间独占 CPU
  火焰图  数据点超过 5 万自动降采样，控制 HTML 大小和渲染性能
  依赖    仅需 Python >= 3.6 标准库，无需 pip install，拷贝即用
        """,
    )
    parser.add_argument(
        "-d",
        "--dirs",
        nargs="+",
        metavar="DIR",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-p",
        "--ports",
        metavar="PORTS",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--base-path",
        metavar="TEMPLATE",
        default=DEFAULT_LOG_PATH_TEMPLATE,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--dates",
        nargs="+",
        metavar="DATE",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["terminal", "markdown", "html"],
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-o",
        "--output",
        nargs="+",
        metavar="FILE",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-n",
        "--top-n",
        type=int,
        default=20,
        metavar="N",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=0,
        metavar="LINES",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument(
        "--log-types",
        nargs="+",
        choices=["interf", "sql", "slow_sql", "sys"],
        default=None,
        help="指定要分析的日志类型，默认分析所有类型"
    )
    parser.add_argument(
        "--files",
        nargs="+",
        metavar="FILE",
        default=None,
        help="指定要分析的具体日志文件路径，可指定多个"
    )

    args = parser.parse_args()

    # ── 合并 -d 和 -p 参数 ────────────────────────────────────
    all_dirs = list(args.dirs or [])

    if args.ports:
        if "{port}" not in args.base_path:
            parser.error("--base-path 模板必须包含 {port} 占位符")
        try:
            port_list = parse_port_expr(args.ports)
        except ValueError as e:
            parser.error(f"端口号表达式错误: {e}")
        port_dirs = expand_ports_to_dirs(port_list, args.base_path)
        # 检查目录是否存在，给出警告
        valid_port_dirs = []
        for d in port_dirs:
            if os.path.isdir(d):
                valid_port_dirs.append(d)
            else:
                print(f"  {c(YELLOW, '[警告]')} 目录不存在，跳过: {d}", file=sys.stderr)
        if not valid_port_dirs:
            parser.error("所有端口对应的日志目录均不存在")
        all_dirs.extend(valid_port_dirs)
        # 输出展开结果
        print(f"  {c(DIM, '端口展开:')} {len(port_list)} 个端口 → {len(valid_port_dirs)} 个有效目录", file=sys.stderr)

    # 不带参数时输出帮助信息
    if not all_dirs and not args.files:
        parser.print_help(sys.stderr)
        sys.exit(0)
    
    # --files 模式下验证文件存在性
    if args.files:
        valid_files = []
        for f in args.files:
            abs_f = os.path.abspath(f)
            if os.path.isfile(abs_f):
                valid_files.append(abs_f)
            else:
                print(f"  {c(YELLOW, '[警告]')} 文件不存在，跳过: {f}", file=sys.stderr)
        if not valid_files:
            parser.error("所有指定的文件均不存在")
        args.files = valid_files
        file_types = set()
        for f in valid_files:
            fname = os.path.basename(f)
            m = re.match(r"(interf|sql|slow_sql|sys|route|dbfw|retry|update)_instance_", fname)
            if m:
                file_types.add(m.group(1))
        print(f"  {c(DIM, '指定文件:')} {len(valid_files)} 个文件, 类型: {', '.join(sorted(file_types))}", file=sys.stderr)

    # 获取进程锁（单实例保护）
    _acquire_lock()
    atexit.register(_release_lock)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    # 应用资源限制
    _apply_resource_limits()

    # 解析日期过滤
    date_filter = None
    if args.dates:
        for d in args.dates:
            if not re.match(r"^\d{4}-\d{2}-\d{2}(T\d{2})?$", d):
                parser.error(f"日期格式错误: {d}，请使用 YYYY-MM-DD 或 YYYY-MM-DDTHH 格式")
        date_filter = set(args.dates)
        has_hour = any(len(d) > 10 for d in date_filter)
        hint = "小时级过滤" if has_hour else "自动包含前一天文件以覆盖跨日日志"
        print(f"  {c(DIM, '日期过滤:')} {', '.join(sorted(date_filter))}"
              f" {c(DIM, '(' + hint + ')')}", file=sys.stderr)

    analyzer = GatewayLogAnalyzer(
        log_dirs=all_dirs, top_n=args.top_n, sample_limit=args.sample,
        date_filter=date_filter, log_types=args.log_types,
        specific_files=args.files
    )
    analyzer.analyze_all()

    # ── 输出处理 ──────────────────────────────────────────────

    # 构造 {date} 占位符的替换值
    if date_filter:
        sorted_dates = sorted(date_filter)
        if len(sorted_dates) == 1:
            date_tag = sorted_dates[0].replace("T", "_")
        else:
            date_tag = sorted_dates[0].replace("T", "_") + "~" + sorted_dates[-1].replace("T", "_")
    else:
        date_tag = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    # 构造 {host} 占位符的替换值（主机名，用于多服务器场景避免文件名冲突）
    import socket as _socket
    host_tag = _socket.gethostname()

    # 扩展名 → 格式映射
    _EXT_FMT = {
        ".html": "html", ".htm": "html",
        ".md": "markdown",
        ".txt": "terminal",
        ".json": "json", ".json.gz": "json.gz",
    }

    def _detect_fmt(path):
        """根据文件扩展名推断输出格式"""
        low = path.lower()
        if low.endswith(".json.gz"):
            return "json.gz"
        for ext, fmt in _EXT_FMT.items():
            if low.endswith(ext):
                return fmt
        return None

    def _fmt_size(size):
        return f"{size/1024/1024:.1f}MB" if size > 1024*1024 else f"{size/1024:.0f}KB"

    outputs = args.output or []
    # 替换文件名中的 {date} 和 {host} 占位符
    outputs = [p.replace("{date}", date_tag).replace("{host}", host_tag) for p in outputs]
    has_report_output = False

    for out_path in outputs:
        fmt = _detect_fmt(out_path)
        if fmt is None:
            # 无法识别扩展名，使用 -f 指定的格式或默认 terminal
            fmt = args.format or "terminal"
        if fmt in ("json", "json.gz"):
            # 数据导出
            compress = fmt == "json.gz"
            name = os.path.basename(out_path)
            # 去掉扩展名
            if name.endswith(".json.gz"):
                name = name[:-8]
            elif name.endswith(".json"):
                name = name[:-5]
            export_dir = os.path.dirname(out_path) or "."
            export_path = analyzer.export_data(name, export_dir, compress=compress)
            fsize = os.path.getsize(export_path)
            print(f"\n  {c(GREEN, '[导出]')} 分析数据已保存到: {export_path} ({_fmt_size(fsize)})", file=sys.stderr)
        else:
            # 报告输出
            has_report_output = True
            report = analyzer.generate_report(fmt=fmt)
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"\n  {c(GREEN, '[保存]')} 报告已保存到: {out_path}", file=sys.stderr)

    if not outputs:
        # 无 -o 参数，输出到终端
        fmt = args.format or "terminal"
        report = analyzer.generate_report(fmt=fmt)
        print(report)


if __name__ == "__main__":
    main()
