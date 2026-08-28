"""Derive the round-three ledger/counts from captured evidence; no server access."""
from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET
HERE=Path(__file__).resolve().parent
def read(n):return json.loads((HERE/n).read_text(encoding='utf-8'))
core=read('rule_probe_current.json'); extra=read('supplemental_core.json')[:5]
coverage={k:list(v) for k,v in core['coverage'].items() if k.startswith('R')}
for row in extra:
    for rid in row['fired']:
        if rid.startswith('R'):coverage.setdefault(rid,[]).append('supplemental:'+row['id'])
rules=[r['rule_id'] for r in core['rules']]
missing=sorted(set(rules)-set(coverage))
diff=read('round3_diff.json'); edge=read('edge_current.json'); http=read('http_results.json')
suites=ET.parse(HERE/'full_regression.xml').getroot().findall('testsuite')
summary={'tested_commit':diff['tested_commit'],'rule_count':len(rules),'rules_equal':diff['rules_equal'],
 'fired_rule_count':len(coverage),'unverified_rules':missing,'corpus_count':core['case_count'],'corpus_oracle_failures':core['failures'],
 'corpus_hitset_changes':len(diff['changes']),'edge_cases':edge['count'],'edge_changes':dict(Counter(r['id'].split(':')[0] for r in diff['edge_changes'])),
 'edge_kfn':edge['groups']['kfn'],'edge_kfn_without_e999':edge['kfn_without_e999'],
 'http_count':len(http),'http_statuses':dict(Counter(r['status'] for r in http)),
 'http_engine_differences':sum(r.get('engine_equals_http') is False for r in http),
 'service_same_as_before':read('service_current.json')==read('service_baseline.json'),
 'pytest':{key:sum(int(s.get(key,'0')) for s in suites) for key in ('tests','failures','errors','skipped')},
 'browser_evidence_groups':len(read('browser_steps.json')),
 'load':{v:{k:len(read('load_'+v+'.json')[k]) for k in ('missing_r042','false_pass')} for v in ('baseline','29.0.0','30.14.0','30.17.0')}}
(HERE/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# 119 条核心规则实测账本（第三轮）','','被测提交 `1596e8b`；主版本 sqlglot 30.14.0。依据 `rule_probe_current.json` 1000 条和 `supplemental_core.json` 前五条。',
 '',f'注册 {len(rules)} 条，至少命中 {len(coverage)} 条；未证明有效：'+', '.join(missing)+'。命中过不代表全边界通过，R042 本轮已有反例。',
 '', '107 条有非注入元数据输入命中；7 条原有元数据分支加本轮 R035/R059，共9条仅用合成上下文验证。真实 TDSQL 在线元数据供给不据此签字。',
 '', '| 规则 | 本轮命中次数 | 首个证据 ID | 验收边界 |','|---|---:|---|---|']
meta={'R048','R055','R056','R057','R058','R060','R064','R035','R059'}
gaps={'R025':'ALTER 动作供给未覆盖；本例未触发','R038':'raw_type 未包含 AUTO_INCREMENT；本例未触发','R049':'当前实现占位返回 None'}
for rid in rules:
    ids=coverage.get(rid,[])
    note=gaps.get(rid,'合成元数据分支，未验证真实在线供给' if rid in meta else '有样本命中，不等于全语义证明')
    if rid=='R042':note='BLOCK：有正例命中，但 # 注释中的引号造成新增漏报/误通过'
    lines.append(f"| {rid} | {len(ids)} | {ids[0] if ids else '—'} | {note} |")
(HERE/'rule_coverage_119.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
