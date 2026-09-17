"""Supplementary HTTP evidence, explicitly distinct from browser operations."""
from pathlib import Path
import json
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
RT = ROOT / 'data/reports/qc_o_1640'
sys.path.insert(0, str(ROOT / 'docs/evidence/v1.6.4.0-uat-d'))
import uat_d40_api as harness
harness.WEB = 'http://127.0.0.1:8026'
harness.RUNTIME = RT
harness.EVID = HERE
Api = harness.Api
records = []

def call(api, method, path, **kwargs):
    r = api.req(method, path, **kwargs)
    records.append(r)
    return r

def login(name='qc_o_1640'):
    a = Api(name)
    a.login()
    return a

def save(name):
    harness.save(name, records)
    print(json.dumps([{'method':r['method'],'path':r['path'],'status':r['status']} for r in records]))

def poll(a, tid):
    for _ in range(50):
        r = call(a, 'GET', '/api/v1/copilot/turns/'+tid)
        if r.get('body',{}).get('terminal'):
            return r
        time.sleep(1)
    raise RuntimeError('turn did not terminate within 50 seconds')

if __name__ == '__main__':
    mode = sys.argv[1]
    a = login()
    if mode == 'providers':
        for label, ep in [('QC1 Controlled Main','ep-uat-a'),('QC1 Controlled Backup','ep-uat-b')]:
            call(a,'POST','/api/v1/copilot-admin/providers',json={
                'name':label,'endpoint_id':ep,'model_id':'qc1-synthetic',
                'auth_mode':'BEARER','secret_action':'REPLACE','secret':'test-key',
                'capabilities':{'context_tokens':32768,'max_output_field':'max_tokens',
                                'supports_json_object':True,'supports_store_false':True}})
        save('api-provider-fixture')
    elif mode in ('disable','restore'):
        s=call(a,'GET','/api/v1/copilot-admin/settings')['body']
        if mode=='disable':
            (RT/'before-disable-settings.json').write_text(json.dumps(s),encoding='utf-8')
        enabled=False if mode=='disable' else json.loads((RT/'before-disable-settings.json').read_text())['settings'].get('enabled',True)
        call(a,'PUT','/api/v1/copilot-admin/settings',json={'enabled':enabled,'expected_revision':s['config_revision']})
        call(a,'GET','/api/v1/copilot/capabilities')
        call(a,'GET','/api/v1/copilot/help?query=R003&page_key=audit-sql')
        save('api-'+mode)
    elif mode == 'inspect':
        for p in ['capabilities','sessions?limit=20','help?query=R003']:
            call(a,'GET','/api/v1/copilot/'+p)
        for p in ['providers','routes','grants','health']:
            call(a,'GET','/api/v1/copilot-admin/'+p)
        sessions=records[1].get('body',{}).get('items',[])
        for s in sessions:
            sid=s['session_id']
            tr=call(a,'GET',f'/api/v1/copilot/sessions/{sid}/turns?limit=20')
            for t in tr.get('body',{}).get('items',[]):
                call(a,'GET',f"/api/v1/copilot/turns/{t['turn_id']}/result")
        save('api-inspect')
    elif mode == 'conversation':
        s=call(a,'POST','/api/v1/copilot/sessions',json={'scope_kind':'GLOBAL_HELP','instance_type':'unknown','page_key':'audit-sql'})
        sid=s['body']['session_id']
        for question in ('本项目的 R003 规则是什么？','接着解释它与表注释的关系。','如何在原即时审核中验证这个建议？'):
            session=call(a,'GET','/api/v1/copilot/sessions/'+sid)['body']
            pr=call(a,'POST',f'/api/v1/copilot/sessions/{sid}/previews',json={
                'expected_session_revision':session['revision'],'scene':'RULE_EXPLAIN',
                'page_key':'audit-sql','question':question,'source_refs':[{'kind':'rule','rule_ids':['R003']}]})
            p=pr['body']
            if 'preview_id' not in p:
                save('api-conversation-debug')
                raise RuntimeError(str(p))
            r=call(a,'POST',f'/api/v1/copilot/sessions/{sid}/turns',json={
                'client_request_id':uuid.uuid4().hex,'preview_id':p['preview_id'],'snapshot_hash':p['snapshot_hash'],
                'expected_session_revision':session['revision'],'confirm_data_use':True})
            tid=r['body']['turn_id']; poll(a,tid)
            call(a,'GET',f'/api/v1/copilot/turns/{tid}/result')
        dev=login('qc_o_1640dev')
        call(dev,'GET',f'/api/v1/copilot/turns/{tid}/result')
        call(dev,'GET',f'/api/v1/copilot/sessions/{sid}')
        (HERE/'conversation-fixture-ids.json').write_text(json.dumps({'session_id':sid,'turn_id':tid}),encoding='utf-8')
        save('api-conversation')
    elif mode == 'revocation':
        s=call(a,'POST','/api/v1/copilot/sessions',json={'scope_kind':'INSTANCE','connection_id':'q40-dist','database':'qc_o_1640_dist','instance_type':'distributed'})['body']
        sid=s['session_id']
        s=call(a,'GET','/api/v1/copilot/sessions/'+sid)['body']
        p=call(a,'POST',f'/api/v1/copilot/sessions/{sid}/previews',json={
            'expected_session_revision':s['revision'],'scene':'AUDIT_EXPLAIN','question':'解释本次合成元数据审核的结果。',
            'source_refs':[{'kind':'metadata_job','job_id':'7a5d09ca4583444d8e6f12a3b747dd2e'}]})['body']
        if 'preview_id' not in p:
            save('api-revocation');raise RuntimeError(str(p))
        tid=call(a,'POST',f'/api/v1/copilot/sessions/{sid}/turns',json={
            'client_request_id':uuid.uuid4().hex,'preview_id':p['preview_id'],'snapshot_hash':p['snapshot_hash'],
            'expected_session_revision':s['revision'],'confirm_data_use':True})['body']['turn_id']
        poll(a,tid)
        call(a,'GET',f'/api/v1/copilot/turns/{tid}/result')
        call(a,'PUT','/api/v1/copilot-admin/grants',json={'username':'qc_o_1640','connection_id':'q40-dist','intent':'REVOKE'})
        call(a,'GET',f'/api/v1/copilot/turns/{tid}/result')
        call(a,'POST',f'/api/v1/copilot/turns/{tid}/actions/resolve',json={'action_id':'A1','view_session_id':sid})
        call(a,'GET',f'/api/v1/copilot/turns/{tid}/export.html')
        call(a,'PUT','/api/v1/copilot-admin/grants',json={'username':'qc_o_1640','connection_id':'q40-dist',
            'intent':'REQUEST','approval_ref':'QC1-SYNTHETIC-ONLY','allow_schema_identifiers':True,'identifier_approval_ref':'QC1-SYNTHETIC-IDENTIFIERS'})
        gs=call(a,'GET','/api/v1/copilot-admin/grants?limit=50')['body']['items']
        b=login('qc_o_1640b')
        for g in gs:
            if g['username']=='qc_o_1640' and g['connection_id']=='q40-dist':
                call(b,'POST','/api/v1/copilot-admin/grants/approve',json={'subject_id':g['subject_id'],'connection_id':g['connection_id'],'expected_revision':g['revision']})
        save('api-revocation')
    elif mode == 'routes-grants':
        ps=call(a,'GET','/api/v1/copilot-admin/providers')['body']['items']
        primary=next(p for p in ps if p['name']=='QC1 Controlled Main')
        for scene in ('USAGE_HELP','RULE_EXPLAIN','SQL_ADVISE','AUDIT_EXPLAIN','JOB_TROUBLESHOOT',
                      'SLOW_EXPLAIN','COMPARE_EXPLAIN','TABLETYPE_EXPLAIN','GATEWAY_EXPLAIN','DIAGNOSTIC_HELP'):
            call(a,'PUT','/api/v1/copilot-admin/routes/'+scene,json={
                'primary_provider_id':primary['id'],'fallback_provider_id':None,
                'privacy_profile':'INTERNAL_REDACTED','expected_revision':0})
        b=login('qc_o_1640b')
        for target in ('qc_o_1640','qc_o_1640dev','qc_o_1640dev2'):
            call(a,'PUT','/api/v1/copilot-admin/grants',json={
                'username':target,'connection_id':'q40-dist','intent':'REQUEST',
                'approval_ref':'QC1-SYNTHETIC-ONLY','allow_schema_identifiers':True,
                'identifier_approval_ref':'QC1-SYNTHETIC-IDENTIFIERS'})
        grants=call(a,'GET','/api/v1/copilot-admin/grants?limit=50')['body']['items']
        for g in grants:
            if g['approval_state']=='PENDING':
                call(b,'POST','/api/v1/copilot-admin/grants/approve',json={
                    'subject_id':g['subject_id'],'connection_id':g['connection_id'],
                    'expected_revision':g['revision']})
        save('api-route-grant-fixture')
