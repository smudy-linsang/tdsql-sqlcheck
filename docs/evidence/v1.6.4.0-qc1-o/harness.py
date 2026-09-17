"""Run the unchanged product against QC1-only resources (web/runner/metadata/gw)."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
RT = ROOT / 'data/reports/qc_o_1640'
D = ROOT / 'docs/evidence/v1.6.4.0-uat-d'
sys.path[:0] = [str(RT / 'trust'), str(ROOT), str(D)]
import _boot  # noqa

os.environ.update({
    'SQLCHECK_DB_HOST': '127.0.0.1', 'SQLCHECK_DB_PORT': '13306',
    'SQLCHECK_DB_USER': 'root', 'SQLCHECK_DB_PASSWORD': 'tdsql_test_2024',
    'SQLCHECK_DB_NAME': 'qc_o_1640_meta',
    'AUTH_ENABLED': 'true', 'SCHEDULER_ENABLED': 'false', 'DATA_MASKING_ENABLED': 'false',
    'REPORT_OUTPUT_DIR': str(RT / 'reports'), 'TDSQL_SQLCHECK_DIR': str(RT),
    'COPILOT_ENABLED': 'true', 'COPILOT_ALLOW_SCHEMA_IDENTIFIERS': 'true',
    'COPILOT_KEYRING_FILE': str(RT / 'copilot-keyring.json'),
    'COPILOT_ENDPOINTS_FILE': str(RT / 'copilot-endpoints.json'),
    'UAT_D40_RUNTIME': str(RT), 'UAT_D40_GW_HOST': (RT / 'gateway-host.txt').read_text().strip() if (RT / 'gateway-host.txt').exists() else '172.16.4.16',
    'PYTHONIOENCODING': 'utf-8',
    'PYTHONPATH': os.pathsep.join([str(RT / 'trust'), str(ROOT)] + [str(p) for p in _boot.CANDIDATES]),
})

if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'web':
        import uvicorn
        uvicorn.run('backend.main:app', host='127.0.0.1', port=8026, log_level='info')
    elif mode == 'runner':
        from backend.workers.copilot_runner import main
        main()
    elif mode == 'metadata':
        from backend.workers.metadata_runner import main
        main()
    elif mode == 'gw':
        sys.argv = [sys.argv[0], '8453', '8454', '8455']
        gateway_source = (D / 'mock_llm_gateway.py').read_text(encoding='utf-8')
        gateway_source = gateway_source.replace(
            'answer = json.loads(json.dumps(ANSWER_TEMPLATE))',
            "answer = json.loads(json.dumps(ANSWER_TEMPLATE))\n"
            "        if (RUNTIME / 'candidate-mode.txt').exists():\n"
            "            answer['sql_candidates'] = [{'sql': 'SELECT * FROM qc_candidate_missing;', 'reason': 'QC1 synthetic candidate for text-validation and editor-safety tests', 'evidence_ids': []}]\n"
        )
        exec(compile(gateway_source, str(D / 'mock_llm_gateway.py'), 'exec'),
             {'__name__': '__main__', '__file__': str(D / 'mock_llm_gateway.py')})
    else:
        raise SystemExit('unknown fixture mode')
