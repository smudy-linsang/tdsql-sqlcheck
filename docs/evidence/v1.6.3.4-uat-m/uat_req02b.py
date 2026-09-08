"""UAT REQ-04 网关验证（修正版）"""
import urllib.request, json, http.client, io

with open('C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/_uat_token.txt') as f: token = f.read().strip()
base = 'http://127.0.0.1:8003'
headers = {'Content-Type':'application/json','Authorization':'Bearer '+token}

print('=== REQ-04 网关验证 ===\n')

# 网关 reports 列表
req = urllib.request.Request(base+'/api/v1/gateway-log/reports?limit=5', headers=headers)
r = urllib.request.urlopen(req, timeout=10)
gr = json.loads(r.read().decode())
items = gr if isinstance(gr, list) else gr.get('items', [])
print('网关报告数:', len(items))
for it in items[:3]:
    rid = it.get('id')
    name = it.get('connection_name') or it.get('connection_id') or '(空)'
    print(f"  id={rid} connection_name='{name}' status={it.get('status')} bytes={it.get('input_bytes')}")
    if it.get('analysis_meta'):
        meta = it['analysis_meta']
        pq = meta.get('parse_quality', {})
        print(f"    parsed={pq.get('parsed_lines')}/{pq.get('total_lines')} skipped={pq.get('skipped_lines')}")
    # HTML 报告：是否带"实例连接名称"
    if rid:
        try:
            r2 = urllib.request.urlopen(urllib.request.Request(base+f'/api/v1/gateway-log/reports/{rid}/html', headers=headers), timeout=10)
            html = r2.read().decode('utf-8', errors='replace')
            print(f'    HTML 含"实例连接名称": {"实例连接名称" in html}')
            if '实例连接名称' in html:
                idx = html.find('实例连接名称')
                print(f'    报告头块: {html[idx:idx+180].replace(chr(10), " ")}')
        except Exception as e:
            print(f'    HTML 导出失败: {e}')

# 触发一次小文件上传
print('\n--- 网关小文件上传 (1000 行 interf 日志) ---')
buf = io.BytesIO()
for i in range(1000):
    buf.write(f'2026-09-07 10:00:00 119.45.220.89 192.0.2.1 3306 user 1 timecost:{i%50} sql:SELECT * FROM t_{i%10} WHERE id={i}\n'.encode())
buf.seek(0)

conn_h = http.client.HTTPConnection('127.0.0.1', 8003, timeout=200)
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
print('  body[:800]:', data[:800])
