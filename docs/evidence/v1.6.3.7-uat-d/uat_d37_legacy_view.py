"""UAT：历史记录列表"存量 vs 新规"取证——统计 UUID 形态存量行及其显示时间。

用法：python uat_d37_legacy_view.py
输出：uat-legacy-view.json
"""
import json
import re
from pathlib import Path

from backend.services.database import _get_connection

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ART = ROOT / "data/reports/uat_d_1636/reports/metadata-audit"

TS_FORM = re.compile(r"^extracted_.+_\d{8}_\d{6}\.sql$")
UUID_FORM = re.compile(r"^extracted_.+_[0-9a-f]{8}\.sql$")


def classify(src):
    if TS_FORM.match(src or ""):
        return "新规-时间戳形态"
    if UUID_FORM.match(src or ""):
        return "存量-UUID形态"
    return "其它/遗留"


cx = _get_connection()
rows = cx.execute(
    "SELECT id, db_name, source, created_at FROM audit_history "
    "WHERE audit_type='extracted_schema' ORDER BY id DESC LIMIT 40").fetchall()
cx.close()

out = {"rows": [], "counts": {}}
for r in rows:
    rid, db, src, created = r["id"], r["db_name"], r["source"], str(r["created_at"])
    kind = classify(src)
    out["counts"][kind] = out["counts"].get(kind, 0) + 1
    out["rows"].append({"id": rid, "db_name": db, "source": src,
                        "created_at": created, "kind": kind,
                        "uuid_form": bool(UUID_FORM.match(src or "")),
                        "ts_form": bool(TS_FORM.match(src or ""))})

# 每个 job 产物目录内 schema.sql 的文件头"提取日期"，用于核对时间基
headers = {}
for d in ART.iterdir():
    f = d / "schema.sql"
    if f.exists():
        try:
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines()[:8]:
                if "提取日期" in line:
                    headers[d.name[:8]] = line.split("提取日期:")[-1].strip()
                    break
        except OSError:
            pass
out["artifact_header_dates"] = headers
out["newest_20_are_all_ts_form"] = all(x["ts_form"] for x in out["rows"][:20])
(HERE / "uat-legacy-view.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out["counts"], ensure_ascii=False))
for x in out["rows"][:24]:
    print(f"#{x['id']:<5} {x['created_at']:<20} {x['kind']:<14} {x['source']}")
