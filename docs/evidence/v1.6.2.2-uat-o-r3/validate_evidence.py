"""Offline consistency and hash verification. Product failures stay failures."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
HERE=Path(__file__).resolve().parent
REPORT=HERE.parents[1]/'UAT-v1.6.2.2-第三轮全项目用户验收测试报告-智能体O.md'
ap=argparse.ArgumentParser();ap.add_argument('--seal',action='store_true');args=ap.parse_args()
def read(n):return json.loads((HERE/n).read_text(encoding='utf-8'))
checks=[]
def check(name,condition):checks.append({'check':name,'passed':bool(condition)})
files=sorted(p for p in HERE.iterdir() if p.is_file() and p.name not in ('validation.json','evidence_manifest.json'))+[REPORT]
def label(p):return p.name if p.parent==HERE else '../../'+p.name
if args.seal:
    manifest={'tested_commit':'1596e8b4819d17beb6507914c4592b0be184a29c','files':[{'path':label(p),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]}
    (HERE/'evidence_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
manifest=read('evidence_manifest.json')
for entry in manifest['files']:
    p=(HERE/entry['path']).resolve()
    if p not in files:
        check('manifest path in explicit evidence inventory: '+entry['path'],False);continue
    check('sha256 '+entry['path'],p.exists() and p.stat().st_size==entry['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==entry['sha256'])
check('manifest inventory exact',set(e['path'] for e in manifest['files'])==set(label(p) for p in files))
for p in files:
    if p.suffix=='.json':
        try:json.loads(p.read_text(encoding='utf-8'));ok=True
        except Exception:ok=False
        check('valid JSON '+p.name,ok)
summary=read('summary.json'); core=read('rule_probe_current.json'); diff=read('round3_diff.json')
check('119 definitions unchanged',len(core['rules'])==119 and diff['rules_equal'])
check('116 hit rules / three honest gaps',summary['fired_rule_count']==116 and summary['unverified_rules']==['R025','R038','R049'])
check('1000 inputs and old three oracle failures',core['case_count']==1000 and len(core['failures'])==3)
check('6 original corpus changes, 94 edge changes',len(diff['changes'])==6 and len(diff['edge_changes'])==94)
kfn=[r for r in core['rows'] if r['id'].startswith('kfn_')]
check('75 original KFN now E999',len(kfn)==75 and all('E999_SYNTAX_ERROR' in r['fired'] for r in kfn))
for version in ('29.0.0','30.14.0','30.17.0'):
    edge=read('edge_'+version+'.json');kf=[r for r in edge['rows'] if r['kind']=='kfn']
    check(version+' 324 inputs and 252 fail-closed KFN',len(edge['rows'])==324 and len(kf)==252 and all('E999_SYNTAX_ERROR' in r['fired'] and not r['all_business_rules_disabled']['passed'] and 'E999_SYNTAX_ERROR' in r['all_business_rules_disabled']['fired'] for r in kf))
    check(version+' 713 test passes','713 passed' in (HERE/('manifest_'+version+'.txt')).read_text(encoding='utf-8'))
    load=read('load_'+version+'.json')
    check(version+' preserves new BLOCK evidence',load['count']==27 and len(load['missing_r042'])==12 and len(load['false_pass'])==4)
check('baseline LOAD no misses',not read('load_baseline.json')['missing_r042'] and not read('load_baseline.json')['false_pass'])
check('formal gate failure remains visible','STATUS NOT_IMPLEMENTED' in (HERE/'implementation_matrix.txt').read_text(encoding='utf-8'))
suites=ET.parse(HERE/'full_regression.xml').getroot().findall('testsuite')
check('1417 tests zero failures/errors/skips',sum(int(s.get('tests','0')) for s in suites)==1417 and all(int(s.get(k,'0'))==0 for s in suites for k in ('errors','failures','skipped')))
http=read('http_results.json')
check('HTTP 614 expected first-batch statuses',len(http)==614 and Counter(r['status'] for r in http)==Counter({200:611,403:3}))
check('17 old service differences unchanged',len(read('service_current.json'))==17 and read('service_current.json')==read('service_baseline.json'))
check('EXPLAIN two actual 500s retained',[r['status'] for r in read('explain_context_results.json')]==[200,500,200,500])
check('diagnostic cleanup clearly labeled',read('ephemeral_cleanup.json')['diagnostic_only_missing_import_supplied'] and all(r['explicit_close_calls']==0 and r['connection_open_after_return'] for r in read('ephemeral_cleanup.json')['rows']))
check('real index duplicate finding retained',sum(r['finding_type']=='重复索引' for r in read('index_report_contract.json')['actual_findings'])==1)
inter=read('gateway_interaction.json')
check('gateway failed controls retained',inter['sectionBefore']==inter['sectionAfter'] and inter['inputsBefore']==inter['inputsAfter']==['',''])
check('daily failed collection retained',[r['status'] for r in read('daily_guard.json')]==[400,400,200])
steps=read('browser_steps.json')
check('browser groups agree with summary',len(steps)==summary['browser_evidence_groups'] and len({s['id'] for s in steps})==len(steps))
for step in steps:
    check('browser pair '+step['id'],all((HERE/(step['id']+ext)).is_file() and (HERE/(step['id']+ext)).stat().st_size>0 for ext in ('.txt','.jpg')))
check('actual green LOAD page retained','审核通过' in (HERE/'17-load-xml-false-pass.txt').read_text(encoding='utf-8'))
pdfs=read('pdf_checks.json')
check('four actual PDFs, five pages',len(pdfs)==4 and sum(p['pages'] for p in pdfs)==5 and all((HERE/p['file']).read_bytes().startswith(b'%PDF-') for p in pdfs))
check('five PDF raster pages',len(list(HERE.glob('*_page-*.png')))==5)
for doc in (REPORT,HERE/'README.md'):
    for target in re.findall(r'\]\(([^)]+)\)',doc.read_text(encoding='utf-8')):
        if target.startswith(('https://','http://','#')):continue
        check('local link '+target,(doc.parent/target.split('#')[0]).exists())
bad=[]
secret_patterns=[rb'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}',rb'access_token=[A-Za-z0-9_-]{12,}']
for p in files:
    if p.suffix not in ('.md','.py','.json','.txt','.xml','.html','.sql'):continue
    raw=p.read_bytes()
    if any(re.search(pattern,raw) for pattern in secret_patterns):bad.append(p.name)
check('no JWT/token literal in text artifacts',not bad)
failed=[r['check'] for r in checks if not r['passed']]
result={'kind':'offline evidence integrity, NOT product acceptance','product_uat':'NOT_APPROVED','checks':len(checks),'passed':len(checks)-len(failed),'failed':failed,'browser_groups':len(steps),'manifest_files':len(manifest['files']),'secret_scan_matches':bad}
(HERE/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
sys.exit(bool(failed))
