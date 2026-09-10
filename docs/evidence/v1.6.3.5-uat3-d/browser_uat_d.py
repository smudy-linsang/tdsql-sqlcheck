"""智能体D / v1.6.3.5 第三轮 UAT：真实浏览器（Playwright + 本机 Chrome）操作脚本。

独立立场：不修改产品源码；所有结论来自真实页面输入/点击 + 数据库/接口反证。
每个场景输出 <scenario>.json 与 PNG 截图到本目录。

用法（仓库根目录）：
  python docs/evidence/v1.6.3.5-uat3-d/browser_uat_d.py <scenario>
scenario ∈ s1_baseline | s3_download | s5_rescan | s6_refresh | s7_runner_offline |
           s8_drop_post | s9_cancel | s10_errors | s11_legacy410 | s12_authz
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNTIME = ROOT / "data/reports/uat_d_1635_r3"
WEB = "http://127.0.0.1:8015"
PROXY = "http://127.0.0.1:8016"
ACCOUNT = "uat_d_1635_r3"
PW = (RUNTIME / "admin.password").read_text(encoding="utf-8").strip()
INSTANCE_LABEL = "D-UAT3-63表-本机模拟目标"
OFFLINE_LABEL = "D-UAT3-不可连接-本机"
MISSING_LABEL = "D-UAT3-不存在库-本机"

RESULT = {}


def utc():
    return datetime.now(timezone.utc).isoformat()


def token():
    r = requests.post(f"{WEB}/api/v1/auth/login",
                      json={"username": ACCOUNT, "password": PW}, timeout=15)
    r.raise_for_status()
    return r.json()["token"]


def api(path, method="GET", tok=None, **kw):
    h = {"Authorization": "Bearer " + (tok or token())}
    r = requests.request(method, WEB + path, headers=h, timeout=60, **kw)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:500]


def jobs():
    _, d = api("/api/v1/audit/metadata-jobs?limit=50")
    return d.get("items", [])


def job_full(job_id):
    _, d = api(f"/api/v1/audit/metadata-jobs/{job_id}")
    return d


def wait_job(job_id, terminal=("SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"),
             timeout=240):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        last = job_full(job_id)
        if last.get("state") in terminal:
            return last
        time.sleep(2)
    return last


class Sess:
    """一个真实浏览器会话（Chrome，真实输入与点击）。"""

    def __init__(self, pw, base=WEB, tag="s"):
        self.base = base
        self.tag = tag
        self.browser = pw.chromium.launch(channel="chrome", headless=True)
        self.ctx = self.browser.new_context(viewport={"width": 1680, "height": 1000})
        self.page = self.ctx.new_page()
        self.shots = []
        self.console = []
        self.page.on("console", lambda m: self.console.append(f"{m.type}:{m.text}"[:300]))

    def close(self):
        try:
            self.browser.close()
        except Exception:
            pass

    def shot(self, name):
        p = HERE / f"{name}.png"
        self.page.screenshot(path=str(p), full_page=True)
        self.shots.append(p.name)
        return p.name

    def login(self, user=ACCOUNT, password=PW, expect_menu="治理概览"):
        p = self.page
        p.goto(self.base + "/", wait_until="domcontentloaded")
        p.get_by_test_id("login-username").fill(user)
        p.get_by_test_id("login-password").fill(password)
        p.get_by_test_id("login-submit").click()
        if expect_menu:
            p.wait_for_selector(f"text={expect_menu}", timeout=30000)
        else:
            p.wait_for_selector(".sidebar", timeout=30000)

    def goto_online(self):
        p = self.page
        p.get_by_text("SQL审核", exact=True).click()
        p.get_by_text("在线元数据审核", exact=True).click()
        p.wait_for_selector("text=在线元数据提取与文件审核", timeout=20000)

    def form(self):
        return self.page.locator(".page-card", has_text="在线元数据提取与文件审核")

    def pick_instance(self, label=INSTANCE_LABEL):
        self.form().locator(".el-select").first.click()
        self.page.locator(f'.el-select-dropdown__item:has-text("{label}")').first.click()
        self.page.keyboard.press("Escape")

    def fill_db(self, db):
        self.form().locator('input[placeholder="目标数据库(留空默认)"]').fill(db)

    def submit(self):
        self.form().get_by_text("拉取元数据并执行文件审核").click()

    def task_card(self):
        return self.page.locator(".page-card", has_text="元数据审核任务 #")

    def card_text(self):
        c = self.task_card()
        return c.first.inner_text() if c.count() else ""

    def active_job_id(self):
        return self.page.evaluate("sessionStorage.getItem('meta_active_job')")

    def results_total(self):
        # 结果表头在任务卡之外的兄弟节点，须读整页文本
        t = self.page.locator("body").inner_text()
        m = re.search(r"审核结果（共\s*(\d+)\s*条", t)
        return int(m.group(1)) if m else None

    def wait_ui_state(self, states, timeout=240):
        t0 = time.time()
        while time.time() - t0 < timeout:
            txt = self.card_text()
            m = re.search(r"状态\s*\n?\s*([A-Z_]+)", txt)
            if m and m.group(1) in states:
                return m.group(1), txt
            if any(s in txt for s in states):
                return next(s for s in states if s in txt), txt
            time.sleep(1.5)
        return None, self.card_text()

    def messages(self):
        return [e.inner_text() for e in self.page.locator(".el-message").all()]


def save(name):
    RESULT["saved_utc"] = utc()
    (HERE / f"{name}.json").write_text(
        json.dumps(RESULT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(RESULT, ensure_ascii=False, indent=2, default=str))


# ══════════════════════════════════════════════════════════════════════════
def s1_baseline(pw):
    """UI-01 基线：登录→选实例→点击审核→阶段推进→完成→计数/耗时→分页。"""
    s = Sess(pw, tag="s1")
    before = jobs()
    s.login()
    RESULT["steps"] = ["真实输入账号口令并点击登录", "进入 SQL审核→在线元数据审核"]
    s.shot("d-s1-01-logged-in")
    s.goto_online()
    s.shot("d-s1-02-online-page")
    s.pick_instance()
    RESULT["scopes_default"] = [e.inner_text() for e in s.form().locator(".el-checkbox").all()]
    s.shot("d-s1-03-instance-picked")
    s.submit()
    time.sleep(1.5)
    s.shot("d-s1-04-submitted-running")
    RESULT["ui_after_submit"] = s.card_text()
    RESULT["messages_after_submit"] = s.messages()
    jid = s.active_job_id()
    RESULT["job_id"] = jid
    job = wait_job(jid) if jid else {}
    RESULT["job_api"] = job
    state, txt = s.wait_ui_state(["SUCCEEDED", "FAILED", "CANCELLED"], timeout=30)
    RESULT["ui_final_state"] = state
    s.shot("d-s1-05-succeeded")
    RESULT["ui_final_card"] = s.card_text()
    RESULT["ui_results_total"] = s.results_total()
    # 分页：第 2 页
    if s.page.locator(".el-pager li", has_text="2").count():
        s.page.locator(".el-pager li", has_text="2").first.click()
        time.sleep(2)
        s.shot("d-s1-06-page2")
        rows = s.page.locator(".el-table__body-wrapper tr").count()
        RESULT["page2_row_count"] = rows
    RESULT["jobs_before"] = len(before)
    RESULT["jobs_after"] = len(jobs())
    s.close()
    return "s1_baseline"


def s3_download(pw):
    """UI-06：任务卡下载 .sql / HTML，比对产物哈希与冻结实例名。"""
    import hashlib
    s = Sess(pw, tag="s3")
    s.login()
    s.goto_online()
    s.pick_instance()
    s.submit()
    time.sleep(2)
    jid = s.active_job_id()
    wait_job(jid)
    s.wait_ui_state(["SUCCEEDED"], timeout=30)
    RESULT["job_id"] = jid
    RESULT["ui_results_total"] = s.results_total()
    d = RUNTIME / "downloads"
    d.mkdir(exist_ok=True)
    for label, fname in (("下载 .sql 文件", "dl.sql"), ("导出 HTML 报告", "dl.html")):
        try:
            with s.page.expect_download(timeout=60000) as dl:
                s.page.get_by_text(label).first.click()
            dl.value.save_as(str(d / fname))
            RESULT[label] = {"file": fname, "bytes": (d / fname).stat().st_size,
                             "sha256": hashlib.sha256((d / fname).read_bytes()).hexdigest()}
        except Exception as e:  # noqa: BLE001
            RESULT[label] = {"error": str(e)[:200]}
    s.shot("d-s3-01-downloaded")
    # 与服务端产物/manifest 比对
    man = RUNTIME / "reports" / "metadata-audit" / (jid or "") / "manifest.json"
    RESULT["manifest_path"] = str(man)
    if man.exists():
        RESULT["manifest"] = json.loads(man.read_text(encoding="utf-8"))
    RESULT["frozen_name_in_sql"] = INSTANCE_LABEL in (d / "dl.sql").read_text(
        encoding="utf-8", errors="ignore") if (d / "dl.sql").exists() else None
    RESULT["frozen_name_in_html"] = INSTANCE_LABEL in (d / "dl.html").read_text(
        encoding="utf-8", errors="ignore") if (d / "dl.html").exists() else None
    s.close()
    return "s3_download"


def s5_rescan(pw):
    """R2-05：完成一次后目标库新增表，同条件再点必须产生新任务并包含新对象。"""
    s = Sess(pw, tag="s5")
    s.login()
    s.goto_online()
    s.pick_instance()
    # 本次点击前的既有任务（前一场景已完成、目标库随后新增了第 64 张表）
    prev = jobs()
    j1 = prev[0]["job_id"] if prev else None
    RESULT["first_job"] = {"id": j1, "state": prev[0]["state"] if prev else None,
                           "progress": prev[0].get("progress") if prev else None,
                           "results_total_expected": 63}
    s.pick_instance()
    s.submit()
    time.sleep(2)
    s.shot("d-s5-01-second-submit")
    j2 = s.active_job_id()
    j2_final = wait_job(j2)
    RESULT["second_job"] = {"id": j2, "state": j2_final.get("state"),
                            "progress": j2_final.get("progress"),
                            "report_id": j2_final.get("report_id")}
    RESULT["new_job_created"] = bool(j2 and j1 and j2 != j1)
    time.sleep(2)
    RESULT["ui_results_total"] = s.results_total()
    s.shot("d-s5-02-second-result")
    # 结果里是否包含新增表
    if j2:
        _, page1 = api(f"/api/v1/audit/metadata-jobs/{j2}/results?offset=0&limit=100")
        sqls = " ".join((i.get("sql_preview") or "") for i in page1.get("items", []))
        RESULT["contains_added_table"] = "t_d_added_after_scan" in sqls
        RESULT["results_total_api"] = page1.get("total")
    s.close()
    return "s5_rescan"


def s6_refresh(pw):
    """R2-06：长任务（child 启动延迟 25s）执行中刷新页面，恢复入口与任务卡。"""
    s = Sess(pw, tag="s6")
    s.login()
    s.goto_online()
    s.pick_instance()
    s.submit()
    time.sleep(2)
    jid = s.active_job_id()
    RESULT["job_id"] = jid
    RESULT["card_before_refresh"] = s.card_text()
    s.shot("d-s6-01-running-before-refresh")
    s.page.reload(wait_until="domcontentloaded")
    time.sleep(3)
    s.shot("d-s6-02-after-reload-dashboard")
    RESULT["card_after_reload_dashboard"] = s.card_text()
    s.goto_online()
    time.sleep(4)
    s.shot("d-s6-03-after-renter-online")
    RESULT["card_after_reenter"] = s.card_text()
    RESULT["recovered_job_id_session"] = s.active_job_id()
    RESULT["recovered_visible"] = "元数据审核任务 #" in s.card_text()
    job = wait_job(jid)
    RESULT["job_final"] = job
    state, txt = s.wait_ui_state(["SUCCEEDED", "FAILED", "CANCELLED"], timeout=60)
    RESULT["ui_final_state"] = state
    s.shot("d-s6-04-final")
    s.close()
    return "s6_refresh"


def s7_runner_offline(pw):
    """R2-01：runner 停止后过期心跳必须拒绝受理（503），不产生任务、不占槽。"""
    s = Sess(pw, tag="s7")
    s.login()
    s.goto_online()
    s.pick_instance()
    before = jobs()
    slot_before = api("/api/v1/audit/metadata-jobs?limit=1")[1]
    s.submit()
    time.sleep(2)
    s.shot("d-s7-01-submit-while-runner-down")
    RESULT["messages"] = s.messages()
    RESULT["card_present"] = s.task_card().count() > 0
    RESULT["card_text"] = s.card_text()
    RESULT["jobs_before"] = len(before)
    RESULT["jobs_after"] = len(jobs())
    RESULT["new_job_created"] = len(jobs()) > len(before)
    RESULT["slot_snapshot_before"] = slot_before.get("total")
    s.close()
    return "s7_runner_offline"


def s8_drop_post(pw):
    """R2-05/UI-05：受理响应被丢弃（代理故障注入），前端须恢复同一任务而非新建。"""
    s = Sess(pw, base=PROXY, tag="s8")
    before = jobs()
    s.login()
    s.goto_online()
    s.pick_instance()
    s.submit()
    time.sleep(3)
    s.shot("d-s8-01-after-drop")
    RESULT["messages"] = s.messages()
    RESULT["card_text"] = s.card_text()
    jid = s.active_job_id()
    RESULT["recovered_job_id"] = jid
    time.sleep(5)
    RESULT["card_text_later"] = s.card_text()
    s.shot("d-s8-02-recovered")
    RESULT["jobs_before"] = len(before)
    RESULT["jobs_after"] = len(jobs())
    RESULT["duplicate_created"] = len(jobs()) - len(before) > 1
    if jid:
        RESULT["job_final"] = wait_job(jid)
    s.close()
    return "s8_drop_post"


def s9_cancel(pw):
    """UI-08：运行中取消 → CANCELLED、槽释放、随后可再发起。"""
    s = Sess(pw, tag="s9")
    s.login()
    s.goto_online()
    s.pick_instance()
    s.submit()
    time.sleep(2)
    jid = s.active_job_id()
    RESULT["job_id"] = jid
    s.shot("d-s9-01-running")
    if s.page.get_by_role("button", name="取消任务").count():
        s.page.get_by_role("button", name="取消任务").first.click()
        time.sleep(1)
        s.shot("d-s9-02-confirm-dialog")
        # 注意：正文含“确认取消？”字样，必须按按钮角色精确点击，不能按文本
        s.page.get_by_role("button", name="确认取消").first.click()
        time.sleep(1)
        s.shot("d-s9-02b-confirmed")
    RESULT["messages_after_cancel"] = s.messages()
    job = wait_job(jid)
    RESULT["job_final"] = job
    time.sleep(2)
    s.shot("d-s9-03-after-cancel")
    RESULT["card_text"] = s.card_text()
    s.close()
    return "s9_cancel"


def s10_errors(pw):
    """R2-03/UI-07：不可连接实例与不存在库的错误语义化（真实浏览器提交）。"""
    out = {}
    for label, key in ((OFFLINE_LABEL, "offline"), (MISSING_LABEL, "missing_db")):
        s = Sess(pw, tag=f"s10-{key}")
        s.login()
        s.goto_online()
        s.pick_instance(label)
        s.submit()
        # 任务卡出现可能滞后于受理（后台执行很快失败），最多等 20s 再读页面
        for _ in range(20):
            if s.task_card().count():
                break
            time.sleep(1)
        jid = s.active_job_id()
        out[key] = {"job_id": jid, "ui": s.card_text(),
                    "messages": s.messages(),
                    "job": wait_job(jid) if jid else {}}
        s.shot(f"d-s10-{key}")
        s.close()
    RESULT.update(out)
    return "s10_errors"


def s11_legacy410(pw):
    """UI-10：旧同步接口必须 410 且零副作用。"""
    s = Sess(pw, tag="s11")
    s.login()
    s.goto_online()
    before = jobs()
    tok = s.page.evaluate("localStorage.getItem('tdsql_token')||''")
    RESULT["token_found"] = bool(tok)
    r = s.page.evaluate("""async () => {
        const t = localStorage.getItem('tdsql_token') || '';
        const resp = await fetch('/api/v1/audit/extract-and-audit', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + t},
            body: JSON.stringify({connection_id: 'd-r3-local', database: 'uat_d_1635_r3_target',
                                  scopes: ['TABLE','INDEX','VIEW','SHARDKEY']})
        });
        return {status: resp.status, text: (await resp.text()).slice(0, 300)};
    }""")
    RESULT["legacy_response"] = r
    time.sleep(2)
    RESULT["jobs_before"] = len(before)
    RESULT["jobs_after"] = len(jobs())
    RESULT["new_job_created"] = len(jobs()) > len(before)
    s.shot("d-s11-legacy-410")
    s.close()
    return "s11_legacy410"


def s12_authz(pw):
    """UI-04：未认证访问与跨用户访问的所有权校验。"""
    js = jobs()
    target = js[0]["job_id"] if js else None
    RESULT["target_job"] = target
    # 未认证
    r = requests.get(f"{WEB}/api/v1/audit/metadata-jobs", timeout=15)
    RESULT["anon_list"] = r.status_code
    if target:
        r2 = requests.get(f"{WEB}/api/v1/audit/metadata-jobs/{target}", timeout=15)
        RESULT["anon_job"] = r2.status_code
    # 第二个用户（非 admin）
    tok = token()
    uname = "uat_d_viewer"
    pw2 = "Vw!" + PW[-8:]
    st, body = api("/api/v1/auth/users", "POST", tok=tok,
                   json={"username": uname, "display_name": "D-UAT3只读", "role": "developer",
                         "password": pw2})
    RESULT["viewer_create"] = {"status": st, "body": body if st != 200 else "created"}
    r3 = requests.post(f"{WEB}/api/v1/auth/login",
                       json={"username": uname, "password": pw2}, timeout=15)
    RESULT["viewer_login"] = {"status": r3.status_code,
                              "note": "401 表示口令与创建时不一致（用户名或口令错误）"}
    if r3.ok and target:
        vt = r3.json().get("token")
        rr = requests.get(f"{WEB}/api/v1/audit/metadata-jobs/{target}",
                          headers={"Authorization": "Bearer " + vt}, timeout=15)
        RESULT["viewer_get_others_job"] = rr.status_code
        rr2 = requests.get(f"{WEB}/api/v1/audit/metadata-jobs",
                           headers={"Authorization": "Bearer " + vt}, timeout=15)
        RESULT["viewer_list"] = rr2.status_code
        try:
            RESULT["viewer_list_items"] = len(rr2.json().get("items", []))
        except ValueError:
            pass
    s = Sess(pw, tag="s12")
    try:
        s.login(user=uname, password=pw2, expect_menu=None)
        s.shot("d-s12-viewer-login")
        RESULT["viewer_menus_has_online"] = s.page.evaluate(
            "document.body.innerText.includes('在线元数据审核')")
        RESULT["viewer_menu_texts"] = [e.inner_text() for e in
                                       s.page.locator(".el-menu-item").all()][:40]
    except Exception as e:  # noqa: BLE001
        RESULT["viewer_ui_error"] = str(e)[:200]
        s.shot("d-s12-viewer-ui-error")
    s.close()
    return "s12_authz"


def s13_failed_card(pw):
    """UI-07：失败任务在页面上是否最终收敛为 FAILED 并显示可读原因。"""
    s = Sess(pw, tag="s13")
    s.login()
    s.goto_online()
    s.pick_instance(OFFLINE_LABEL)
    s.submit()
    seq = []
    final = None
    for i in range(15):
        time.sleep(2)
        txt = s.card_text()
        m = re.search(r"状态\s*\n?\s*([A-Z_]+)", txt)
        st = m.group(1) if m else None
        seq.append({"t_plus_s": (i + 1) * 2, "state": st,
                    "has_error_alert": "无法连接目标数据库" in txt,
                    "elapsed": (re.search(r"已用时长\s*\n?\s*([^\n\t]*)", txt) or [None, None])[1]})
        if st in ("FAILED", "CANCELLED", "SUCCEEDED"):
            final = st
            break
    RESULT["card_state_sequence"] = seq
    RESULT["final_state"] = final
    RESULT["final_card_text"] = s.card_text()
    RESULT["job_id"] = s.active_job_id()
    s.shot("d-s13-failed-card")
    if RESULT["job_id"]:
        RESULT["job_api"] = job_full(RESULT["job_id"])
    s.close()
    return "s13_failed_card"


def s14_concurrency(pw):
    """JOB-01/UI-02：同一用户双击 + 另一用户并发提交，全主机只允许 1 个活动任务。"""
    before = len(jobs())
    s = Sess(pw, tag="s14a")
    s.login()
    s.goto_online()
    s.pick_instance()
    s.submit()
    s.submit()          # 立刻再点一次（模拟双击）
    time.sleep(2)
    jid = s.active_job_id()
    RESULT["userA_job"] = jid
    RESULT["userA_card"] = s.card_text()
    # 另一用户（dba，已改密）并发提交
    owner_pw = (RUNTIME / "owner.password").read_text(encoding="utf-8").strip()
    s2 = Sess(pw, tag="s14b")
    s2.login(user="uat_d_dba2", password=owner_pw, expect_menu=None)
    s2.goto_online()
    s2.pick_instance()
    s2.submit()
    time.sleep(3)
    RESULT["userB_messages"] = s2.messages()
    RESULT["userB_job"] = s2.active_job_id()
    RESULT["userB_card"] = s2.card_text()
    s.shot("d-s14-a-running")
    s2.shot("d-s14-b-busy")
    RESULT["jobs_before"] = before
    RESULT["jobs_after"] = len(jobs())
    RESULT["exactly_one_new_job"] = (len(jobs()) - before) == 1
    if jid:
        RESULT["job_final"] = wait_job(jid)
    s.close()
    s2.close()
    return "s14_concurrency"


def s15_double_click(pw):
    """UI-02：同一事件循环内连续三次点击提交，必须只产生 1 个任务。"""
    before = len(jobs())
    s = Sess(pw, tag="s15")
    s.login()
    s.goto_online()
    s.pick_instance()
    r = s.page.evaluate("""() => {
        const bs = [...document.querySelectorAll('button')]
            .filter(b => (b.innerText || '').includes('拉取元数据并执行文件审核'));
        if (!bs.length) return 'button-not-found';
        bs[0].click(); bs[0].click(); bs[0].click();
        return 'clicked-x3';
    }""")
    RESULT["js_clicks"] = r
    time.sleep(5)
    RESULT["jobs_before"] = before
    RESULT["jobs_after"] = len(jobs())
    RESULT["jobs_added"] = len(jobs()) - before
    RESULT["job_id"] = s.active_job_id()
    RESULT["card_after_clicks"] = s.card_text()
    s.shot("d-s15-double-click")
    if RESULT["job_id"]:
        RESULT["job_final"] = wait_job(RESULT["job_id"])
    s.close()
    return "s15_double_click"


def s16_recover_action(pw):
    """D-03："恢复查看上次任务"必须是独立动作（只 GET、不新建任务）。"""
    before = len(jobs())
    s = Sess(pw, tag="s16")
    s.login()
    s.goto_online()
    has_btn = s.page.get_by_role("button", name="恢复查看上次任务").count() > 0
    RESULT["button_present"] = has_btn
    RESULT["buttons_state_at_entry"] = {
        lb: {"count": s.page.get_by_role("button", name=lb).count(),
             "disabled": (s.page.get_by_role("button", name=lb).first.is_disabled()
                          if s.page.get_by_role("button", name=lb).count() else None)}
        for lb in ("拉取元数据并执行文件审核", "恢复查看上次任务")}
    if has_btn and not s.page.get_by_role("button", name="恢复查看上次任务").first.is_disabled():
        s.page.get_by_role("button", name="恢复查看上次任务").first.click()
        time.sleep(3)
        RESULT["msg_no_history"] = s.messages()
        RESULT["jobs_after_empty_recover"] = len(jobs()) - before
        s.shot("d-s16-01-recover-no-history")
    s.pick_instance()
    s.submit()
    time.sleep(2)
    jid = s.active_job_id()
    wait_job(jid)
    s.wait_ui_state(["SUCCEEDED"], timeout=30)
    RESULT["job_id"] = jid
    RESULT["submission_record"] = s.page.evaluate("sessionStorage.getItem('meta_submission')")
    s.page.reload(wait_until="domcontentloaded")
    time.sleep(3)
    s.goto_online()
    rec = s.page.get_by_role("button", name="恢复查看上次任务")
    if rec.count() and not rec.first.is_disabled():
        rec.first.click()
    time.sleep(4)
    RESULT["card_after_recover"] = s.card_text()
    RESULT["recovered_job_id"] = s.active_job_id()
    RESULT["recovered_same_job"] = (s.active_job_id() == jid)
    RESULT["jobs_added_excluding_scan"] = len(jobs()) - before - 1
    s.shot("d-s16-02-recovered")
    s.close()
    return "s16_recover_action"


def s17_phantom_accepted(pw):
    """鲁棒性观察：状态 ACCEPTED 但已不在唯一槽的历史残留任务，前端如何表现。"""
    s = Sess(pw, tag="s17")
    s.login()
    s.goto_online()
    time.sleep(4)
    RESULT["card_text"] = s.card_text()
    RESULT["job_id_session"] = s.page.evaluate("sessionStorage.getItem('meta_active_job')")
    for label in ("拉取元数据并执行文件审核", "恢复查看上次任务"):
        loc = s.page.get_by_role("button", name=label)
        RESULT[label] = {"count": loc.count(),
                         "disabled": (loc.first.is_disabled() if loc.count() else None)}
    RESULT["api_jobs_active"] = [{"job_id": j["job_id"], "state": j["state"]}
                                 for j in jobs()
                                 if j["state"] not in ("SUCCEEDED", "FAILED", "CANCELLED")]
    s.shot("d-s17-phantom-accepted")
    s.close()
    return "s17_phantom_accepted"


SCENARIOS = {
    "s1_baseline": s1_baseline,
    "s3_download": s3_download,
    "s5_rescan": s5_rescan,
    "s6_refresh": s6_refresh,
    "s7_runner_offline": s7_runner_offline,
    "s8_drop_post": s8_drop_post,
    "s9_cancel": s9_cancel,
    "s10_errors": s10_errors,
    "s11_legacy410": s11_legacy410,
    "s12_authz": s12_authz,
    "s13_failed_card": s13_failed_card,
    "s14_concurrency": s14_concurrency,
    "s15_double_click": s15_double_click,
    "s16_recover_action": s16_recover_action,
    "s17_phantom_accepted": s17_phantom_accepted,
}

if __name__ == "__main__":
    name = sys.argv[1]
    RESULT["scenario"] = name
    RESULT["started_utc"] = utc()
    RESULT["base"] = "proxy" if name == "s8_drop_post" else "direct"
    with sync_playwright() as p:
        SCENARIOS[name](p)
    save(name)
