"""检查 M01 报告 HTML 中"实例连接名称"出现位置。"""
import re
with open('C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/evidence/v1.6.3.4-uat-m/uat_m2_m01_report_9.html', encoding='utf-8', errors='replace') as f:
    html = f.read()
print('total len:', len(html))
print('count of 未关联实例（网关日志分析）:', html.count('未关联实例（网关日志分析）'))
print('count of 未关联实例:', html.count('未关联实例'))
print('count of SIT-分布式实例A:', html.count('SIT-分布式实例A'))
print('count of report-context:', html.count('class="report-context"'))
for m in re.finditer('实例连接名称', html):
    s = m.start()
    print('  pos=', s, ':', repr(html[max(0,s-60):s+120]))
