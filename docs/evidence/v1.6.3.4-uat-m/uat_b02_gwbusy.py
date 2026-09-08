"""REQ-04 + B-02 一致性 + GATEWAY_BUSY 验证。
两个并发上传，第二个被锁挡回 429，应满足：
- Retry-After 头存在
- X-Request-ID 头 = 响应体内 detail.request_id
- detail.code = GATEWAY_BUSY
- 文案含"未被处理"
"""
import urllib.request, json
import http.client, threading, time

with open('C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/_uat_token.txt') as f: token = f.read().strip()
results = []
def post(tag):
    c = http.client.HTTPConnection('127.0.0.1', 8003, timeout=200)
    boundary = f'----gwbusy-{tag}'
    body = []
    body.append(f'--{boundary}\r\n'.encode())
    body.append(b'Content-Disposition: form-data; name="log_type"\r\n\r\ninterf\r\n')
    body.append(f'--{boundary}\r\n'.encode())
    body.append(b'Content-Disposition: form-data; name="connection_id"\r\n\r\n5ea70d74\r\n')
    body.append(f'--{boundary}\r\n'.encode())
    body.append(b'Content-Disposition: form-data; name="file"; filename="interf_instance_0.2026-09-07.0"\r\n')
    body.append(b'Content-Type: text/plain\r\n\r\n')
    # 让分析多花点时间（多行）
    lines = []
    for i in range(20000):
        lines.append(f'[2026-09-07 10:00:0{i%10} {i:05d}] INFO topic=test&timecost=12.5&sql=select {i}&db=biz&user=root&host=127.0.0.1')
    body.append('\n'.join(lines).encode())
    body.append(f'\r\n--{boundary}--\r\n'.encode())
    payload = b''.join(body)
    c.request('POST', '/api/v1/gateway-log/upload', body=payload, headers={
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Content-Length': str(len(payload)),
        'Authorization': 'Bearer ' + token,
    })
    r = c.getresponse()
    body_resp = r.read().decode('utf-8', errors='replace')
    return (tag, r.status, r.getheader('x-request-id'), r.getheader('Retry-After'), body_resp)

t1 = threading.Thread(target=lambda: results.append(post('A')))
t1.start()
time.sleep(0.3)  # 让 A 先到锁
t2 = threading.Thread(target=lambda: results.append(post('B')))
t2.start()
t1.join()
t2.join()

for r in results:
    tag, code, xid, retry, body = r
    print(f'\n=== 请求 {tag} ===')
    print('  status:', code)
    print('  X-Request-ID 头:', xid)
    print('  Retry-After 头:', retry)
    # 解析体
    try:
        j = json.loads(body)
        if 'detail' in j and isinstance(j['detail'], dict):
            d = j['detail']
            print('  体内 request_id:', d.get('request_id'))
            print('  体内 code:', d.get('code'))
            print('  体内 stage:', d.get('stage'))
            print('  体内 message:', d.get('message'))
            # 头体一致性（B-02 修复点）
            print('  头体 request_id 一致:', xid == d.get('request_id'))
        else:
            print('  body[:300]:', body[:300])
    except Exception as e:
        print('  body[:200]:', body[:200])
