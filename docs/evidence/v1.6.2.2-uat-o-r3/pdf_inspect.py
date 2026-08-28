"""Read-only PDF content inspection; exports are obtained from the application."""
from pathlib import Path
import json
from pypdf import PdfReader
HERE=Path(__file__).resolve().parent
rows=[]
for path in HERE.glob('*.pdf'):
    reader=PdfReader(path)
    text='\n\n'.join(p.extract_text() or '' for p in reader.pages)
    (HERE/(path.stem+'_pdf.txt')).write_text(text,encoding='utf-8')
    rows.append({'file':path.name,'pages':len(reader.pages),'bytes':path.stat().st_size,'text_chars':len(text),'has_gateway_record':'50.00%' in text,'has_false_excellent':'索引健康度极佳' in text,'unassessed':'未评估' in text})
(HERE/'pdf_checks.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(rows,ensure_ascii=False))
