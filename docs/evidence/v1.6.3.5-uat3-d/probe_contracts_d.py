"""智能体D / v1.6.3.5 第三轮 UAT：独立契约探针（不改产品代码）。

子命令：
  cancel_elapsed   已取消任务 finished_at 缺失 → elapsed 是否随查询时间膨胀
  ownership        同角色跨用户访问所有权（404/403）与列表隔离
  deploy_mutation  对 tests/test_v1635_deploy_contract.py 做真实变异，验证断言有效性
"""
import importlib.util
import json
import sys
import time
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNTIME = ROOT / "data/reports/uat_d_1635_r3"
ACCOUNT = "uat_d_1635_r3"
PW = (RUNTIME / "admin.password").read_text(encoding="utf-8").strip()
WEB = "http://127.0.0.1:8015"


def _tok():
    import requests
    return requests.post(f"{WEB}/api/v1/auth/login",
                         json={"username": ACCOUNT, "password": PW},
                         timeout=15).json()["token"]


def cancel_elapsed():
    import requests
    h = {"Authorization": "Bearer " + _tok()}

    def snap():
        d = requests.get(f"{WEB}/api/v1/audit/metadata-jobs?limit=50",
                         headers=h, timeout=20).json()
        return {j["job_id"][:8]: {"state": j["state"],
                                  "elapsed": j["elapsed_seconds"],
                                  "finished_at": j.get("finished_at")}
                for j in d["items"] if j["state"] in ("SUCCEEDED", "FAILED", "CANCELLED")}

    a = snap()
    time.sleep(75)
    b = snap()
    rows = []
    for k, v in a.items():
        after = b.get(k, {})
        rows.append({"job": k, "state": v["state"], "finished_at": v["finished_at"],
                     "elapsed_t0": v["elapsed"], "elapsed_t75": after.get("elapsed"),
                     "grew": (after.get("elapsed") or 0) - (v["elapsed"] or 0)})
    out = {"samples": rows,
           "cancelled_grow": [r for r in rows if r["state"] == "CANCELLED"]}
    (HERE / "probe-cancel-elapsed.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def ownership():
    import requests
    admin = {"Authorization": "Bearer " + _tok()}
    jobs = requests.get(f"{WEB}/api/v1/audit/metadata-jobs?limit=5",
                        headers=admin, timeout=20).json()["items"]
    target = jobs[0]["job_id"]
    # 同角色（dba）另一用户，具备该菜单权限 → 应走所有权 404 而不是 403
    uname, pw2 = "uat_d_dba", "Db!" + PW[-8:]
    st, _ = _post_user(admin, uname, pw2, "dba")
    r = requests.post(f"{WEB}/api/v1/auth/login",
                      json={"username": uname, "password": pw2}, timeout=15)
    out = {"target_job": target, "dba_user_create_status": st,
           "dba_login": r.status_code}
    if r.ok:
        dh = {"Authorization": "Bearer " + r.json()["token"]}
        out["dba_get_admin_job"] = requests.get(
            f"{WEB}/api/v1/audit/metadata-jobs/{target}", headers=dh, timeout=20).status_code
        out["dba_get_admin_results"] = requests.get(
            f"{WEB}/api/v1/audit/metadata-jobs/{target}/results", headers=dh,
            timeout=20).status_code
        out["dba_cancel_admin_job"] = requests.post(
            f"{WEB}/api/v1/audit/metadata-jobs/{target}/cancel", headers=dh,
            timeout=20).status_code
        lst = requests.get(f"{WEB}/api/v1/audit/metadata-jobs", headers=dh, timeout=20)
        out["dba_list_status"] = lst.status_code
        try:
            out["dba_list_items"] = len(lst.json().get("items", []))
        except ValueError:
            out["dba_list_items"] = None
        out["dba_download_admin_sql"] = requests.get(
            f"{WEB}/api/v1/audit/metadata-jobs/{target}/sql", headers=dh,
            timeout=20).status_code
    anon = requests.get(f"{WEB}/api/v1/audit/metadata-jobs", timeout=15)
    out["anon_list"] = anon.status_code
    out["anon_job"] = requests.get(f"{WEB}/api/v1/audit/metadata-jobs/{target}",
                                   timeout=15).status_code
    (HERE / "probe-ownership.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def _post_user(headers, username, password, role):
    import requests
    r = requests.post(f"{WEB}/api/v1/auth/users", headers=headers, timeout=20,
                      json={"username": username, "display_name": "D-UAT3-" + role,
                            "role": role, "password": password})
    return r.status_code, r.text[:200]


# ── 部署契约变异 ─────────────────────────────────────────────────────────
def _load_contract():
    spec = importlib.util.spec_from_file_location(
        "d_deploy_contract", ROOT / "tests/test_v1635_deploy_contract.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mutate(text, kind, needle):
    if kind == "delete":
        return "\n".join(l for l in text.split("\n") if needle not in l)
    if kind == "comment":
        return "\n".join(("#" + l if needle in l and not l.strip().startswith("#") else l)
                         for l in text.split("\n"))
    if kind == "move_after_web":
        lines = text.split("\n")
        idx = next((i for i, l in enumerate(lines) if needle in l), None)
        if idx is None:
            return text
        line = lines.pop(idx)
        widx = next((i for i, l in enumerate(lines)
                     if "systemctl restart tdsql-sqlcheck" in l), len(lines) - 1)
        lines.insert(widx + 1, line)
        return "\n".join(lines)
    if kind == "strip_to_install_and_pkill":
        out = []
        for l in text.split("\n"):
            if needle in l:
                out.append('echo "[INFO] runner unit 已安装"')
                out.append('pkill -f "backend.workers.metadata_runner" || true')
            else:
                out.append(l)
        return "\n".join(out)
    raise ValueError(kind)


def deploy_mutation():
    mod = _load_contract()
    dep = ROOT / "deploy"
    cases = []
    scripts = ["install.sh", "upgrade_incremental.sh", "apply_patch.sh"]
    needle = "systemctl restart tdsql-metadata-runner"
    for script in scripts:
        original = (dep / script).read_text(encoding="utf-8")
        for kind in ("delete", "comment", "move_after_web", "strip_to_install_and_pkill"):
            mutated = _mutate(original, kind, needle)
            real = Path.read_text

            def fake(self, *a, **kw):
                if self.name == script:
                    return mutated
                return real(self, *a, **kw)

            caught = None
            with mock.patch.object(Path, "read_text", fake):
                try:
                    mod.test_runner_starts_before_web(script)
                    caught = False
                except AssertionError:
                    caught = True
            cases.append({"script": script, "mutation": kind,
                          "assertion_caught": caught})
        # 原始脚本必须通过
        ok = True
        try:
            mod.test_runner_starts_before_web(script)
        except AssertionError:
            ok = False
        cases.append({"script": script, "mutation": "clean", "assertion_caught": not ok,
                      "note": "clean 应为 False（未捕获=通过）"})
    # upgrade 的 nohup 分支
    script = "upgrade_incremental.sh"
    original = (dep / script).read_text(encoding="utf-8")
    for kind in ("delete", "comment"):
        mutated = _mutate(original, kind, "backend.workers.metadata_runner")
        real = Path.read_text

        def fake2(self, *a, **kw):
            if self.name == script:
                return mutated
            return real(self, *a, **kw)

        caught = None
        with mock.patch.object(Path, "read_text", fake2):
            try:
                mod.test_upgrade_nohup_branch_starts_runner()
                caught = False
            except AssertionError:
                caught = True
        cases.append({"script": script, "mutation": "nohup_" + kind,
                      "assertion_caught": caught})
    bad = [c for c in cases if c["mutation"] != "clean" and not c["assertion_caught"]]
    out = {"cases": cases, "bad_mutations_caught": len(bad), "bad_list": bad}
    (HERE / "probe-deploy-mutation.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    {"cancel_elapsed": cancel_elapsed, "ownership": ownership,
     "deploy_mutation": deploy_mutation}[sys.argv[1]]()
