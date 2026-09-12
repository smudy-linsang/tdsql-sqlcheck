"""智能体D / v1.6.3.7 UAT 第一轮：真实浏览器（Playwright + 本机 Chrome）用户视角验收。

场景：
  u1_scan_and_history  提交在线元数据审核 → 完成 → 历史列表核验（命名/报告ID/提取时间一致性）
  u2_downloads         历史列表下载 .sql 与 HTML 报告（真实下载事件 + 文件名核验）
  u3_date_filter       按"开始日期=今天"筛选（本地时区口径）
  u4_smoke_others      相邻模块冒烟（即时审核 / 文件审核 / 大表治理 / 慢SQL 页面可用）
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNTIME = ROOT / "data/reports/uat_d_1636"
WEB = "http://127.0.0.1:8025"
ACCOUNT = "uat_d_1636"
PW = (RUNTIME / "admin.password").read_text(encoding="utf-8").strip()
DIST_LABEL = "D36-分布式库-含子表命名"
RESULT = {}


def utc():
    return datetime.now(timezone.utc).isoformat()


class Sess:
    def __init__(self, pw):
        self.browser = pw.chromium.launch(channel="chrome", headless=True)
        self.ctx = self.browser.new_context(viewport={"width": 1680, "height": 1000},
                                            accept_downloads=True)
        self.page = self.ctx.new_page()

    def close(self):
        try:
            self.browser.close()
        except Exception:
            pass

    def shot(self, name):
        self.page.screenshot(path=str(HERE / f"{name}.png"), full_page=True)

    def login(self):
        p = self.page
        p.goto(WEB + "/", wait_until="domcontentloaded")
        p.get_by_test_id("login-username").fill(ACCOUNT)
        p.get_by_test_id("login-password").fill(PW)
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

    def card_text(self):
        c = self.page.locator(".page-card", has_text="元数据审核任务 #")
        return c.first.inner_text() if c.count() else ""

    def history_card(self):
        """历史记录页卡（避免与"审核结果"表串行匹配）。"""
        return self.page.locator(".page-card", has_text="元数据提取与文件审核历史记录")

    def history_rows(self, n=5):
        rows = []
        card = self.history_card()
        for tr in card.locator(".el-table__body-wrapper tbody tr").all()[:n]:
            rows.append([c.inner_text().strip() for c in tr.locator("td").all()])
        return rows


def u1_scan_and_history(pw):
    s = Sess(pw)
    s.login()
    s.goto_online()
    f = s.form()
    f.locator(".el-select").first.click()
    s.page.locator(f'.el-select-dropdown__item:has-text("{DIST_LABEL}")').first.click()
    s.page.keyboard.press("Escape")
    s.shot("u1-01-ready")
    f.get_by_text("拉取元数据并执行文件审核").click()
    time.sleep(2)
    jid = s.page.evaluate("sessionStorage.getItem('meta_active_job')")
    RESULT["job_id"] = jid
    t0 = time.time()
    state = None
    while time.time() - t0 < 240:
        t = s.card_text()
        m = re.search(r"状态\s*\n?\s*([A-Z_]+)", t)
        if m and m.group(1) in ("SUCCEEDED", "FAILED", "CANCELLED"):
            state = m.group(1)
            break
        time.sleep(1.5)
    RESULT["task_state"] = state
    RESULT["task_card"] = s.card_text()
    s.shot("u1-02-done")
    s.goto_history()
    s.shot("u1-03-history")
    headers = [h.inner_text().strip() for h in
               s.page.locator(".el-table__header-wrapper th").all()]
    RESULT["headers_has_report_id"] = "报告ID" in headers
    rows = s.history_rows(5)
    RESULT["history_rows"] = rows
    # 取最新一条：文件名 / 报告ID / 提取时间
    newest = rows[0] if rows else []
    name = next((c for c in newest if c.endswith(".sql")), "")
    rid = next((c for c in newest if re.fullmatch(r"#\d+", c)), "")
    created = next((c for c in newest if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}", c)), "")
    RESULT["newest"] = {"report_id": rid, "source": name, "created_at": created}
    m = re.search(r"_(\d{8})_(\d{6})\.sql$", name)
    RESULT["name_is_timestamp_form"] = bool(m)
    if m and created:
        try:
            ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            ct = datetime.strptime(created, "%Y-%m-%d %H:%M")
            RESULT["name_vs_created_delta_s"] = round((ct - ts.replace(second=0)).total_seconds(), 1)
            RESULT["name_matches_created_minute"] = abs(
                (ct - ts.replace(second=0)).total_seconds()) < 60
        except ValueError as e:
            RESULT["parse_error"] = str(e)
    s.close()
    return "u1_scan_and_history"


def u2_downloads(pw):
    """历史列表下载：真实点击 → 捕获浏览器真实请求（含弹出页/下载事件）→ 校验服务端文件名。"""
    import requests
    s = Sess(pw)
    s.login()
    s.goto_online()
    s.goto_history()
    captured = []
    popups = []
    s.ctx.on("request", lambda r: captured.append(r.url)
             if "/api/v1/audit/report/" in r.url else None)
    s.ctx.on("page", lambda pg: popups.append(pg.url))
    row = s.history_card().locator(".el-table__body-wrapper tbody tr").first
    src = ""
    cells = [c.inner_text().strip() for c in row.locator("td").all()]
    src = next((c for c in cells if c.endswith(".sql")), "")
    RESULT["history_row_source"] = src
    out = {}
    downloads = []
    for label, key in (("下载 .sql", "sql"), ("下载 HTML 报告", "html")):
        try:
            with s.page.expect_download(timeout=8000) as dl:
                row.get_by_text(label, exact=True).first.click()
            d = dl.value
            downloads.append({"key": key, "suggested_filename": d.suggested_filename,
                              "url": d.url})
        except Exception as e:  # noqa: BLE001
            out[key] = {"download_event": False, "note": str(e).strip()[:120]}
        time.sleep(1)
    RESULT["download_events"] = downloads
    RESULT["captured_urls"] = captured
    RESULT["popups"] = popups
    tok = s.page.evaluate("localStorage.getItem('tdsql_token')")
    for key, suffix in (("sql", "/sql"), ("html", "/html")):
        url = next((u for u in captured if u.endswith(suffix) or suffix + "?" in u), None)
        if not url:
            ev = next((d for d in downloads if d["key"] == key), None)
            if ev:
                out.setdefault(key, {}).update({"request_captured": False,
                                                "from_download_event": ev})
            else:
                out.setdefault(key, {})["request_captured"] = False
            continue
        r = requests.get(url, timeout=60)
        cd = r.headers.get("Content-Disposition", "")
        out.setdefault(key, {}).update(
            {"request_captured": True, "status": r.status_code,
             "content_disposition": cd, "bytes": len(r.content),
             "filename_matches_row": (src in cd) if src else None})
    RESULT["downloads"] = out
    RESULT["names_are_timestamp_form"] = bool(
        re.match(r"^extracted_.+_\d{8}_\d{6}\.sql$", src or ""))
    s.shot("u2-01-downloads")
    s.close()
    return "u2_downloads"


def u3_date_filter(pw):
    s = Sess(pw)
    s.login()
    s.goto_online()
    s.goto_history()
    today = datetime.now().strftime("%Y-%m-%d")
    s.page.locator('input[placeholder="开始日期"]').first.fill(today)
    s.page.keyboard.press("Enter")
    s.page.get_by_role("button", name="查询").first.click()
    time.sleep(3)
    card = s.history_card()
    cnt = card.locator(".el-table__body-wrapper tbody tr").count()
    rows = s.history_rows(5)
    RESULT["filter_today"] = {"date": today, "rows": cnt,
                              "names": [next((c for c in r if c.endswith(".sql")), "")
                                        for r in rows],
                              "created": [next((c for c in r
                                                if re.match(r"^\d{4}-\d{2}-\d{2} ", c)), "")
                                          for r in rows]}
    RESULT["filter_today_all_match"] = all(
        c.startswith(today) for c in RESULT["filter_today"]["created"]) if rows else None
    s.shot("u3-01-filter-today")
    s.close()
    return "u3_date_filter"


GROUPS = {
    "SQL审核": ["即时审核", "文件审核"],
    "慢SQL治理": ["扫描任务", "慢SQL记录", "EXPLAIN分析"],
    "实例体检": ["上线检查", "大表治理", "深度诊断"],
    "平台治理": ["实例管理", "审核规则库", "评估规则集"],
    "系统管理": ["用户管理", "角色管理", "权限矩阵", "数据保留", "操作审计", "系统信息"],
}


def u4_smoke_others(pw):
    """相邻模块冒烟：展开分组后逐菜单进入，记录渲染情况 + 页面 JS 错误。"""
    s = Sess(pw)
    s.login()
    errors = []
    s.page.on("pageerror", lambda e: errors.append(str(e)[:200]))
    out = {}
    for grp, items in GROUPS.items():
        try:
            t = s.page.locator(".el-sub-menu__title", has_text=grp).first
            if t.count():
                t.click(timeout=8000)
                time.sleep(1)
        except Exception as e:  # noqa: BLE001
            out[f"__group__{grp}"] = {"expand_err": str(e)[:100]}
        for it in items:
            try:
                s.page.locator(".el-menu-item", has_text=it).first.click(timeout=8000)
                time.sleep(1.2)
                body = s.page.locator("body").inner_text()
                out[it] = {"ok": True, "route": s.page.evaluate("location.hash"),
                           "cards": s.page.locator(".page-card").count(),
                           "body_len": len(body)}
            except Exception as e:  # noqa: BLE001
                out[it] = {"ok": False, "err": str(e)[:120]}
    # 即时审核：一次真实 SQL 审核（用户视角的相邻主流程）
    try:
        s.page.locator(".el-menu-item", has_text="即时审核").first.click(timeout=8000)
        time.sleep(1)
        s.page.locator("textarea").first.fill("SELECT * FROM t_uat WHERE id=1 LIMIT 2001;")
        s.page.get_by_role("button", name=re.compile("审核")).first.click()
        time.sleep(4)
        body = s.page.locator("body").inner_text()
        out["instant_audit_run"] = {"ok": True, "has_result_rows":
                                    s.page.locator(".el-table__body-wrapper tbody tr").count(),
                                    "body_len": len(body)}
        s.shot("u4-02-instant-audit")
    except Exception as e:  # noqa: BLE001
        out["instant_audit_run"] = {"ok": False, "err": str(e)[:140]}
    out["js_errors"] = errors
    out["version_mark"] = s.page.evaluate(
        "[...document.querySelectorAll('*')].filter(e=>e.children.length===0"
        "&&/1\\.6\\.3\\./.test(e.textContent)).map(e=>e.textContent.trim())[0] || ''")
    RESULT["smoke"] = out
    RESULT["smoke_all_ok"] = all(v.get("ok") for k, v in out.items()
                                 if isinstance(v, dict) and k != "instant_audit_run")
    s.shot("u4-01-smoke")
    s.close()
    return "u4_smoke_others"


SCENARIOS = {"u1_scan_and_history": u1_scan_and_history, "u2_downloads": u2_downloads,
             "u3_date_filter": u3_date_filter, "u4_smoke_others": u4_smoke_others}

if __name__ == "__main__":
    name = sys.argv[1]
    RESULT["scenario"] = name
    RESULT["started_utc"] = utc()
    with sync_playwright() as p:
        SCENARIOS[name](p)
    RESULT["saved_utc"] = utc()
    (HERE / f"{name}.json").write_text(
        json.dumps(RESULT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(RESULT, ensure_ascii=False, indent=2, default=str))
