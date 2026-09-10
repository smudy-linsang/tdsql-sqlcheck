"""智能体D / v1.6.3.5 UAT：非 admin 角色对在线元数据审核接口的可达性与拒绝原因。"""
import json
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parents[2] / "data/reports/uat_d_1635_r3"
PW = (RUNTIME / "admin.password").read_text(encoding="utf-8").strip()
WEB = "http://127.0.0.1:8015"

users = [("uat_d_viewer", "developer", "Vw!" + PW[-8:]),
         ("uat_d_dba", "dba", "Db!" + PW[-8:])]
out = {}
for uname, role, pw in users:
    r = requests.post(f"{WEB}/api/v1/auth/login",
                      json={"username": uname, "password": pw}, timeout=15)
    rec = {"login": r.status_code}
    if r.ok:
        h = {"Authorization": "Bearer " + r.json()["token"]}
        m = requests.get(f"{WEB}/api/v1/auth/visible-menus", headers=h, timeout=15)
        rec["menus_status"] = m.status_code
        rec["menus"] = m.json() if m.ok else m.text[:200]
        for path in ("/api/v1/audit/metadata-jobs",
                     "/api/v1/audit/metadata-jobs?limit=5",
                     "/api/v1/audit/extracted-reports"):
            rr = requests.get(WEB + path, headers=h, timeout=20)
            rec[path] = {"status": rr.status_code, "body": rr.text[:220]}
    out[role] = rec

dest = HERE / "probe-nonadmin-access.json"
dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
