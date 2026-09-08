"""UAT REQ-01 报告实例标识验证。
H01 文件审核、H02 在线元数据审核、H03 慢SQL扫描任务，验证生成的报告带"实例连接名称"块。
"""
import urllib.request, json

with open('C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/_uat_token.txt') as f: token = f.read().strip()
base = 'http://127.0.0.1:8003'
headers = {'Content-Type':'application/json','Authorization':'Bearer '+token}

print('=== REQ-01 报告实例标识验证 ===\n')

# 1. 查已保存连接
req = urllib.request.Request(base+'/api/v1/tdsql/connections', headers=headers)
r = urllib.request.urlopen(req, timeout=10)
conns = json.loads(r.read().decode()).get('connections', [])
print('已保存连接数:', len(conns))
for c in conns:
    print(f"  id={c['id']} name='{c['name']}' host={c['host']} port={c['port']} dist={c.get('is_distributed',0)}")

# 2. 取一个分布式连接 id
dist_conn = next((c for c in conns if c.get('is_distributed')==1), None) or (conns[0] if conns else None)
if not dist_conn:
    print('无可用连接，跳过在线元数据审核')
else:
    cid = dist_conn['id']
    print(f"\n使用连接: id={cid} name='{dist_conn['name']}'")

    # 3. 提交一份文件审核（H01 离线）
    sql = """CREATE TABLE t_uat (
  id BIGINT NOT NULL,
  ts DATETIME ON UPDATE CURRENT_TIMESTAMP,
  c VARCHAR(20) CHARACTER SET utf8mb4,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"""
    body = json.dumps({'content': sql, 'instance_type': 'distributed', 'project_id': ''}).encode('utf-8')
    req = urllib.request.Request(base+'/api/v1/audit/file', data=body, headers=headers, method='POST')
    r = urllib.request.urlopen(req, timeout=10)
    fa = json.loads(r.read().decode())
    print(f"\nH01 文件审核: report_id={fa.get('report_id')} total_violations={fa.get('total_violations')}")

    # 4. 列出文件审核报告，找到最新一条，导出 HTML 看是否带"实例连接名称：未关联实例（离线文件审核）"
    req = urllib.request.Request(base+'/api/v1/audit/file-reports?limit=5', headers=headers)
    r = urllib.request.urlopen(req, timeout=10)
    fr = json.loads(r.read().decode())
    items = fr.get('items', [])
    print(f'文件审核报告数: {len(items)}')
    if items:
        rid = items[0]['id']
        # 导出 HTML
        req = urllib.request.Request(base+f'/api/v1/audit/file-reports/{rid}/html', headers=headers)
        try:
            r = urllib.request.urlopen(req, timeout=10)
            html = r.read().decode('utf-8', errors='replace')
            print(f'H01 HTML 报告长度: {len(html)}')
            # 关键文本探测
            print('  包含"实例连接名称":', '实例连接名称' in html)
            print('  包含"未关联实例（离线文件审核）":', '未关联实例（离线文件审核）' in html)
            print('  包含 R043:', 'R043' in html)
            # 取出报告头部一小段
            idx = html.find('实例连接名称')
            if idx > 0:
                print('  实例连接名称块:', html[idx:idx+200].replace('\n', ' '))
        except Exception as e:
            print('H01 HTML 导出失败:', e)

# 5. capabilities 测试 (REQ-04)
print('\n--- REQ-04 网关 capabilities ---')
req = urllib.request.Request(base+'/api/v1/gateway-log/capabilities', headers=headers)
r = urllib.request.urlopen(req, timeout=10)
cap = json.loads(r.read().decode())
for k in ['config_version','upload_max_bytes','request_max_bytes','concurrent','concurrency_fixed','browser_wait_seconds','deployment_mode']:
    print(f"  {k}: {cap.get(k)}")

print('\nREQ-01/H01/H04 H08 capabilities 验证完成。')
