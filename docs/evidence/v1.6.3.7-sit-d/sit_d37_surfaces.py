"""智能体D / v1.6.3.7 SIT：命名在下载与报告抬头消费面的端到端核验（HTTP）。"""
import json
import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[3]
os.environ.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                   "SQLCHECK_DB_NAME": "uat_d_1636_meta", "AUTH_ENABLED": "true"})
sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent
WEB = "http://127.0.0.1:8025"
RUNTIME = ROOT / "data/reports/uat_d_1636"
PW = (RUNTIME / "admin.password").read_text(encoding="utf-8").strip()
ACCOUNT = "uat_d_1636"


def tok():
    r = requests.post(f"{WEB}/api/v1/auth/login",
                      json={"username": ACCOUNT, "password": PW}, timeout=15)
    r.raise_for_status()
    return r.json()["token"]


t = tok()
h = {"Authorization": "Bearer " + t}
lst = requests.get(f"{WEB}/api/v1/audit/extracted-reports?limit=50", headers=h, timeout=30).json()
reports = lst.get("reports") or []
target = next((r for r in reports
               if re.match(r"^extracted_.+_\d{8}_\d{6}\.sql$", r.get("source") or "")), None)
out = {"total_reports": len(reports),
       "newest_three": [{"id": r["id"], "source": r["source"], "created_at": r["created_at"]}
                        for r in reports[:3]],
       "target_report": target and {"id": target["id"], "source": target["source"],
                                    "created_at": target["created_at"]}}
if target:
    rid = target["id"]
    src = target["source"]
    # ① 历史 SQL 下载：Content-Disposition 文件名
    r1 = requests.get(f"{WEB}/api/v1/audit/report/{rid}/sql?access_token={t}", timeout=60)
    cd = r1.headers.get("Content-Disposition", "")
    out["sql_download"] = {"status": r1.status_code,
                           "content_disposition": cd,
                           "filename_equals_source": (src in cd),
                           "body_bytes": len(r1.content)}
    # ② 历史 HTML 报告：抬头是否展示同一文件名 + 报告ID
    r2 = requests.get(f"{WEB}/api/v1/audit/report/{rid}/html?access_token={t}", timeout=60)
    html = r2.text
    out["html_report"] = {"status": r2.status_code, "bytes": len(html.encode("utf-8")),
                          "shows_source": src in html,
                          "shows_jobid_form": bool(re.search(r"extracted_\w+_[0-9a-f]{8}\.sql", html))}
    # ③ 截图用：报告ID 列在接口里是否返回
    out["report_id_field_present"] = "id" in (target or {})
out["verdict"] = ("PASS 下载与报告抬头均使用还原后的文件名"
                  if out.get("sql_download", {}).get("filename_equals_source")
                  and out.get("html_report", {}).get("shows_source") else "FAIL")
(HERE / "sit-surfaces.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
