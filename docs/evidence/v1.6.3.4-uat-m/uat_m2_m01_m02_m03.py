"""UAT 第二轮：M01/M02/M03 端到端实测。"""
import urllib.request, json, http.client, io, sys
sys.path.insert(0, 'C:/TDSQL_SQLCHECK/TDSQL-SQLCheck')

# 1) 登录拿 token
data = json.dumps({'username':'admin','password':'UatM2@2026!'}).encode()
req = urllib.request.Request('http://127.0.0.1:8003/api/v1/auth/login',
                              data=data, headers={'Content-Type':'application/json'}, method='POST')
r = urllib.request.urlopen(req, timeout=5)
body = json.loads(r.read().decode())
token = body['token']
print(f'[TOKEN] OK len={len(token)}')
with open('C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/evidence/v1.6.3.4-uat-m/uat_m2_token.txt','w',encoding='utf-8') as f:
    f.write(token)
headers = {'Content-Type':'application/json','Authorization':'Bearer '+token}
base = 'http://127.0.0.1:8003'

# 2) 取分布式连接
req = urllib.request.Request(base+'/api/v1/tdsql/connections', headers=headers)
r = urllib.request.urlopen(req, timeout=10)
conns = json.loads(r.read().decode()).get('connections', [])
dist = next((c for c in conns if c.get('is_distributed')==1 and 'bad' not in c['name'].lower() and 'smoke' not in c['name'].lower()), None)
print(f'[CONN] dist={dist["name"] if dist else "NONE"} (id={dist["id"] if dist else "?"})')
cid = dist['id'] if dist else conns[0]['id']

# ===========================================
# M01 验证：H08 新报告 HTML 仅 1 次"实例连接名称"
# ===========================================
print('\n=== M01 端到端验证：H08 报告头部去重 ===\n')
buf = io.BytesIO()
for i in range(2000):
    buf.write(f'[2026-09-08 10:00:0{i%10} {i:05d}] INFO topic=test&timecost=12.5&sql=select {i}&db=biz&user=root&host=127.0.0.1\n'.encode())
buf.seek(0)
conn_h = http.client.HTTPConnection('127.0.0.1', 8003, timeout=200)
boundary = '----uat-m01-2'
body = []
body.append(f'--{boundary}\r\n'.encode())
body.append(b'Content-Disposition: form-data; name="log_type"\r\n\r\ninterf\r\n')
body.append(f'--{boundary}\r\n'.encode())
body.append(f'Content-Disposition: form-data; name="connection_id"\r\n\r\n{cid}\r\n'.encode())
body.append(f'--{boundary}\r\n'.encode())
body.append(b'Content-Disposition: form-data; name="file"; filename="interf_instance_0.2026-09-08.0"\r\n')
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
print(f'  /upload status={r.status} x-request-id={r.getheader("x-request-id")}')
upload = json.loads(r.read().decode('utf-8', errors='replace'))
new_rid = upload.get('report_id')
print(f'  new report_id={new_rid} parsed={upload.get("parse_quality",{}).get("parsed_lines")}/{upload.get("parse_quality",{}).get("total_lines")}')

# 取 HTML
req = urllib.request.Request(base+f'/api/v1/gateway-log/reports/{new_rid}/html', headers=headers)
html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='replace')
with open(f'C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/evidence/v1.6.3.4-uat-m/uat_m2_m01_report_{new_rid}.html','w',encoding='utf-8') as f:
    f.write(html)

# 关键断言
n_count = html.count('实例连接名称')
n_placeholder = html.count('未关联实例（网关日志分析）')
n_frozen = html.count('SIT-分布式实例A') if dist else 0
n_class_block = html.count('class="report-context"')
print(f'  HTML 长度={len(html)}')
print(f'  "实例连接名称" 出现次数: {n_count}  (期望=1)')
print(f'  "未关联实例（网关日志分析）" 出现次数: {n_count}  (期望=0)')
print(f'  "SIT-分布式实例A" 出现次数: {n_frozen}  (期望>=1)')
print(f'  report-context 块数: {n_class_block}  (期望=1)')
m01_pass = n_count == 1 and n_placeholder == 0 and n_frozen >= 1 and n_class_block == 1
print(f'  M01 端到端: {"PASS" if m01_pass else "FAIL"}')
if not m01_pass:
    # 找出全部"实例连接名称"出现位置
    import re
    for m in re.finditer(r'实例连接名称', html):
        print(f'    pos={m.start()}: ...{html[max(0,m.start()-30):m.start()+80]}...')

# ===========================================
# M02 验证：H01 离线报告含 #fff3cd 徽标
# ===========================================
print('\n=== M02 端到端验证：离线报告带浅黄徽标 ===\n')
sql = """CREATE TABLE t_uat_m02 (
  id BIGINT NOT NULL,
  ts DATETIME ON UPDATE CURRENT_TIMESTAMP,
  c VARCHAR(20) CHARACTER SET utf8mb4,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"""
body = json.dumps({'content':sql,'instance_type':'distributed','project_id':''}).encode('utf-8')
req = urllib.request.Request(base+'/api/v1/audit/file', data=body, headers=headers, method='POST')
r = urllib.request.urlopen(req, timeout=10)
fa = json.loads(r.read().decode())
print(f'  H01 file audit: {fa}')

# 取最新文件报告
req = urllib.request.Request(base+'/api/v1/audit/file-reports?limit=1', headers=headers)
r = urllib.request.urlopen(req, timeout=10)
items = json.loads(r.read().decode()).get('items', [])
h01_rid = items[0]['id'] if items else None
print(f'  h01 report_id={h01_rid}')

# 拉 HTML
if h01_rid:
    req = urllib.request.Request(base+f'/api/v1/audit/file-reports/{h01_rid}/html', headers=headers)
    html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='replace')
    with open(f'C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/evidence/v1.6.3.4-uat-m/uat_m2_m02_h01_{h01_rid}.html','w',encoding='utf-8') as f:
        f.write(html)
    has_offline_text = '未关联实例（离线文件审核）' in html
    has_badge_color = '#fff3cd' in html
    has_text_color = '#856404' in html
    n_offline_span = html.count('未关联实例（离线文件审核）')
    has_strong_offline = '<strong>未关联实例（离线文件审核）</strong>' in html
    print(f'  HTML 长度={len(html)}')
    print(f'  含"未关联实例（离线文件审核）": {has_offline_text} (次数={n_offline_span})')
    print(f'  含浅黄徽标 #fff3cd: {has_badge_color}')
    print(f'  含文字色 #856404: {has_text_color}')
    print(f'  误用 strong 包占位: {has_strong_offline} (期望 False)')
    m02_pass = has_offline_text and has_badge_color and has_text_color and not has_strong_offline
    print(f'  M02 端到端: {"PASS" if m02_pass else "FAIL"}')

# ===========================================
# M02 验证：H08 报告不应用徽标（已绑定真名）
# ===========================================
print('\n=== M02 端到端验证：H08 报告不应用徽标（已绑定真名） ===\n')
if new_rid:
    has_strong_frozen = '<strong>SIT-分布式实例A</strong>' in html if html else False
    print(f'  M01 报告中"已绑定真名"用 <strong>: {has_strong_frozen}')
    if 'class="badge-offline"' in html or has_badge_color in html:
        # M01/M02 报告可能不是同一个 HTML
        pass
    # 看 M01 报告
    m01_html_path = f'C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/evidence/v1.6.3.4-uat-m/uat_m2_m01_report_{new_rid}.html'
    with open(m01_html_path, encoding='utf-8') as f:
        m01_html = f.read()
    has_frozen_strong = '<strong>SIT-分布式实例A</strong>' in m01_html
    has_offline_in_m01 = '#fff3cd' in m01_html
    print(f'  M01 报告含 <strong>SIT-分布式实例A</strong>: {has_frozen_strong}')
    print(f'  M01 报告含徽标 #fff3cd: {has_offline_in_m01} (期望 False)')
    m02_h08_pass = has_frozen_strong and not has_offline_in_m01
    print(f'  M02(H08侧)端到端: {"PASS" if m02_h08_pass else "FAIL"}')

print('\n=== 全部端到端实测完成 ===')
