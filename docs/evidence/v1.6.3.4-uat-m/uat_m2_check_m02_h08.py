"""M02 H08 侧验证：M01 已绑定报告含 strong 而非徽标。"""
import re
with open('C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/evidence/v1.6.3.4-uat-m/uat_m2_m01_report_9.html', encoding='utf-8', errors='replace') as f:
    html = f.read()
# 找 SIT-分布式实例A 上下文
for m in re.finditer('SIT-分布式实例A', html):
    s = m.start()
    print('  pos=', s, ':', repr(html[max(0,s-100):s+50]))
print()
print('contains #fff3cd:', '#fff3cd' in html, '(期望 False)')
print('contains <strong>SIT-分布式实例A</strong>:', '<strong>SIT-分布式实例A</strong>' in html, '(期望 True)')
