"""REQ-04 网关真实格式小文件上传链路测试。"""
import urllib.request, json, http.client, io

with open('C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/_uat_token.txt') as f: token = f.read().strip()
base = 'http://127.0.0.1:8003'
headers = {'Content-Type':'application/json','Authorization':'Bearer '+token}

print('=== REQ-04 网关真实格式小文件上传 ===\n')

# 仿造 SAMPLE_INTERF_LOG 真实格式：1000 行
buf = io.BytesIO()
for i in range(1000):
    tc = 12.5 + (i % 100) * 1.7
    line = f'[2026-09-07 10:00:0{i%10} {i:05d}] INFO topic=test&timecost={tc:.1f}&sql=select * from t_{i%10}&db=biz&user=root&host=127.0.0.1\n'
    buf.write(line.encode())
buf.seek(0)

conn_h = http.client.HTTPConnection('127.0.0.1', 8003, timeout=200)
boundary = '----uat-boundary-test-real'
body = []
body.append(f'--{boundary}\r\n'.encode())
body.append(b'Content-Disposition: form-data; name="log_type"\r\n\r\ninterf\r\n')
body.append(f'--{boundary}\r\n'.encode())
body.append(b'Content-Disposition: form-data; name="connection_id"\r\n\r\n5ea70d74\r\n')
body.append(f'--{boundary}\r\n'.encode())
# 文件名须符合 <type>_instance_<port>.<date>.<seq> 规则
body.append(b'Content-Disposition: form-data; name="file"; filename="interf_instance_0.2026-09-07.0"\r\n')
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
print('  body[:1200]:', data[:1200])
