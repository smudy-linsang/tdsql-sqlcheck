"""精确定位 #fff3cd 在 H08 报告中的出现位置，区分分析器告警 vs 来源块徽标。"""
import re
with open('C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/evidence/v1.6.3.4-uat-m/uat_m2_m01_report_9.html', encoding='utf-8', errors='replace') as f:
    html = f.read()
for m in re.finditer('#fff3cd', html):
    s = m.start()
    ctx = html[max(0,s-150):s+200]
    print('  pos=', s, ':')
    print('    ctx:', repr(ctx))
    print()
