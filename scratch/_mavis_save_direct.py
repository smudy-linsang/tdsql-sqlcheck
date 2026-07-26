"""直接调用 _save_audit_history 看实际错误"""
import sys
sys.path.insert(0, '.')
from backend.services.audit_service import _save_audit_history
from backend.models import AuditResult, AuditSummary, Violation
from backend.engine.rules.base import Severity

results = [AuditResult(
    sql='CREATE TABLE t1 (id INT)',
    sql_type='DDL', passed=False, file_path='test.sql', line_number=1,
    violations=[Violation(rule_id='R001', category='ddl', severity=Severity.ERROR, message='test', suggestion='fix')]
)]
summary = AuditSummary(total_sql=1, passed=0, failed=1, error_count=1, warning_count=0, pass_rate=0.0, results=results)

rid = _save_audit_history('extracted_schema', 'test.sql', results, summary,
                          created_by='admin', connection_id='adhoc', db_name='tdsql_test')
print('返回 report_id:', rid)

# 直接查 DB 看实际写入情况
import pymysql
conn = pymysql.connect(host='127.0.0.1', port=13306, user='root', password='tdsql_test_2024', database='tdsql_sqlcheck_test')
cur = conn.cursor()
if rid:
    cur.execute("SELECT id, audit_type, connection_id, source FROM audit_history WHERE id = %s", (rid,))
    print(f"DB id={rid}:", cur.fetchall())
cur.execute("SELECT id, audit_type, connection_id, source FROM audit_history WHERE audit_type='extracted_schema' ORDER BY id DESC LIMIT 5")
print("\n最近 5 条 extracted_schema:")
for r in cur.fetchall():
    print(f"  {r}")
conn.close()
