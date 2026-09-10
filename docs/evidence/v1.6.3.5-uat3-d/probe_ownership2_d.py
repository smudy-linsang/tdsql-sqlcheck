"""智能体D / v1.6.3.5 UAT：同角色跨用户所有权（404 不泄漏存在性）正确探针。

前置：先用初始口令登录 → 完成首登强制改密 → 再以新口令复登，业务接口才可用。
"""
import json
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parents[2] / "data/reports/uat_d_1635_r3"
ADMIN_PW = (RUNTIME / "admin.password").read_text(encoding="utf-8").strip()
WEB = "http://127.0.0.1:8015"
NEW_PW = "Uat3!D" + ADMIN_PW[-6:]


def login(u, p):
    return requests.post(f"{WEB}/api/v1/auth/login",
                         json={"username": u, "password": p}, timeout=15)


def admin_headers():
    return {"Authorization": "Bearer " + login("uat_d_1635_r3", ADMIN_PW).json()["token"]}


def ensure_user(uname, role, pw):
    st, body = 0, ""
    r = requests.post(f"{WEB}/api/v1/auth/users", headers=admin_headers(), timeout=20,
                      json={"username": uname, "display_name": "D-UAT3-" + role,
                            "role": role, "password": pw})
    return r.status_code, r.text[:160]


out = {"web": WEB, "new_password_used": "见 RUNTIME/owner.password（不入库）"}
target = requests.get(f"{WEB}/api/v1/audit/metadata-jobs?limit=5",
                      headers=admin_headers(), timeout=20).json()["items"][0]["job_id"]
out["target_job"] = target

owner_pw = NEW_PW
(RUNTIME / "owner.password").write_text(owner_pw, encoding="utf-8")

for uname, role in (("uat_d_dev2", "developer"), ("uat_d_dba2", "dba")):
    rec = {}
    r = login(uname, owner_pw)
    if not r.ok:
        rec["create"] = ensure_user(uname, role, owner_pw)
        r = login(uname, owner_pw)
    rec["login_status"] = r.status_code
    if r.ok:
        tok = r.json()["token"]
        h = {"Authorization": "Bearer " + tok}
        # 首登强制改密（若需要）
        ch = requests.post(f"{WEB}/api/v1/auth/change-password", headers=h, timeout=20,
                           json={"old_password": owner_pw, "new_password": owner_pw + "x"})
        rec["change_password"] = ch.status_code
        if ch.status_code == 200:
            owner_pw2 = owner_pw + "x"
            (RUNTIME / "owner.password").write_text(owner_pw2, encoding="utf-8")
            r = login(uname, owner_pw2)
            rec["relogin"] = r.status_code
            tok = r.json()["token"]
            h = {"Authorization": "Bearer " + tok}
            owner_pw = owner_pw2
        m = requests.get(f"{WEB}/api/v1/auth/visible-menus", headers=h, timeout=15)
        rec["menus"] = {"status": m.status_code,
                        "has_online_metadata": ("schema-extractor-audit" in m.text)}
        rec["list_own"] = requests.get(f"{WEB}/api/v1/audit/metadata-jobs",
                                       headers=h, timeout=20).status_code
        for label, url, method in (
                ("get_others_job", f"/api/v1/audit/metadata-jobs/{target}", "GET"),
                ("get_others_results", f"/api/v1/audit/metadata-jobs/{target}/results", "GET"),
                ("get_others_sql", f"/api/v1/audit/metadata-jobs/{target}/sql", "GET"),
                ("get_others_html", f"/api/v1/audit/metadata-jobs/{target}/html", "GET"),
                ("cancel_others_job", f"/api/v1/audit/metadata-jobs/{target}/cancel", "POST")):
            rr = requests.request(method, WEB + url, headers=h, timeout=20)
            rec[label] = {"status": rr.status_code, "body": rr.text[:120]}
    out[role] = rec

dest = HERE / "probe-ownership2.json"
dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
