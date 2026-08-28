"""Offline verification/generation of recorded R2 evidence, not a product run."""
from collections import Counter
from datetime import datetime, timezone
import ast
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
R1 = HERE.parent/'v1.6.2.2-uat-o-r1'
REPORT = HERE.parents[1]/'UAT-v1.6.2.2-第二轮全项目用户验收测试报告-智能体O.md'
E999 = 'E999_SYNTAX_ERROR'


def read(name, directory=HERE):
    return json.loads((directory/name).read_text(encoding='utf-8'))


def write(name, value):
    (HERE/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    core, before = read('rule_probe_current.json'), read('rule_probe_current.json',R1)
    diff = read('round2_diff.json')
    assert core['case_count'] == len(core['rows']) == 1000
    assert len(core['rules']) == core['rule_count'] == 119
    assert core['rules'] == before['rules'] and diff['rules_equal']
    assert len(diff['changes']) == 70
    assert all(set(r['after'])-set(r['before']) == {E999} and set(r['before'])<=set(r['after']) for r in diff['changes'])
    failures = Counter(r['kind'] for r in core['failures'])
    assert failures == {'corpus_exact':3,'must_have':2}
    assert {r['id'] for r in core['failures'] if r['kind']=='must_have'} == {'kfn_literal:19','kfn_literal:20'}
    kfn = [r for r in core['rows'] if r['id'].startswith('kfn_')]
    assert len(kfn) == 75 and sum(E999 not in r['fired'] for r in kfn)==2
    fired = {r for r in core['covered'] if r.startswith('R')}
    assert len(fired)==114
    missing = sorted({r['rule_id'] for r in core['rules']}-fired)
    assert missing == ['R025','R035','R038','R049','R059']
    injected = sorted(r for r in fired if all(c.startswith('metadata:') for c in core['coverage'][r]))
    assert injected==['R048','R055','R056','R057','R058','R060','R064']
    edges = [read(n) for n in ('edge_29_0_0.json','edge_current.json','edge_30_17_0.json')]
    edge_summary = []
    for edge in edges:
        assert edge['count']==len(edge['rows'])==324
        assert edge['groups']=={'kfn':252,'real-object':30,'ordinary-error':35,'marker-literal':3,'control':4}
        leaks=[r for r in edge['rows'] if r['known_fidelity_failures'] and E999 not in r['fired']]
        assert len(leaks)==60
        assert all(r['all_business_rules_disabled']=={'passed':True,'fired':[]} for r in leaks)
        assert edge['kfn_without_e999']==edges[0]['kfn_without_e999']
        edge_summary.append({'sqlglot':edge['sqlglot'],'inputs':324,'kfn_without_e999':len(leaks)})
    assert len(diff['edge_changes'])==160
    assert Counter('kfn' if r['id'].startswith('kfn-path:') else 'ordinary' for r in diff['edge_changes'])=={'kfn':156,'ordinary':4}
    steps=read('browser_steps.json')
    assert len(steps)==len({r['id'] for r in steps})==64
    for step in steps:
        assert (HERE/(step['id']+'.jpg')).read_bytes().startswith(b'\xff\xd8\xff')
        assert (HERE/(step['id']+'.txt')).stat().st_size>0
    http=read('http_results.json')
    assert len(http)==614
    statuses=Counter(r['status'] for r in http)
    assert statuses=={200:611,403:3}
    mismatches=[r['id'] for r in http if r.get('engine_equals_http') is False]
    assert len(mismatches)==17
    assert read('service_current.json')==read('service_baseline.json')
    assert len(read('service_current.json'))==17
    assert [r['status'] for r in read('explain_context_results.json')]==[200,500,200]
    suites=list(ET.parse(HERE/'full_regression.xml').getroot().iter('testsuite'))
    assert sum(int(s.get('tests',0)) for s in suites)==1384
    assert all(sum(int(s.get(k,0)) for s in suites)==0 for k in ('failures','errors','skipped'))
    old_suites=list(ET.parse(HERE/'full_regression_auth_override.xml').getroot().iter('testsuite'))
    assert sum(int(s.get('failures',0)) for s in old_suites)==4
    matrix=(HERE/'implementation_matrix.txt').read_text(encoding='utf-8')
    assert matrix.count('680 passed')==3
    assert '71 passed' in matrix and '1384 passed' in matrix
    assert 'RESULT PASS mode=implementation versions=29.0.0,30.14.0,30.17.0' in matrix
    assert (HERE/'ops_report.pdf').read_bytes().startswith(b'%PDF-')
    for page in (1,2):
        assert (HERE/f'ops_report-{page}.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert read('gateway_css_leak.json')=={'before':{'bodyPadding':'0px','styleCount':0},'afterClose':{'bodyPadding':'20px','styleCount':1}}
    assert not read('download_attempt.json')['event'] and not read('pdf_download_attempt.json')['event']
    # Generate ledgers from this round's records, never copy last round's verdict.
    ledger=['# 第二轮119条核心规则覆盖账本（智能体O）','',
            '119定义与6957499相同；114命中不代表所有边界正确。107条非注入元数据输入、7条仅合成元数据、5条缺口。详见主报告§3.3。','',
            '| 规则 | 分类 | 本轮实际证据 | 首个样例 |','|---|---|---|---|']
    for rule in core['rules']:
        rid=rule['rule_id']; ids=core['coverage'].get(rid,[])
        status='未触发：既有缺口，不能算通过' if not ids else ('仅显式合成元数据' if rid in injected else '非注入元数据输入触发')
        ledger.append(f"| {rid} | {rule['category']} | {status} | {ids[0] if ids else '—'} |")
    (HERE/'rule_coverage_119.md').write_text('\n'.join(ledger)+'\n',encoding='utf-8',newline='\n')
    modules=Counter(c.get('classname','').split('.')[1] if c.get('classname','').startswith('tests.') else c.get('classname','') for s in suites for c in s.iter('testcase'))
    write('regression_modules.json',{'total':sum(modules.values()),'test_modules_or_classes':dict(sorted(modules.items()))})
    for p in HERE.glob('*.json'):
        json.loads(p.read_text(encoding='utf-8'))
    for p in HERE.glob('*.py'):
        ast.parse(p.read_text(encoding='utf-8'),filename=str(p))
    sensitive=re.compile(r'Bearer\s+[A-Za-z0-9_.-]{20,}|eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]+\.|[\"\x27](?:password_hash|salt)[\"\x27]\s*:\s*[\"\x27][^\"\x27]+',re.I)
    for p in [REPORT]+[p for p in HERE.iterdir() if p.suffix in ('.md','.txt','.json','.py','.html','.sql','.xml')]:
        content=p.read_text(encoding='utf-8')
        assert not sensitive.search(content),f'Possible credential {p.name}'
        for value in re.findall(r'"password"\s*:\s*"([^"\s]+)"',content):
            assert set(value)<={'*'},f'Unmasked password {p.name}'
    for p in (REPORT,HERE/'README.md',HERE/'rule_coverage_119.md'):
        for target in re.findall(r'\]\(([^)]+)\)',p.read_text(encoding='utf-8')):
            if target.startswith(('https://','http://','#')):
                continue
            resolved=(p.parent/target.split('#',1)[0]).resolve()
            assert resolved.exists() or resolved in {HERE/'validation.json',HERE/'evidence_manifest.json'},(p.name,target)
    summary={'kind':'offline_evidence_validation_not_product_retest',
             'validated_at':datetime.now(timezone.utc).isoformat(),
             'tested_commit':diff['tested_commit'],'baseline_commit':diff['before_commit'],
             'browser_checkpoint_pairs':64,'registered_rules':119,'fired_rules':114,
             'injected_metadata_only_rules':injected,'unfired_rules':missing,
             'core_inputs':1000,'changed_rule_id_sets':70,'unchanged_rule_id_sets':930,
             'independent_oracle_failures':dict(failures),'edge_versions':edge_summary,
             'http_first_batch':len(http),'http_status_counts':dict(statuses),
             'http_engine_mismatches':17,'service_differences_same_as_baseline':True,
             'later_explain_statuses':[200,500,200],'pytest_passed':1384,
             'matrix_passed':True,'links_valid':True,'credential_pattern_scan':'no_matches',
             'unclosed_defects':{'BLOCK':1,'MAJOR':6,'MINOR':1},
             'verdict':'NO_GO; mandatory E999 path still incomplete; see report for scope and other findings'}
    write('validation.json',summary)
    files=[{'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(HERE.iterdir()) if p.is_file() and p.name!='evidence_manifest.json']
    write('evidence_manifest.json',{'tested_commit':diff['tested_commit'],'files':files,
          'report':{'path':REPORT.name,'sha256':sha(REPORT)},'note':'Excludes manifest itself; preserves raw bytes via directory-scoped .gitattributes.'})
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print('FILES',len(files),'BYTES',sum(r['bytes'] for r in files))


if __name__=='__main__':
    main()
