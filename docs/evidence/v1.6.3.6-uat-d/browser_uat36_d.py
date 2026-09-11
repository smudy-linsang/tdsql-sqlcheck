"""智能体D / v1.6.3.6 UAT：真实浏览器（Playwright + 本机 Chrome）操作脚本。

场景：
  b1_normal_flow   正常提交→完成→分页（回归 v1.6.3.5 主流程）
  b2_running_panel 通过"恢复查看上次任务"加载含跳过的既有任务，核对**运行面板**跳过口径（P4）
  b3_history_list  历史列表"跳过"列（P3）
  b4_history_html  历史 HTML 报告告警条与 KPI（P1：异常红告警 / 节选橙提示）
  b5_sql_download  下载 .sql 与 HTML（P2：文件头 [SKIPPED-SUMMARY] 与逐个 [SKIPPED] 块）
  b6_errors        错误路径回归（不可连接实例）
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
RUNTIME = ROOT / "data/reports/uat_d_1636"
WEB = "http://127.0.0.1:8025"
ACCOUNT = "uat_d_1636"
PW = (RUNTIME / "admin.password").read_text(encoding="utf-8").strip()
DIST_LABEL = "D36-分布式库-含子表命名"
OFFLINE_LABEL = "D36-不可连接-本机"
# 含跳过的端到端任务（由 uat36_scenarios_d.py bug01_e2e 产出）
SKIP_JOB = sys.argv[2] if len(sys.argv) > 2 else "a0278f77786b453093f04e38ff2ae9bb"
SKIP_REPORT = int(sys.argv[3]) if len(sys.argv) > 3 else 4
OMITTED_REPORT = int(sys.argv[4]) if len(sys.argv) > 4 else 6

RESULT = {}


def utc():
    return datetime.now(timezone.utc).isoformat()


class Sess:
    def __init__(self, pw, base=WEB):
        self.base = base
        self.browser = pw.chromium.launch(channel="chrome", headless=True)
        self.ctx = self.browser.new_context(viewport={"width": 1680, "height": 1000},
                                            accept_downloads=True)
        self.page = self.ctx.new_page()
        self.shots = []

    def close(self):
        try:
            self.browser.close()
        except Exception:
            pass

    def shot(self, name):
        p = HERE / f"{name}.png"
        self.page.screenshot(path=str(p), full_page=True)
        self.shots.append(p.name)

    def login(self, user=ACCOUNT, password=PW):
        p = self.page
        p.goto(self.base + "/", wait_until="domcontentloaded")
        p.get_by_test_id("login-username").fill(user)
        p.get_by_test_id("login-password").fill(password)
        p.get_by_test_id("login-submit").click()
        p.wait_for_selector("text=治理概览", timeout=30000)

    def goto_online(self):
        p = self.page
        p.get_by_text("SQL审核", exact=True).click()
        p.get_by_text("在线元数据审核", exact=True).click()
        p.wait_for_selector("text=在线元数据提取与文件审核", timeout=20000)

    def goto_history(self):
        self.page.get_by_text("历史元数据审核记录", exact=True).click()
        time.sleep(3)

    def form(self):
        return self.page.locator(".page-card", has_text="在线元数据提取与文件审核")

    def pick_instance(self, label=DIST_LABEL):
        self.form().locator(".el-select").first.click()
        self.page.locator(f'.el-select-dropdown__item:has-text("{label}")').first.click()
        self.page.keyboard.press("Escape")

    def submit(self):
        self.form().get_by_text("拉取元数据并执行文件审核").click()

    def card_text(self):
        c = self.page.locator(".page-card", has_text="元数据审核任务 #")
        return c.first.inner_text() if c.count() else ""

    def active_job(self):
        return self.page.evaluate("sessionStorage.getItem('meta_active_job')")

    def wait_state(self, states, timeout=300):
        t0 = time.time()
        while time.time() - t0 < timeout:
            t = self.card_text()
            m = re.search(r"状态\s*\n?\s*([A-Z_]+)", t)
            if m and m.group(1) in states:
                return m.group(1)
            time.sleep(1.5)
        return None

    def dl(self, label, fname):
        with self.page.expect_download(timeout=90000) as d:
            self.page.get_by_text(label).first.click()
        dest = HERE / fname
        d.value.save_as(str(dest))
        return dest


def save(name):
    RESULT["saved_utc"] = utc()
    (HERE / f"{name}.json").write_text(
        json.dumps(RESULT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(RESULT, ensure_ascii=False, indent=2, default=str))


def api_jobs():
    r = requests.post(f"{WEB}/api/v1/auth/login",
                      json={"username": ACCOUNT, "password": PW}, timeout=15)
    h = {"Authorization": "Bearer " + r.json()["token"]}
    return requests.get(f"{WEB}/api/v1/audit/extracted-reports?limit=50",
                        headers=h, timeout=20).json()


# ── 场景 ─────────────────────────────────────────────────────────────────
def b1_normal_flow(pw):
    s = Sess(pw)
    s.login()
    s.goto_online()
    s.pick_instance()
    s.shot("d36-b1-01-ready")
    s.submit()
    time.sleep(2)
    s.shot("d36-b1-02-submitted")
    jid = s.active_job()
    RESULT["job_id"] = jid
    st = s.wait_state(["SUCCEEDED", "FAILED", "CANCELLED"])
    RESULT["final_state"] = st
    RESULT["card_text"] = s.card_text()
    s.shot("d36-b1-03-done")
    body = s.page.locator("body").inner_text()
    m = re.search(r"审核结果（共\s*(\d+)\s*条", body)
    RESULT["results_total"] = int(m.group(1)) if m else None
    if s.page.locator(".el-pager li", has_text="2").count():
        s.page.locator(".el-pager li", has_text="2").first.click()
        time.sleep(2)
        s.shot("d36-b1-04-page2")
    s.close()
    return "b1_normal_flow"


def b2_running_panel(pw):
    """用"恢复查看上次任务"加载含跳过的既有任务，核对运行面板跳过口径（P4）。"""
    s = Sess(pw)
    s.login()
    # 在进入页面前就把会话恢复线索指向含跳过的任务（否则页面自动恢复会先抢到别的任务）
    s.page.evaluate(f"sessionStorage.setItem('meta_active_job','{SKIP_JOB}')")
    s.goto_online()
    time.sleep(3)
    rec = s.page.get_by_role("button", name="恢复查看上次任务")
    if rec.count() and not rec.first.is_disabled():
        rec.first.click()
    time.sleep(4)
    RESULT["job_id"] = s.active_job()
    RESULT["card_text"] = s.card_text()
    RESULT["has_skip_in_panel"] = "跳过" in s.card_text()
    s.shot("d36-b2-running-panel")
    s.close()
    return "b2_running_panel"


def b3_history_list(pw):
    s = Sess(pw)
    s.login()
    s.goto_online()
    s.goto_history()
    s.shot("d36-b3-history-list")
    rows = []
    for tr in s.page.locator(".el-table__body-wrapper tbody tr").all():
        cells = [c.inner_text().strip() for c in tr.locator("td").all()]
        rows.append(cells)
    RESULT["headers"] = [h.inner_text().strip() for h in
                         s.page.locator(".el-table__header-wrapper th").all()]
    RESULT["row_count"] = len(rows)
    RESULT["rows"] = rows[:8]
    s.close()
    return "b3_history_list"


def b4_history_html(pw):
    s = Sess(pw)
    s.login()
    s.goto_online()
    s.goto_history()
    out = {}
    tok = s.page.evaluate("localStorage.getItem('tdsql_token')")
    for rid, tag in ((SKIP_REPORT, "skip_report"), (OMITTED_REPORT, "omitted_report")):
        url = f"{WEB}/api/v1/audit/report/{rid}/html?access_token={tok}"
        r = requests.get(url, timeout=60)
        html = r.text if r.ok else ""
        out[tag] = {
            "status": r.status_code, "bytes": len(html.encode("utf-8")),
            "red_warning": "未能读取 DDL" in html,
            "orange_omitted": "本报告明细为节选" in html,
            "kpi_skip": "跳过（良性" in html,
        }
        dest = HERE / f"d36-{tag}.html"
        dest.write_text(html, encoding="utf-8")
    RESULT.update(out)
    s.shot("d36-b4-history")
    s.close()
    return "b4_history_html"


def b5_sql_download(pw):
    """P2 呈现面：①历史下载 .sql（按 results_json 重建 + [跳过]/[节选] 文件头）；
    ②任务卡下载产物 .sql（含 [SKIPPED-SUMMARY] 汇总与异常逐个块）。"""
    s = Sess(pw)
    s.login()
    s.page.evaluate(f"sessionStorage.setItem('meta_active_job','{SKIP_JOB}')")
    s.goto_online()
    time.sleep(3)
    tok = s.page.evaluate("localStorage.getItem('tdsql_token')")
    out = {}
    for rid, tag in ((SKIP_REPORT, "skip_report"), (OMITTED_REPORT, "omitted_report")):
        r = requests.get(f"{WEB}/api/v1/audit/report/{rid}/sql?access_token={tok}", timeout=60)
        text = r.text if r.ok else ""
        (HERE / f"d36-{tag}.sql").write_text(text, encoding="utf-8")
        out[f"history_{tag}"] = {
            "status": r.status_code, "bytes": len(text.encode("utf-8")),
            "header_skip": "[跳过]" in text,
            "header_omitted": "[节选]" in text,
            "blocks": text.count("-- SQL Object: CREATE"),
        }
    # ② 任务卡下载产物 .sql（真实浏览器点击 + 下载事件）
    rec = s.page.get_by_role("button", name="恢复查看上次任务")
    if rec.count() and not rec.first.is_disabled():
        rec.first.click()
        time.sleep(4)
    try:
        dest = s.dl("下载 .sql 文件", "d36-artifact-skip.sql")
        text = dest.read_text(encoding="utf-8", errors="ignore")
        out["artifact_sql"] = {
            "bytes": dest.stat().st_size,
            "has_summary_line": "[SKIPPED-SUMMARY]" in text,
            "per_object_blocks": text.count("-- [SKIPPED] SQL Object:"),
            "objects": text.count("-- SQL Object: CREATE"),
        }
        dest2 = s.dl("导出 HTML 报告", "d36-artifact-skip.html")
        html = dest2.read_text(encoding="utf-8", errors="ignore")
        out["artifact_html"] = {
            "bytes": dest2.stat().st_size,
            "mentions_skip": "跳过" in html,
        }
    except Exception as e:  # noqa: BLE001
        out["artifact_download_error"] = str(e)[:200]
    RESULT.update(out)
    s.shot("d36-b5-downloads")
    s.close()
    return "b5_sql_download"


def b6_errors(pw):
    s = Sess(pw)
    s.login()
    s.goto_online()
    s.pick_instance(OFFLINE_LABEL)
    s.submit()
    for _ in range(20):
        if s.card_text():
            break
        time.sleep(1)
    time.sleep(6)   # 等失败收敛
    RESULT["card_text"] = s.card_text()
    RESULT["job_id"] = s.active_job()
    s.shot("d36-b6-offline-error")
    s.close()
    return "b6_errors"


SCENARIOS = {"b1_normal_flow": b1_normal_flow, "b2_running_panel": b2_running_panel,
             "b3_history_list": b3_history_list, "b4_history_html": b4_history_html,
             "b5_sql_download": b5_sql_download, "b6_errors": b6_errors}

if __name__ == "__main__":
    name = sys.argv[1]
    RESULT["scenario"] = name
    RESULT["started_utc"] = utc()
    with sync_playwright() as p:
        SCENARIOS[name](p)
    save(name)
