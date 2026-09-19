# -*- coding: utf-8 -*-
import os, sys
from pathlib import Path

ROOT = Path(r"c:\TDSQL_SQLCHECK\TDSQL-SQLCheck")
RT = ROOT / 'data/reports/qc_o_1640'
D = ROOT / 'docs/evidence/v1.6.4.0-uat-d'
sys.path[:0] = [str(RT / 'trust'), str(ROOT), str(D)]
import _boot

mode = sys.argv[1]
if mode == 'web':
    import uvicorn
    uvicorn.run('backend.main:app', host='0.0.0.0', port=8000, log_level='info')
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
