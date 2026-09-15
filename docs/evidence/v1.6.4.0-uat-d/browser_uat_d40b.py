"""智能体D / v1.6.4.0 UAT：真实浏览器核心用户旅程。

  b5_copilot_page  侧栏「Copilot专家助手」页：建会话 → 预览 → 提交 → 结果 → 导出 → 反馈
  b6_menu_coverage 全菜单 Copilot 业务入口覆盖面扫描（DETAIL §3.1 第 3 条 / §13.1 清单）
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

from uat_d40_api import creds, save, utc  # noqa: E402

WEB = "http://127.0.0.1:8025"
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)
ENTRY_PAT = re.compile(r"解读|解释这条|排查|修改建议|让 ?Copilot|Copilot ?解读|分析失败|问 Copilot")


class S:
    def __init__(self, pw):
        self.b = pw.chromium.launch(channel="chrome", headless=True)
        self.ctx = self.b.new_context(viewport={"width": 1680, "height": 1000},
                                      accept_downloads=True)
        self.p = self.ctx.new_page()
        self.errs = []
        self.p.on("pageerror", lambda e: self.errs.append("pageerror: " + str(e)[:250]))
        self.p.on("console", lambda m: self.errs.append(f"{m.type}: {m.text[:250]}")
                  if m.type == "error" else None)

    def shot(self, n):
        self.p.screenshot(path=str(SHOTS / f"{n}.png"), full_page=True)
        return f"docs/evidence/v1.6.4.0-uat-d/shots/{n}.png"

    def login(self):
        p = self.p
        p.goto(WEB + "/", wait_until="domcontentloaded")
        p.get_by_test_id("login-username").fill("uat_d_1640")
        p.get_by_test_id("login-password").fill(creds()["uat_d_1640"])
        p.get_by_test_id("login-submit").click()
        p.wait_for_selector("text=治理概览", timeout=30000)
        time.sleep(2)

    def menu(self, text):
        self.p.locator(".el-menu-item", has_text=text).first.click()
        time.sleep(2.5)

    def close(self):
        try:
            self.b.close()
        except Exception:
            pass


def b5_copilot_page():
    out = {"utc": utc(), "steps": []}
    with sync_playwright() as pw:
        s = S(pw)
        s.login()
        s.menu("Copilot专家助手")
        page_el = s.p.query_selector("[data-testid='copilot-page']")
        out["copilot_page_rendered"] = bool(page_el)
        out["page_text"] = page_el.inner_text()[:600] if page_el else ""
        out["shot_page"] = s.shot("b5-01-copilot-page")

        if not page_el:
            out["verdict"] = "会话页未渲染"
            s.close()
            save("b5_copilot_page", out)
            print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:3000])
            return

        # 0) 记录会话页首屏能力显示（未点抽屉时 capabilities 未加载）
        out["page_mode_tag"] = s.p.evaluate(
            """() => Array.from(document.querySelectorAll(
                 "[data-testid='copilot-page'] .el-tag"))
                 .map(e => e.innerText.trim())""")
        out["capabilities_api"] = s.p.evaluate(
            """async () => {
              const t = localStorage.getItem('tdsql_token') || '';
              const h = t ? ('Bearer ' + t) : '';
              const r = await fetch('/api/v1/copilot/capabilities',
                                    h ? {headers: {Authorization: h}} : {});
              return r.ok ? (await r.json()).mode : ('HTTP ' + r.status);
            }""")

        # 1) 已授权实例下拉选项（服务端有 APPROVED 授权时应可见）
        try:
            s.p.locator(".copilot-toolbar .el-select").first.click()
            time.sleep(1.5)
            out["instance_options"] = [e.inner_text().strip() for e in
                                       s.p.query_selector_all(".el-select-dropdown__item")
                                       if e.is_visible()]
            s.p.keyboard.press("Escape")
            time.sleep(1.0)
        except Exception as e:
            out["select_instance_err"] = str(e)[:200]

        # 2) 建通用会话（不依赖实例选择）
        try:
            s.p.get_by_role("button", name="新建通用会话").first.click()
            time.sleep(2.5)
        except Exception as e:
            out["create_session_err"] = str(e)[:200]
        out["session_selected"] = s.p.evaluate(
            "() => { const e=document.querySelector('.copilot-session-bar .el-select input');"
            " return e ? e.value : null }")
        out["shot_session"] = s.shot("b5-02-session")

        # 2) 提问 + 预览
        s.p.locator(".copilot-question-input textarea").first.fill(
            "在线元数据审核任务失败时我应该先看什么证据？")
        s.p.get_by_role("button", name="预览资料").first.click()
        time.sleep(6)
        prev = s.p.query_selector(".copilot-preview-box")
        out["preview_text"] = prev.inner_text()[:600] if prev else ""
        err = s.p.query_selector(".copilot-error")
        out["preview_error"] = err.inner_text()[:300] if err else ""
        out["shot_preview"] = s.shot("b5-03-preview")

        # 3) 提交
        btn = s.p.get_by_role("button", name="确认提交").first
        out["submit_disabled"] = btn.get_attribute("disabled") is not None or \
            "is-disabled" in (btn.get_attribute("class") or "")
        if not out["submit_disabled"]:
            btn.click()
            time.sleep(4)
            out["shot_submit"] = s.shot("b5-04-submitted")
            for _ in range(30):
                txt = ""
                tl = s.p.query_selector(".copilot-turn-list")
                if tl:
                    txt = tl.inner_text()
                if re.search(r"SUCCEEDED|LOCAL_ONLY|DEGRADED|FAILED|CANCELLED|"
                             r"INTERRUPTED", txt):
                    break
                time.sleep(2)
            out["turn_list_text"] = (s.p.query_selector(".copilot-turn-list") or
                                     type("x", (), {"inner_text": lambda: ""})()).inner_text()[:600]
            out["shot_turns"] = s.shot("b5-05-turns")

            # 4) 查看结果
            try:
                s.p.get_by_role("button", name="查看结果").first.click()
                time.sleep(3)
                dlg = s.p.query_selector(".el-dialog")
                out["result_dialog_text"] = dlg.inner_text()[:900] if dlg else ""
                out["shot_result"] = s.shot("b5-06-result")
                s.p.keyboard.press("Escape")
                time.sleep(1)
            except Exception as e:
                out["view_result_err"] = str(e)[:200]

            # 5) 导出报告（真实下载/新标签）
            try:
                with s.ctx.expect_page(timeout=15000) as newp:
                    s.p.get_by_role("button", name="导出报告").first.click()
                pg = newp.value
                pg.wait_for_load_state("domcontentloaded", timeout=15000)
                out["export_url"] = pg.url
                out["export_text_head"] = pg.inner_text()[:400]
                out["export_is_html_report"] = "内部建议资料" in pg.inner_text() or \
                    "建议报告" in pg.inner_text()
                pg.screenshot(path=str(SHOTS / "b5-07-export.png"), full_page=True)
                out["shot_export"] = "docs/evidence/v1.6.4.0-uat-d/shots/b5-07-export.png"
                pg.close()
            except Exception as e:
                out["export_err"] = str(e)[:300]

            # 6) 反馈
            try:
                s.p.get_by_role("button", name="有用").first.click()
                time.sleep(2)
                out["feedback_clicked"] = True
            except Exception as e:
                out["feedback_err"] = str(e)[:200]

        out["console_errors"] = s.errs[-12:]
        s.close()
    save("b5_copilot_page", out)
    for k, v in out.items():
        if k != "console_errors":
            print(f"{k}: {json.dumps(v, ensure_ascii=False, default=str)[:400]}")
    return out


def b6_menu_coverage():
    """逐菜单扫描 Copilot 业务入口按钮（需接线的 8 个模块 + 其余帮助入口）。"""
    out = {"utc": utc(), "pages": []}
    with sync_playwright() as pw:
        s = S(pw)
        s.login()
        groups = [g.inner_text().strip() for g in
                  s.p.query_selector_all(".el-sub-menu__title")]
        out["groups"] = groups
        leaves = s.p.evaluate(
            "() => Array.from(document.querySelectorAll('.el-menu-item'))"
            ".map(e => (e.innerText||'').trim()).filter(Boolean)")
        out["leaf_menus"] = leaves
        for name in leaves:
            try:
                s.p.locator(".el-menu-item", has_text=name).first.click()
                time.sleep(2.2)
                hits = sorted({(e.inner_text() or "").strip()
                               for e in s.p.query_selector_all("button, .el-button")
                               if e.is_visible() and
                               ENTRY_PAT.search((e.inner_text() or "").strip() or "")
                               and len((e.inner_text() or "").strip()) < 30})
                out["pages"].append({"menu": name, "copilot_entry_buttons": hits})
            except Exception as e:
                out["pages"].append({"menu": name, "error": str(e)[:120]})
        out["console_errors"] = s.errs[-8:]
        s.close()
    save("b6_menu_coverage", out)
    print(f"顶层分组: {out['groups']}")
    for p in out["pages"]:
        print(f"  {p.get('menu'):20s} 业务入口={p.get('copilot_entry_buttons')}"
              f" {p.get('error','')}")
    total = sum(len(p.get("copilot_entry_buttons") or []) for p in out["pages"])
    print(f"合计 Copilot 业务入口按钮数 = {total}")
    return out


if __name__ == "__main__":
    m = sys.argv[1] if len(sys.argv) > 1 else "b5_copilot_page"
    {"b5_copilot_page": b5_copilot_page,
     "b6_menu_coverage": b6_menu_coverage}.get(m, lambda: print(__doc__))()
