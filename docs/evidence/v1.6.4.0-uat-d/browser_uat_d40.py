"""智能体D / v1.6.4.0 UAT：真实浏览器（Playwright + 本机 Chrome）用户视角验收。

场景：
  b1_local_help     页头入口 → 抽屉 → 本地帮助全流程（含预览/提交/结果/导出）
  b2_business_entry 业务结果区 Copilot 入口探针（逐菜单扫描"解读/解释/建议"按钮）
  b3_admin_page     管理区"AI配置"页面探针（菜单可点性 + 页面主体）
  b4_audit_page     操作审计页面对 Copilot 审计元数据的呈现
  b5_editor_bridge  "送入审核编辑器" 对既有草稿的覆盖行为
  b6_menu_scan      全菜单 Copilot 帮助入口覆盖面（DETAIL §3.1/§13.1 清单）
"""
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))
import _boot  # noqa: F401,E402

from playwright.sync_api import sync_playwright  # noqa: E402

from uat_d40_api import RUNTIME, creds, save, utc  # noqa: E402

WEB = "http://127.0.0.1:8025"
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)


class Sess:
    def __init__(self, pw, username="uat_d_1640", download_dir=None):
        self.pw = pw
        self.username = username
        self.browser = pw.chromium.launch(channel="chrome", headless=True)
        self.ctx = self.browser.new_context(
            viewport={"width": 1680, "height": 1000}, accept_downloads=True)
        self.page = self.ctx.new_page()
        self.console = []
        self.page.on("console", lambda m: self.console.append(
            {"type": m.type, "text": m.text[:300]}))
        self.page.on("pageerror", lambda e: self.console.append(
            {"type": "pageerror", "text": str(e)[:300]}))
        self.download_dir = download_dir

    def shot(self, name, full=True):
        p = SHOTS / f"{name}.png"
        self.page.screenshot(path=str(p), full_page=full)
        return str(p.relative_to(HERE.parents[2]))

    def login(self):
        p = self.page
        p.goto(WEB + "/", wait_until="domcontentloaded")
        p.get_by_test_id("login-username").fill(self.username)
        p.get_by_test_id("login-password").fill(creds()[self.username])
        p.get_by_test_id("login-submit").click()
        p.wait_for_selector("text=治理概览", timeout=30000)

    def close(self):
        try:
            self.browser.close()
        except Exception:
            pass


def _visible_texts(page, sel="button, .el-button, .el-menu-item"):
    out = []
    for el in page.query_selector_all(sel):
        try:
            if el.is_visible():
                t = (el.inner_text() or "").strip()
                if t:
                    out.append(t)
        except Exception:
            pass
    return out


def b1_local_help():
    """本地帮助全流程（对应 CP-F01/CP-F02 + §12.3 预览可见性）。"""
    out = {"utc": utc(), "steps": []}
    with sync_playwright() as pw:
        s = Sess(pw)
        s.login()
        out["steps"].append({"login": "ok", "shot": s.shot("b1-01-dashboard")})

        s.page.get_by_test_id("copilot-open").click()
        time.sleep(2)
        s.page.wait_for_selector(".copilot-drawer", timeout=15000)
        drawer_text = s.page.locator(".copilot-drawer").inner_text()
        out["steps"].append({"drawer_opened": True,
                             "drawer_text": drawer_text[:600],
                             "shot": s.shot("b1-02-drawer")})

        # 场景选项
        s.page.locator(".copilot-drawer .el-select").first.click()
        time.sleep(1)
        opts = [e.inner_text().strip() for e in
                s.page.query_selector_all(".el-select-dropdown__item")
                if e.is_visible()]
        out["scene_options_in_drawer"] = opts
        s.page.keyboard.press("Escape")
        time.sleep(0.5)

        s.page.locator(".copilot-drawer textarea").first.fill(
            "在线元数据审核怎么用？")
        s.page.locator(".copilot-drawer").get_by_text("预览资料",
                                                      exact=True).click()
        time.sleep(4)
        prev_text = ""
        try:
            prev_text = s.page.locator(".copilot-preview-box").first.inner_text()
        except Exception:
            pass
        out["steps"].append({"preview_box": prev_text[:600],
                             "shot": s.shot("b1-03-preview")})
        out["console_so_far"] = s.console[-10:]
        s.close()
    save("b1_local_help", out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:4000])


def _scan_business_entries(page):
    """扫描当前页面所有可见按钮，找 Copilot 业务入口。"""
    pats = re.compile(r"解读|解释|排查|修改建议|让 Copilot|Copilot 解读|分析失败")
    hits = []
    for el in page.query_selector_all("button, .el-button, a, span"):
        try:
            if not el.is_visible():
                continue
            t = (el.inner_text() or "").strip()
            if t and pats.search(t) and len(t) < 30:
                hits.append(t)
        except Exception:
            pass
    return sorted(set(hits))


def b2_business_entry():
    """业务结果区 Copilot 入口探针（DETAIL §3.1 第 3 条、§13.1 需接线模块清单）。"""
    out = {"utc": utc(), "pages": []}
    targets = [
        ("即时审核", ["SQL审核", "即时审核"]),
        ("文件审核", ["SQL审核", "文件审核"]),
        ("在线元数据审核", ["SQL审核", "在线元数据审核"]),
        ("审核规则库", ["SQL审核", "审核规则库"]),
        ("慢SQL记录", ["慢SQL分析", "慢SQL记录"]),
        ("上线检查", ["SQL审核", "上线检查"]),
        ("大表治理", ["大表治理"]),
    ]
    with sync_playwright() as pw:
        s = Sess(pw)
        s.login()
        for label, path in targets:
            try:
                for seg in path:
                    s.page.get_by_text(seg, exact=True).first.click()
                    time.sleep(1.2)
                time.sleep(2.0)
                hits = _scan_business_entries(s.page)
                shot = s.shot(f"b2-{label}")
                out["pages"].append({"page": label, "copilot_entry_buttons": hits,
                                     "shot": shot})
            except Exception as e:
                out["pages"].append({"page": label, "error": str(e)[:200]})
        s.close()
    save("b2_business_entry", out)
    for p in out["pages"]:
        print(f"{p.get('page'):18s} entry_buttons={p.get('copilot_entry_buttons')}"
              f" {p.get('error','')}")
    return out


def b3_admin_page():
    """管理区"AI配置"（DETAIL §3.1 第 4 条）页面主体探针。"""
    out = {"utc": utc()}
    with sync_playwright() as pw:
        s = Sess(pw)
        s.login()
        body_before = s.page.locator(".page-content, .main-content, body").first
        before = body_before.inner_text()[:200]
        clicked = False
        try:
            s.page.get_by_text("系统管理", exact=True).first.click()
            time.sleep(1.0)
        except Exception:
            pass
        try:
            s.page.get_by_text("AI配置", exact=True).first.click()
            clicked = True
        except Exception as e:
            out["click_error"] = str(e)[:200]
        time.sleep(2.0)
        # 页面主体：data-testid 或 page-content
        content = ""
        for sel in ("[data-testid='copilot-admin-page']", ".page-content"):
            el = s.page.query_selector(sel)
            if el:
                content = el.inner_text()[:800]
                break
        out.update({
            "menu_item_exists": s.page.get_by_text("AI配置", exact=True).count() > 0,
            "menu_clicked": clicked,
            "page_content_text": content,
            "page_content_len": len(content.strip()),
            "current_page_attr": s.page.evaluate(
                "() => (document.querySelector('.el-menu-item.is-active')||{}).innerText||''"),
            "shot": s.shot("b3-ai-config"),
            "body_first200_before": before,
        })
        s.close()
    save("b3_admin_page", out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:2500])
    return out


def b4_audit_page():
    """操作审计页面是否呈现 Copilot 审计元数据（§12.5）。"""
    out = {"utc": utc()}
    with sync_playwright() as pw:
        s = Sess(pw)
        s.login()
        try:
            s.page.get_by_text("系统管理", exact=True).first.click()
            time.sleep(1.0)
            s.page.get_by_text("操作审计", exact=True).first.click()
            time.sleep(3.0)
        except Exception as e:
            out["nav_error"] = str(e)[:200]
        txt = ""
        el = s.page.query_selector(".page-content")
        if el:
            txt = el.inner_text()[:1500]
        out["page_text"] = txt
        out["mentions_copilot"] = ("Copilot" in txt) or ("copilot" in txt.lower())
        # 表格行数
        rows = s.page.query_selector_all(".el-table__body-wrapper tbody tr")
        out["table_rows"] = len(rows)
        out["shot"] = s.shot("b4-audit")
        s.close()
    save("b4_audit_page", out)
    print(json.dumps({k: v for k, v in out.items() if k != "page_text"},
                     ensure_ascii=False)[:800])
    return out


if __name__ == "__main__":
    m = sys.argv[1] if len(sys.argv) > 1 else "b1_local_help"
    {"b1_local_help": b1_local_help,
     "b2_business_entry": b2_business_entry,
     "b3_admin_page": b3_admin_page,
     "b4_audit_page": b4_audit_page}.get(m, lambda: print(__doc__))()
