"""智能体D / v1.6.4.0 第二轮 UAT：真实浏览器复验（B01/B02/B05/M02/M03/N04）。

判据：
  B01 未登录页无 Copilot 游离节点；copilot 元素祖先链不含 EL-DIALOG；登录可用
  B02 登录后抽屉/结果框默认关闭；点页头图标能打开抽屉
  B05 「AI配置」页渲染出模型/路由/授权页签
  M03 侧栏进会话页后模式显示正确、实例下拉有选项
  N04 抽屉内有会话选择与新建按钮且可用
  M02 业务结果区 Copilot 入口（预期仍为 0 → 未修复）
"""
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(R1))
sys.path.insert(0, str(ROOT))
import _boot  # noqa: F401,E402

from playwright.sync_api import sync_playwright  # noqa: E402

from uat_d40_api import creds, utc  # noqa: E402

WEB = "http://127.0.0.1:8025"
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)


def ev(page, body, default=None):
    """安全求值：body 为函数体（不带外层括号），异常返回 default。"""
    src = "() => { try { " + body + " } catch (e) { return 'ERR:' + String(e); } }"
    try:
        return page.evaluate(src)
    except Exception as e:
        return default if default is not None else f"EVAL_ERR:{str(e)[:160]}"


VIS = """
  const visible = (el) => { if (!el) return false;
    const o = el.closest('.el-overlay') || el;
    return getComputedStyle(o).display !== 'none'; };
  const pathOf = (el) => { const p = []; let n = el;
    while (n && n.id !== 'app') {
      p.unshift(n.tagName + (n.className ? '.' + String(n.className).split(' ')[0] : ''));
      n = n.parentElement; }
    return p.join(' > '); };
  const cpPage = document.querySelector("[data-testid='copilot-page']");
  const admPage = document.querySelector("[data-testid='copilot-admin-page']");
  return {
    overlays_visible: Array.from(document.querySelectorAll('.el-overlay'))
      .filter(o => getComputedStyle(o).display !== 'none').length,
    copilot_nodes: document.querySelectorAll('[class*=copilot]').length,
    copilot_drawer_visible: visible(document.querySelector('.copilot-drawer')),
    copilot_page_path: cpPage ? pathOf(cpPage) : null,
    admin_page_path: admPage ? pathOf(admPage) : null,
  };
"""


class S:
    def __init__(self, pw, login=True):
        self.b = pw.chromium.launch(channel="chrome", headless=True)
        self.ctx = self.b.new_context(viewport={"width": 1680, "height": 1000},
                                      accept_downloads=True)
        self.p = self.ctx.new_page()
        self.errs = []
        self.p.on("pageerror", lambda e: self.errs.append("pageerror: " + str(e)[:250]))
        self.p.on("console", lambda m: self.errs.append(f"{m.type}: {m.text[:250]}")
                  if m.type == "error" else None)
        if login:
            self.login()

    def shot(self, n):
        self.p.screenshot(path=str(SHOTS / f"{n}.png"), full_page=True)
        return f"docs/evidence/v1.6.4.0-uat2-d/shots/{n}.png"

    def login(self):
        p = self.p
        p.goto(WEB + "/", wait_until="domcontentloaded")
        p.wait_for_timeout(2000)
        p.get_by_test_id("login-username").fill("uat_d_1640")
        p.get_by_test_id("login-password").fill(creds()["uat_d_1640"])
        p.get_by_test_id("login-submit").click()
        p.wait_for_selector("text=治理概览", timeout=30000)
        time.sleep(2)

    def menu(self, text):
        """导航到菜单项：若在折叠的子菜单里，先展开父级。"""
        p = self.p
        item = p.locator(".el-menu-item", has_text=text).first
        try:
            if not item.is_visible():
                raise RuntimeError("hidden")
        except Exception:
            # 逐个子菜单尝试展开，直到目标可见
            titles = p.locator(".el-sub-menu__title")
            for i in range(titles.count()):
                try:
                    titles.nth(i).click(timeout=3000)
                    time.sleep(0.6)
                    if p.locator(".el-menu-item", has_text=text).first.is_visible():
                        break
                except Exception:
                    continue
        p.locator(".el-menu-item", has_text=text).first.click(timeout=10000)
        time.sleep(3)

    def close(self):
        try:
            self.b.close()
        except Exception:
            pass


def main():
    out = {"utc": utc()}
    try:
        _run(out)
    except Exception as e:
        out["fatal"] = str(e)[:500]
    finally:
        (HERE / "r2_browser.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        for k, v in out.items():
            if k != "console_errors":
                print(f"{k}: {json.dumps(v, ensure_ascii=False, default=str)[:400]}")


def _run(out):
    with sync_playwright() as pw:
        # ── B01：未登录首屏 ──
        s = S(pw, login=False)
        s.p.goto(WEB + "/", wait_until="domcontentloaded")
        s.p.wait_for_timeout(2500)
        out["B01_anonymous"] = ev(s.p, VIS)
        try:
            s.p.get_by_test_id("login-username").fill("uat_d_1640", timeout=6000)
            out["B01_login_form_reachable"] = True
            try:
                s.p.get_by_test_id("login-password").fill(creds()["uat_d_1640"],
                                                          timeout=6000)
                s.p.get_by_test_id("login-submit").click(timeout=6000)
                s.p.wait_for_selector("text=治理概览", timeout=25000)
                out["B01_login_succeeded"] = True
            except Exception as e:
                out["B01_login_succeeded"] = False
                out["B01_login_err"] = str(e)[:200]
        except Exception as e:
            out["B01_login_form_reachable"] = False
            out["B01_err"] = str(e)[:200]
        out["B01_shot"] = s.shot("r2-b01-anonymous")
        s.close()

        # ── B02 / N04 / M03 / B05 / M02 ──
        s = S(pw)
        out["B02_after_login"] = ev(s.p, VIS)
        s.p.get_by_test_id("copilot-open").click()
        time.sleep(3)
        out["B02_drawer_after_click"] = ev(s.p, VIS)
        drawer = s.p.query_selector(".copilot-drawer")
        out["B02_drawer_text"] = (drawer.inner_text()[:400] if drawer else "")
        out["B02_shot"] = s.shot("r2-b02-drawer")

        out["N04_has_session_select"] = ev(s.p, """
            return !!document.querySelector('.copilot-drawer .el-select');""")
        out["N04_has_new_session_btn"] = ev(s.p, """
            return Array.from(document.querySelectorAll('.copilot-drawer button'))
              .some(b => (b.innerText || '').includes('新建会话'));""")
        try:
            s.p.locator(".copilot-drawer").get_by_role(
                "button", name="新建会话").first.click(timeout=8000)
            out["N04_new_session_clicked"] = True
            time.sleep(3)
        except Exception as e:
            out["N04_new_session_clicked"] = False
            out["N04_click_err"] = str(e)[:200]
        out["N04_select_values"] = ev(s.p, """
            return Array.from(document.querySelectorAll('.copilot-drawer .el-select'))
              .map(x => { const i = x.querySelector('input'); return i ? i.value : ''; });""")
        out["N04_drawer_text_after"] = (
            (s.p.query_selector(".copilot-drawer") or
             type("x", (), {"inner_text": lambda: ""})()).inner_text()[:400])
        out["N04_shot"] = s.shot("r2-n04-drawer-session")
        try:
            s.p.locator(".copilot-drawer").get_by_role(
                "button", name="关闭").first.click(timeout=6000)
            time.sleep(1)
        except Exception:
            pass

        # M03：会话页
        s.menu("Copilot专家助手")
        out["M03_page_rendered"] = s.p.locator(
            "[data-testid='copilot-page']").count() > 0
        out["M03_mode_tags"] = ev(s.p, """
            return Array.from(document.querySelectorAll(
              "[data-testid='copilot-page'] .el-tag")).map(e => e.innerText.trim());""")
        out["M03_shot"] = s.shot("r2-m03-copilot-page")
        try:
            s.p.locator("[data-testid='copilot-page'] .el-select").first.click()
            time.sleep(1.5)
            out["M03_instance_options"] = [
                e.inner_text().strip() for e in
                s.p.query_selector_all(".el-select-dropdown__item") if e.is_visible()]
            s.p.keyboard.press("Escape")
            time.sleep(1)
        except Exception as e:
            out["M03_instance_err"] = str(e)[:200]
        out["M03_page_eval"] = ev(s.p, VIS)
        out["M03_api_mode"] = ev(s.p, """
            const t = localStorage.getItem('tdsql_token') || '';
            const r = await fetch('/api/v1/copilot/capabilities',
                t ? {headers: {Authorization: 'Bearer ' + t}} : {});
            return r.ok ? (await r.json()).mode : ('HTTP ' + r.status);""")

        # B05：AI配置页
        s.menu("AI配置")
        out["B05_page_rendered"] = s.p.locator(
            "[data-testid='copilot-admin-page']").count() > 0
        adm = s.p.query_selector("[data-testid='copilot-admin-page']")
        out["B05_text"] = adm.inner_text()[:700] if adm else ""
        out["B05_tabs"] = ev(s.p, """
            return Array.from(document.querySelectorAll(
              "[data-testid='copilot-admin-page'] .el-tabs__item"))
              .map(e => e.innerText.trim());""")
        out["B05_page_eval"] = ev(s.p, VIS)
        out["B05_shot"] = s.shot("r2-b05-ai-config")

        # M02：业务页 Copilot 入口扫描
        pat = re.compile(r"解读|解释这条|排查|修改建议|让 ?Copilot|Copilot ?解读|分析失败")
        scan = []
        for name in ("即时审核", "文件审核", "在线元数据审核", "审核规则库",
                     "慢SQL记录", "上线检查", "大表治理"):
            try:
                s.menu(name)
                hits = sorted({(e.inner_text() or "").strip()
                               for e in s.p.query_selector_all("button, .el-button")
                               if e.is_visible() and
                               pat.search((e.inner_text() or "").strip() or "") and
                               len((e.inner_text() or "").strip()) < 30})
                scan.append({"page": name, "copilot_entries": hits})
            except Exception as e:
                scan.append({"page": name, "error": str(e)[:120]})
        out["M02_scan"] = scan
        out["M02_total_entries"] = sum(len(x.get("copilot_entries") or [])
                                       for x in scan)
        out["console_errors"] = s.errs[-8:]
        s.close()


if __name__ == "__main__":
    main()
