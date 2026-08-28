"""Extract original attached HTML DDL without losing Unicode, then replay."""
import hashlib
import html
import json
import logging
import re
import sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,sys.argv[1])
logging.getLogger('sqlglot').setLevel(logging.ERROR)
from backend.engine.checker import RuleChecker
c=RuleChecker()
rows=[]
for label,name,fixture,scope in [
    ('gg77','TDSQL审核报告_gg77_2026-08-25.html','report_6309_kcfb_list_info.sql','distributed'),
    ('gg78','TDSQL审核报告_gg78_2026-08-25 (1).html','report_6311_biz_tx_log.sql','centralized')]:
    source=Path('C:/Users/linsa/OneDrive/Desktop')/name
    raw=source.read_bytes()
    sql=html.unescape(re.search(r'<div class="sql-text">(.*?)</div>',raw.decode('utf-8'),re.S).group(1)).strip()
    (HERE/f'original_{label}.sql').write_text(sql,encoding='utf-8')
    repo_fixture=(Path(sys.argv[1])/'tests/fixtures'/fixture).read_text(encoding='utf-8').strip()
    normalize=lambda x:x.replace('\r\n','\n')
    result=c.audit_sql(sql,instance_type=scope)
    fresult=c.audit_sql(repo_fixture,instance_type=scope)
    rows.append({'source_name':name,'source_sha256':hashlib.sha256(raw).hexdigest(),'scope':scope,'sql_sha256':hashlib.sha256(sql.encode()).hexdigest(),'fixture':fixture,'exact_equal_ignoring_line_endings':normalize(sql)==normalize(repo_fixture),'source_replacement_char_count':sql.count('\ufffd'),'fixture_replacement_char_count':repo_fixture.count('\ufffd'),'fired':sorted({v.rule_id for v in result.violations}),'fixture_fired':sorted({v.rule_id for v in fresult.violations}),'parse_error':c.parser.parse(sql).parse_error})
Path(sys.argv[2]).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(rows,ensure_ascii=False,indent=2))
