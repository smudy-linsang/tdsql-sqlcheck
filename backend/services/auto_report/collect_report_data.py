#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TDSQL 运维汇报数据采集器
从各子项目的 output 目录中采集数据，汇总为统一 JSON 格式供 PPT 生成脚本使用。

用法:
    python3 collect_report_data.py [选项]

选项:
    --inspection-csv <path>     每日巡检 CSV 文件路径
    --count-rows-dir <path>     count_table_rows output 目录
    --index-dir <path>          index_analysis output 目录
    --sql-dir <path>            sql_analysis output 目录
    --gateway-dir <path>        gateway_log_analysis output 目录 (含 .json/.json.gz)
    --schema-diff-html <path>   table_schema_diff HTML 报告路径
    --schema-diff-prod <path>   table_schema_diff 生产环境导出目录
    --schema-diff-test <path>   table_schema_diff 测试环境导出目录
    -o, --output <path>         输出 JSON 路径 (默认: output/report_data.json)
    --report-title <title>      报告标题
    --report-date <date>        报告日期 (YYYY-MM-DD)
"""

import argparse
import csv
import glob
import gzip
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime


def parse_percent(s):
    """解析百分比字符串为浮点数"""
    if not s:
        return 0.0
    s = str(s).strip().replace('%', '')
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def parse_int(s):
    """安全解析整数"""
    if not s:
        return 0
    s = str(s).strip().replace(',', '')
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


# ── 每日巡检数据采集 ──────────────────────────────────────────────────────────

def collect_inspection_data(csv_path):
    """从每日巡检 CSV 中提取关键数据"""
    if not csv_path or not os.path.exists(csv_path):
        return None

    result = {
        'source_file': os.path.basename(csv_path),
        'instances': [],
        'servers': [],
        'summary': {}
    }

    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        content = f.read()

    # 拆分 Sheet1 和 Sheet2
    parts = content.split('[Sheet2]')
    sheet1_text = parts[0].strip()
    sheet2_text = parts[1].strip() if len(parts) > 1 else ''

    # 解析 Sheet1 — 实例巡检
    lines = sheet1_text.strip().split('\n')
    if len(lines) < 2:
        return result

    reader = csv.DictReader(lines)
    instances = []
    total_slow_queries = 0
    cpu_alerts = []
    disk_alerts = []
    delay_alerts = []

    for row in reader:
        inst = {
            'name': row.get('实例名称', ''),
            'instance_id': row.get('实例ID', ''),
            'cpu_cores': parse_int(row.get('CPU核数', 0)),
            'memory_gb': parse_int(row.get('内存GB', 0)),
            'data_disk_gb': parse_int(row.get('数据盘GB', 0)),
            'data_disk_usage': parse_percent(row.get('数据盘使用率', '0%')),
            'cpu_peak': parse_percent(row.get('CPU峰值', '0%')),
            'avg_cpu_peak': parse_percent(row.get('平均CPU峰值', '0%')),
            'avg_cpu': parse_percent(row.get('全天平均CPU', '0%')),
            'avg_memory': parse_percent(row.get('全天平均内存', '0%')),
            'slow_queries': parse_int(row.get('慢查询总数', 0)),
            'repl_delay': parse_int(row.get('主备延迟秒', 0)),
            'total_requests': parse_int(row.get('上一日全天总请求量', 0)),
            'select_peak': parse_int(row.get('汇总SELECT请求量峰值', 0)),
            'active_conn_peak': parse_int(row.get('汇总活跃连接数峰值', 0)),
            'l_pct': parse_percent(row.get('上一日全天L_<5ms百分比', '0%')),
        }
        instances.append(inst)
        total_slow_queries += inst['slow_queries']

        # 检测异常
        if inst['cpu_peak'] > 80:
            cpu_alerts.append({'name': inst['name'], 'value': inst['cpu_peak']})
        if inst['data_disk_usage'] > 80:
            disk_alerts.append({'name': inst['name'], 'value': inst['data_disk_usage']})
        if inst['repl_delay'] > 5:
            delay_alerts.append({'name': inst['name'], 'value': inst['repl_delay']})

    # 计算汇总
    instance_count = len(instances)
    avg_cpu_all = sum(i['avg_cpu'] for i in instances) / instance_count if instance_count else 0
    avg_mem_all = sum(i['avg_memory'] for i in instances) / instance_count if instance_count else 0
    total_requests_all = sum(i['total_requests'] for i in instances)

    # 按慢查询排序取 TOP 10
    top_slow = sorted(instances, key=lambda x: x['slow_queries'], reverse=True)[:10]
    # 按请求量排序取 TOP 10
    top_requests = sorted(instances, key=lambda x: x['total_requests'], reverse=True)[:10]
    # 按 CPU 峰值排序
    top_cpu = sorted(instances, key=lambda x: x['cpu_peak'], reverse=True)[:10]

    result['instances'] = instances
    result['summary'] = {
        'instance_count': instance_count,
        'avg_cpu': round(avg_cpu_all, 2),
        'avg_memory': round(avg_mem_all, 2),
        'total_slow_queries': total_slow_queries,
        'total_requests': total_requests_all,
        'cpu_alerts': cpu_alerts,
        'disk_alerts': disk_alerts,
        'delay_alerts': delay_alerts,
        'alert_count': len(cpu_alerts) + len(disk_alerts) + len(delay_alerts),
    }
    result['top_slow_queries'] = [{'name': i['name'], 'count': i['slow_queries']} for i in top_slow]
    result['top_requests'] = [{'name': i['name'], 'count': i['total_requests']} for i in top_requests]
    result['top_cpu'] = [{'name': i['name'], 'value': i['cpu_peak']} for i in top_cpu]

    # 解析 Sheet2 — 服务器巡检
    if sheet2_text:
        srv_lines = sheet2_text.strip().split('\n')
        srv_reader = csv.DictReader(srv_lines)
        for row in srv_reader:
            result['servers'].append({
                'ip': row.get('服务器IP', ''),
                'cpu_peak': parse_percent(row.get('CPU全天峰值', '0%')),
                'cpu_avg': parse_percent(row.get('CPU全天平均值', '0%')),
                'disk_max': parse_percent(row.get('最大磁盘利用率', '0%')),
            })

    return result


# ── 表数据量统计采集 ──────────────────────────────────────────────────────────

def collect_count_rows_data(output_dir):
    """从 count_table_rows 的 CSV 输出中提取统计数据"""
    if not output_dir or not os.path.exists(output_dir):
        return None

    # 查找最新的 summary CSV
    csv_files = sorted(glob.glob(os.path.join(output_dir, 'summary_*.csv')), reverse=True)
    if not csv_files:
        return None

    result = {
        'source_file': os.path.basename(csv_files[0]),
        'snapshot_count': len(csv_files),
        'tables': [],
        'summary': {}
    }

    # 解析最新的 CSV
    tables = []
    with open(csv_files[0], 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rc = row.get('row_count', '0')
            if rc in ('TIMEOUT', 'ERROR', ''):
                continue
            tables.append({
                'service': row.get('service_name', ''),
                'database': row.get('database', ''),
                'table': row.get('table_name', ''),
                'row_count': parse_int(rc),
                'table_type': row.get('table_type', ''),
            })

    # 排序取 TOP
    tables.sort(key=lambda x: x['row_count'], reverse=True)
    total_tables = len(tables)
    total_rows = sum(t['row_count'] for t in tables)

    # 数据量分布
    dist = {'亿级': 0, '千万级': 0, '百万级': 0, '十万级': 0, '万级以下': 0}
    for t in tables:
        rc = t['row_count']
        if rc >= 100000000:
            dist['亿级'] += 1
        elif rc >= 10000000:
            dist['千万级'] += 1
        elif rc >= 1000000:
            dist['百万级'] += 1
        elif rc >= 100000:
            dist['十万级'] += 1
        else:
            dist['万级以下'] += 1

    # 按服务维度汇总
    svc_summary = defaultdict(lambda: {'tables': 0, 'total_rows': 0})
    for t in tables:
        svc = t['service'] or t['database']
        svc_summary[svc]['tables'] += 1
        svc_summary[svc]['total_rows'] += t['row_count']

    result['tables_top20'] = tables[:20]
    result['summary'] = {
        'total_tables': total_tables,
        'total_rows': total_rows,
        'distribution': dist,
        'snapshot_count': len(csv_files),
    }
    result['service_summary'] = [
        {'name': k, 'tables': v['tables'], 'total_rows': v['total_rows']}
        for k, v in sorted(svc_summary.items(), key=lambda x: x[1]['total_rows'], reverse=True)
    ]

    return result


# ── 索引分析数据采集 ──────────────────────────────────────────────────────────

def collect_index_data(output_dir):
    """从 index_analysis 的 CSV 输出中提取统计数据"""
    if not output_dir or not os.path.exists(output_dir):
        return None

    # 查找最新的索引统计 CSV
    index_files = sorted(glob.glob(os.path.join(output_dir, 'index_stats_*.csv')), reverse=True)
    usage_files = sorted(glob.glob(os.path.join(output_dir, 'index_usage_*.csv')), reverse=True)
    table_files = sorted(glob.glob(os.path.join(output_dir, 'table_stats_*.csv')), reverse=True)

    if not index_files:
        return None

    result = {
        'source_file': os.path.basename(index_files[0]),
        'summary': {},
        'duplicate_indexes': [],
        'unused_indexes': [],
        'fragmentation_top': [],
    }

    # 解析索引元数据
    indexes = defaultdict(list)  # (schema, table, index_name) -> columns
    with open(index_files[0], 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row.get('table_schema', ''), row.get('table_name', ''),
                   row.get('index_name', ''))
            indexes[key].append(row)

    total_indexes = len(indexes)
    # 统计索引类型
    pk_count = sum(1 for k in indexes if k[2] == 'PRIMARY')
    unique_count = sum(1 for k, cols in indexes.items()
                       if k[2] != 'PRIMARY' and cols and cols[0].get('non_unique', '1') == '0')
    normal_count = total_indexes - pk_count - unique_count

    # 检测重复索引（简化版：相同表上列前缀相同的索引）
    table_indexes = defaultdict(list)
    for (schema, table, idx_name), cols in indexes.items():
        col_list = ','.join(c.get('column_name', '') for c in sorted(cols, key=lambda x: parse_int(x.get('seq_in_index', 0))))
        table_indexes[(schema, table)].append({'name': idx_name, 'columns': col_list})

    duplicate_count = 0
    prefix_redundant_count = 0
    for (schema, table), idx_list in table_indexes.items():
        seen = {}
        for idx in idx_list:
            if idx['columns'] in seen:
                duplicate_count += 1
                result['duplicate_indexes'].append({
                    'schema': schema, 'table': table,
                    'index1': seen[idx['columns']], 'index2': idx['name'],
                    'columns': idx['columns'],
                })
            else:
                seen[idx['columns']] = idx['name']
            # 检测前缀冗余
            for other in idx_list:
                if other['name'] != idx['name'] and other['columns'].startswith(idx['columns'] + ','):
                    prefix_redundant_count += 1

    # 解析索引使用情况
    unused_indexes = []
    if usage_files:
        with open(usage_files[0], 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('index_name', '') == 'PRIMARY':
                    continue
                count_read = parse_int(row.get('count_read', 0))
                if count_read == 0:
                    unused_indexes.append({
                        'schema': row.get('object_schema', ''),
                        'table': row.get('object_name', ''),
                        'index': row.get('index_name', ''),
                    })

    # 解析表碎片
    fragmentation = []
    if table_files:
        with open(table_files[0], 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                data_free = parse_int(row.get('data_free', 0))
                data_length = parse_int(row.get('data_length', 0))
                if data_free > 1048576 and data_length > 0:  # > 1MB
                    frag_rate = round(data_free / (data_length + data_free) * 100, 1)
                    fragmentation.append({
                        'schema': row.get('table_schema', ''),
                        'table': row.get('table_name', ''),
                        'data_free_mb': round(data_free / 1048576, 1),
                        'frag_rate': frag_rate,
                    })
        fragmentation.sort(key=lambda x: x['data_free_mb'], reverse=True)

    unique_tables = len(table_indexes)
    result['summary'] = {
        'total_indexes': total_indexes,
        'pk_count': pk_count,
        'unique_count': unique_count,
        'normal_count': normal_count,
        'duplicate_count': duplicate_count,
        'prefix_redundant_count': prefix_redundant_count,
        'unused_count': len(unused_indexes),
        'unique_tables': unique_tables,
        'fragmented_tables': len(fragmentation),
    }
    result['unused_indexes'] = unused_indexes[:20]
    result['fragmentation_top'] = fragmentation[:20]

    return result


# ── SQL 分析数据采集 ──────────────────────────────────────────────────────────

def collect_sql_data(output_dir):
    """从 sql_analysis 的 CSV 中提取统计数据"""
    if not output_dir or not os.path.exists(output_dir):
        return None

    digest_files = sorted(glob.glob(os.path.join(output_dir, 'digest_*.csv')), reverse=True)
    slowlog_files = sorted(glob.glob(os.path.join(output_dir, 'slowlog_*.csv')), reverse=True)

    if not digest_files and not slowlog_files:
        return None

    result = {
        'source_file': '',
        'summary': {},
        'top_frequent_sql': [],
        'top_slow_sql': [],
        'full_scan_sql': [],
        'type_distribution': {},
    }

    # 解析 digest 数据
    if digest_files:
        result['source_file'] = os.path.basename(digest_files[0])
        sql_records = []
        type_counts = defaultdict(int)

        with open(digest_files[0], 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rec = {
                    'service': row.get('service_name', ''),
                    'schema': row.get('schema_name', ''),
                    'digest_text': (row.get('digest_text', '') or '')[:200],
                    'count': parse_int(row.get('count_star', 0)),
                    'total_time': float(row.get('total_time_sec', 0) or 0),
                    'avg_time': float(row.get('avg_time_sec', 0) or 0),
                    'max_time': float(row.get('max_time_sec', 0) or 0),
                    'rows_examined': parse_int(row.get('rows_examined', 0)),
                    'no_index': parse_int(row.get('no_index_used', 0)),
                }
                sql_records.append(rec)

                # SQL 类型分布
                sql_text = (rec['digest_text'] or '').strip().upper()
                if sql_text.startswith('SELECT'):
                    type_counts['SELECT'] += rec['count']
                elif sql_text.startswith('INSERT'):
                    type_counts['INSERT'] += rec['count']
                elif sql_text.startswith('UPDATE'):
                    type_counts['UPDATE'] += rec['count']
                elif sql_text.startswith('DELETE'):
                    type_counts['DELETE'] += rec['count']
                elif sql_text.startswith('COMMIT') or sql_text.startswith('BEGIN') or sql_text.startswith('ROLLBACK'):
                    type_counts['TRANSACTION'] += rec['count']
                else:
                    type_counts['OTHER'] += rec['count']

        total_count = sum(r['count'] for r in sql_records)
        total_time = sum(r['total_time'] for r in sql_records)

        # 高频 SQL TOP 10
        by_freq = sorted(sql_records, key=lambda x: x['count'], reverse=True)
        result['top_frequent_sql'] = by_freq[:10]

        # 高耗时 SQL TOP 10
        by_time = sorted(sql_records, key=lambda x: x['avg_time'], reverse=True)
        result['top_slow_sql'] = by_time[:10]

        # 全表扫描 SQL
        full_scan = [r for r in sql_records if r['no_index'] > 0]
        full_scan.sort(key=lambda x: x['count'], reverse=True)
        result['full_scan_sql'] = full_scan[:10]

        result['type_distribution'] = dict(type_counts)
        result['summary'] = {
            'total_sql_types': len(sql_records),
            'total_count': total_count,
            'total_time': round(total_time, 2),
            'full_scan_count': len(full_scan),
        }

    # 解析慢查询
    if slowlog_files:
        slow_records = []
        with open(slowlog_files[0], 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                slow_records.append({
                    'service': row.get('service_name', ''),
                    'query_time': float(row.get('query_time', 0) or 0),
                    'lock_time': float(row.get('lock_time', 0) or 0),
                    'rows_examined': parse_int(row.get('rows_examined', 0)),
                    'sql_text': (row.get('sql_text', '') or '')[:200],
                })
        slow_records.sort(key=lambda x: x['query_time'], reverse=True)
        result['slowlog_top'] = slow_records[:10]
        result['summary']['slowlog_count'] = len(slow_records)

    return result


# ── Gateway 日志分析数据采集 ──────────────────────────────────────────────────

def collect_gateway_data(output_dir):
    """从 gateway_log_analysis 的 JSON 输出中提取统计数据"""
    if not output_dir or not os.path.exists(output_dir):
        return None

    # 查找 JSON/JSON.GZ 文件
    json_files = sorted(glob.glob(os.path.join(output_dir, '*.json')), reverse=True)
    gz_files = sorted(glob.glob(os.path.join(output_dir, '*.json.gz')), reverse=True)

    data = None
    source_file = ''

    for f in gz_files + json_files:
        try:
            if f.endswith('.gz'):
                with gzip.open(f, 'rt', encoding='utf-8') as fh:
                    data = json.load(fh)
            else:
                with open(f, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
            source_file = os.path.basename(f)
            break
        except Exception:
            continue

    if not data:
        return None

    result = {
        'source_file': source_file,
        'summary': {},
        'daily_stats': [],
        'timecost_distribution': {},
        'top_high_timecost': [],
        'top_sql_patterns': [],
        'error_codes': {},
    }

    # 解析 JSON 数据（支持短 key 格式）
    results = data.get('r', data.get('results', {}))
    for dir_path, dir_data in results.items():
        itf = dir_data.get('itf', dir_data.get('interf', {}))
        if not itf:
            continue

        # 每日统计
        ds = itf.get('ds', itf.get('daily_stats', {}))
        for date_str, stats in sorted(ds.items()):
            if isinstance(stats, dict):
                result['daily_stats'].append({
                    'date': date_str,
                    'count': stats.get('c', stats.get('count', 0)),
                })

        # 耗时分布
        tb = itf.get('tb', itf.get('timecost_bins', {}))
        result['timecost_distribution'] = dict(tb)

        # 高耗时 SQL
        ht = itf.get('ht', itf.get('high_timecost', []))
        for item in ht[:10]:
            if isinstance(item, dict):
                result['top_high_timecost'].append({
                    'timecost': item.get('tc', item.get('timecost', 0)),
                    'sql': (item.get('sq', item.get('sql', '')) or '')[:200],
                    'user': item.get('u', item.get('user', '')),
                })
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                result['top_high_timecost'].append({
                    'timecost': item[0],
                    'sql': str(item[1])[:200],
                })

        # 高频 SQL 模式
        sp = itf.get('sp', itf.get('sql_patterns', []))
        for item in sp[:10]:
            if isinstance(item, dict):
                result['top_sql_patterns'].append({
                    'pattern': (item.get('p', item.get('pattern', '')) or '')[:200],
                    'count': item.get('c', item.get('count', 0)),
                    'avg_time': item.get('a', item.get('avg_time', 0)),
                })
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                result['top_sql_patterns'].append({
                    'pattern': str(item[0])[:200],
                    'count': item[1],
                })

        # 错误码
        rc = itf.get('rc', itf.get('resultcode_counts', {}))
        for code, cnt in rc.items():
            if str(code) != '0':
                result['error_codes'][str(code)] = cnt

        total_lines = itf.get('tl', itf.get('total_lines', 0))
        result['summary'] = {
            'total_requests': total_lines,
            'daily_count': len(result['daily_stats']),
            'error_count': sum(result['error_codes'].values()),
        }
        break  # 只取第一个目录

    return result


# ── 表结构对比数据采集 ────────────────────────────────────────────────────────

def collect_schema_diff_data(prod_dir, test_dir):
    """从 table_schema_diff 的导出 JSON 中提取概要信息"""
    if not prod_dir or not os.path.exists(prod_dir):
        return None

    result = {
        'summary': {},
        'prod_instances': 0,
        'test_instances': 0,
        'prod_databases': set(),
        'prod_tables': 0,
    }

    # 统计生产环境
    prod_files = glob.glob(os.path.join(prod_dir, 'instance_*.json'))
    result['prod_instances'] = len(prod_files)

    total_tables = 0
    db_set = set()
    for f in prod_files:
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            dbs = data.get('databases', {})
            for db_name, db_info in dbs.items():
                db_set.add(db_name)
                tables = db_info.get('tables', {})
                total_tables += len(tables)
        except Exception:
            continue

    result['prod_databases'] = list(db_set)
    result['prod_tables'] = total_tables

    # 统计测试环境
    if test_dir and os.path.exists(test_dir):
        test_files = glob.glob(os.path.join(test_dir, 'instance_*.json'))
        result['test_instances'] = len(test_files)

    result['summary'] = {
        'prod_instances': result['prod_instances'],
        'test_instances': result['test_instances'],
        'total_databases': len(db_set),
        'total_tables': total_tables,
        'status': '已完成对比' if result['test_instances'] > 0 else '待导出测试环境',
    }

    return result


# ── 主函数 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='TDSQL 运维汇报数据采集器')
    parser.add_argument('--inspection-csv', help='每日巡检 CSV 路径')
    parser.add_argument('--count-rows-dir', help='count_table_rows output 目录')
    parser.add_argument('--index-dir', help='index_analysis output 目录')
    parser.add_argument('--sql-dir', help='sql_analysis output 目录')
    parser.add_argument('--gateway-dir', help='gateway_log_analysis output 目录')
    parser.add_argument('--schema-diff-prod', help='table_schema_diff 生产环境目录')
    parser.add_argument('--schema-diff-test', help='table_schema_diff 测试环境目录')
    parser.add_argument('-o', '--output', default='output/report_data.json', help='输出 JSON 路径')
    parser.add_argument('--report-title', default='陕西农信核心 TDSQL 数据库主动运维报告', help='报告标题')
    parser.add_argument('--report-date', default=datetime.now().strftime('%Y-%m-%d'), help='报告日期')

    args = parser.parse_args()

    # 自动发现路径
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def auto_path(arg, *candidates):
        if arg:
            return arg
        for c in candidates:
            p = os.path.join(base_dir, c)
            if os.path.exists(p):
                return p
        return None

    # 自动发现最新巡检 CSV
    inspection_csv = args.inspection_csv
    if not inspection_csv:
        insp_dir = os.path.join(base_dir, 'daily_inspection', 'reports')
        if os.path.exists(insp_dir):
            csvs = sorted(glob.glob(os.path.join(insp_dir, '*.csv')), reverse=True)
            if csvs:
                inspection_csv = csvs[0]

    report_data = {
        'meta': {
            'title': args.report_title,
            'date': args.report_date,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'generator': 'tdsql-toolkit/auto_report',
        },
        'modules': {}
    }

    # 采集各模块数据
    sys.stderr.write('[采集] 每日巡检数据...\n')
    insp = collect_inspection_data(inspection_csv)
    if insp:
        report_data['modules']['daily_inspection'] = insp
        sys.stderr.write(f'  ✓ 已采集 {insp["summary"]["instance_count"]} 个实例\n')
    else:
        sys.stderr.write('  - 未找到巡检数据\n')

    sys.stderr.write('[采集] 表数据量统计...\n')
    count_dir = auto_path(args.count_rows_dir, 'count_table_rows/output')
    rows = collect_count_rows_data(count_dir)
    if rows:
        report_data['modules']['count_table_rows'] = rows
        sys.stderr.write(f'  ✓ 已采集 {rows["summary"]["total_tables"]} 张表\n')
    else:
        sys.stderr.write('  - 未找到数据\n')

    sys.stderr.write('[采集] 索引分析...\n')
    idx_dir = auto_path(args.index_dir, 'index_analysis/output')
    idx = collect_index_data(idx_dir)
    if idx:
        report_data['modules']['index_analysis'] = idx
        sys.stderr.write(f'  ✓ 已采集 {idx["summary"]["total_indexes"]} 个索引\n')
    else:
        sys.stderr.write('  - 未找到数据\n')

    sys.stderr.write('[采集] SQL 分析...\n')
    sql_dir = auto_path(args.sql_dir, 'sql_analysis/output')
    sql = collect_sql_data(sql_dir)
    if sql:
        report_data['modules']['sql_analysis'] = sql
        sys.stderr.write(f'  ✓ 已采集 {sql["summary"]["total_sql_types"]} 种 SQL\n')
    else:
        sys.stderr.write('  - 未找到数据\n')

    sys.stderr.write('[采集] Gateway 日志分析...\n')
    gw_dir = auto_path(args.gateway_dir, 'gateway_log_analysis/output')
    gw = collect_gateway_data(gw_dir)
    if gw:
        report_data['modules']['gateway_analysis'] = gw
        sys.stderr.write(f'  ✓ 已采集 {gw["summary"].get("total_requests", 0)} 条请求\n')
    else:
        sys.stderr.write('  - 未找到数据\n')

    sys.stderr.write('[采集] 表结构对比...\n')
    prod_dir = auto_path(args.schema_diff_prod, 'table_schema_diff/output_prod')
    test_dir = auto_path(args.schema_diff_test, 'table_schema_diff/output_test')
    diff = collect_schema_diff_data(prod_dir, test_dir)
    if diff:
        report_data['modules']['schema_diff'] = diff
        sys.stderr.write(f'  ✓ 生产 {diff["summary"]["prod_instances"]} 实例, '
                         f'{diff["summary"]["total_tables"]} 张表\n')
    else:
        sys.stderr.write('  - 未找到数据\n')

    # 输出
    out_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    active_modules = [k for k, v in report_data['modules'].items() if v]
    sys.stderr.write(f'\n[完成] 已采集 {len(active_modules)} 个模块数据 → {out_path}\n')
    sys.stderr.write(f'  活跃模块: {", ".join(active_modules)}\n')

    return 0


if __name__ == '__main__':
    sys.exit(main())
