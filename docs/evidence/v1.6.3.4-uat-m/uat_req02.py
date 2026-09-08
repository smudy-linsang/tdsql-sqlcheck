"""UAT REQ-02 二级分区主表 + REQ-04 网关上传统计验证。"""
import urllib.request, json

with open('C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/_uat_token.txt') as f: token = f.read().strip()
base = 'http://127.0.0.1:8003'
headers = {'Content-Type':'application/json','Authorization':'Bearer '+token}

# REQ-02: 二级分区主表
print('=== REQ-02 二级分区主表验证 ===\n')

# 1. 查看表类型统计接口
req = urllib.request.Request(base+'/api/v1/table-type-stats/history?limit=5', headers=headers)
try:
    r = urllib.request.urlopen(req, timeout=10)
    h = json.loads(r.read().decode())
    print('历史表类型统计数:', len(h.get('items', [])))
    for item in h.get('items', [])[:3]:
        print(f"  history_id={item.get('id')} connection_name={item.get('connection_name')}")
        print(f"    single={item.get('single_count')} broadcast={item.get('broadcast_count')} shard={item.get('shard_count')}")
        print(f"    main={item.get('secondary_partition_main_tables')}/{item.get('secondary_partition_check_state')}/inv={item.get('secondary_partition_inventory_state')}")
        print(f"    candidates={item.get('secondary_partition_candidates')} checked={item.get('secondary_partition_checked')} unk={item.get('secondary_partition_unknown')} unc={item.get('secondary_partition_unchecked')}")
        print(f"    outside_shard={item.get('secondary_partition_outside_shard')}")
except urllib.error.HTTPError as e:
    print('history 接口异常:', e.code, e.read().decode()[:200])

# 2. 对可用连接尝试立即采集
req = urllib.request.Request(base+'/api/v1/tdsql/connections', headers=headers)
r = urllib.request.urlopen(req, timeout=10)
conns = json.loads(r.read().decode()).get('connections', [])
dist_conn = next((c for c in conns if c.get('is_distributed')==1 and 'smoke' not in c['name'].lower() and 'bad' not in c['name'].lower()), None)
if not dist_conn:
    print('\n无可用分布式连接用于采集')
else:
    cid = dist_conn['id']
    print(f"\n尝试在 {dist_conn['name']} (id={cid}) 上做表类型统计...")
    body = json.dumps({'connection_id': cid, 'databases': []}).encode('utf-8')
    req = urllib.request.Request(base+'/api/v1/table-type-stats/run', data=body, headers=headers, method='POST')
    try:
        r = urllib.request.urlopen(req, timeout=210)
        out = json.loads(r.read().decode())
        print('  采集结果:')
        for k in ('status', 'connection_id', 'connection_name', 'started_at', 'finished_at'):
            print(f'    {k}: {out.get(k)}')
        items = out.get('items') or out.get('databases') or []
        if not items and 'summary' in out:
            items = out['summary'].get('items', [])
        print('  库明细数:', len(items))
        # 关键二级分区主表字段
        for it in items[:5]:
            print(f"    db={it.get('database_name')} main={it.get('secondary_partition_main_tables')}/state={it.get('secondary_partition_check_state')}/inv={it.get('secondary_partition_inventory_state')} cand={it.get('secondary_partition_candidates')}/chk={it.get('secondary_partition_checked')}/unk={it.get('secondary_partition_unknown')}/unc={it.get('secondary_partition_unchecked')}/out={it.get('secondary_partition_outside_shard')}")
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:300]
        print('  /run 异常:', e.code, body)

# REQ-04 网关
print('\n=== REQ-04 网关验证 ===\n')
# 列出最近一次网关报告
req = urllib.request.Request(base+'/api/v1/gateway-log/reports?limit=5', headers=headers)
try:
    r = urllib.request.urlopen(req, timeout=10)
    gr = json.loads(r.read().decode())
    items = gr.get('items', [])
    print('网关报告数:', len(items))
    for it in items[:3]:
        print(f"  id={it.get('id')} connection_name={it.get('connection_name') or it.get('connection_id')}")
        print(f"    input_bytes={it.get('input_bytes')} status={it.get('status')} created_at={it.get('created_at')}")
        if it.get('analysis_meta'):
            meta = it['analysis_meta']
            print(f"    meta.parsed={meta.get('parse_quality',{}).get('parsed_lines')}/{meta.get('parse_quality',{}).get('total_lines')}")
            print(f"    meta.queries={meta.get('metrics',{}).get('total_queries')}")
        # 关键：报告 HTML 应带 connection name
        rid = it['id']
        req2 = urllib.request.Request(base+f'/api/v1/gateway-log/reports/{rid}/html', headers=headers)
        try:
            r2 = urllib.request.urlopen(req2, timeout=10)
            html = r2.read().decode('utf-8', errors='replace')
            has_name = '实例连接名称' in html
            print(f'    HTML 含"实例连接名称": {has_name}')
            if has_name:
                idx = html.find('实例连接名称')
                print(f'    报告头块片段: {html[idx:idx+150].replace(chr(10), " ")}')
        except urllib.error.HTTPError as e:
            print(f'    HTML 导出失败: {e.code}')
except urllib.error.HTTPError as e:
    print('网关 reports 接口异常:', e.code, e.read().decode()[:200])

# REQ-04 触发一次小文件上传，确认 200 OK 且返回 report_id（流式解析链路）
print('\n--- 网关小文件上传 (REQ-04 §6.5 链路验证) ---')
import io
buf = io.BytesIO()
for i in range(1000):
    # 仿 interf 日志行
    buf.write(f'2026-09-07 10:00:00 119.45.220.89 192.0.2.1 3306 user 1 timecost:{i%50} sql:SELECT * FROM t_{i%10} WHERE id={i}\n'.encode())
buf.seek(0)

import http.client
conn_h = http.client.HTTPConnection('127.0.0.1', 8003, timeout=120)
boundary = '----uat-boundary-test-1'
body = []
body.append(f'--{boundary}\r\n'.encode())
body.append(b'Content-Disposition: form-data; name="log_type"\r\n\r\ninterf\r\n')
body.append(f'--{boundary}\r\n'.encode())
body.append(b'Content-Disposition: form-data; name="connection_id"\r\n\r\n5ea70d74\r\n')
body.append(f'--{boundary}\r\n'.encode())
body.append(b'Content-Disposition: form-data; name="file"; filename="uat_small.log"\r\n')
body.append(b'Content-Type: text/plain\r\n\r\n')
body.append(buf.read())
body.append(f'\r\n--{boundary}--\r\n'.encode())
payload = b''.join(body)
conn_h.request('POST', '/api/v1/gateway-log/upload', body=payload, headers={
    'Content-Type': f'multipart/form-data; boundary={boundary}',
    'Content-Length': str(len(payload)),
    'Authorization': 'Bearer ' + token,
})
r = conn_h.getresponse()
print('  /upload status:', r.status)
print('  x-request-id:', r.getheader('x-request-id'))
data = r.read().decode('utf-8', errors='replace')
print('  body[:600]:', data[:600])
