"""智能体D / v1.6.4.0 第三轮 UAT：业务入口真实可用性（走完整用户流程）。

入口按钮是条件渲染（需先有审核/任务结果），因此必须真实产生结果后再验证：
  ① 即时审核：输入 SQL → 审核 → 期望出现「生成修改建议」「解释违规规则」
  ② 审核规则库：规则列表行内「解释」
  ③ 在线元数据审核：提交任务 → SUCCEEDED → 期望出现「让 Copilot 解读」
并点击入口，验证抽屉打开且上下文（page_key / source_refs）确实带入。
"""
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
ROOT = HERE.parents[2]
for p in (str(HERE), str(R1), str(ROOT)):
    sys.path.insert(0, p)
import _boot  # noqa: F401,E402

from playwright.sync_api import sync_playwright  # noqa: E402

from uat_d40_api import creds, utc  # noqa: E402

WEB = "http://127.0.0.1:8025"
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)
ENTRY_PAT = re.compile(r"解读|解释|修改建议|让 ?Copilot")


def ev(page, body, default="EVAL_ERR"):
    src = "() => { try { " + body + " } catch (e) { return 'ERR:' + String(e); } }"
    try:
        return page.evaluate(src)
    except Exception as e:
        return f"{default}:{str(e)[:120]}"


class S:
    def __init__(self, pw):
        self.b = pw.chromium.launch(channel="chrome", headless=True)
        self.ctx = self.b.new_context(viewport={"width": 1680, "height": 1000})
        self.p = self.ctx.new_page()
        self.errs = []
        self.p.on("pageerror", lambda e: self.errs.append("pageerror: " + str(e)[:200]))

    def shot(self, n):
        self.p.screenshot(path=str(SHOTS / f"{n}.png"), full_page=True)
        return f"docs/evidence/v1.6.4.0-uat3-d/shots/{n}.png"

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
        p = self.p
        item = p.locator(".el-menu-item", has_text=text).first
        try:
            if not item.is_visible():
                raise RuntimeError
        except Exception:
            for i in range(p.locator(".el-sub-menu__title").count()):
                try:
                    p.locator(".el-sub-menu__title").nth(i).click(timeout=2500)
                    time.sleep(0.5)
                    if p.locator(".el-menu-item", has_text=text).first.is_visible():
                        break
                except Exception:
                    continue
        p.locator(".el-menu-item", has_text=text).first.click(timeout=10000)
        time.sleep(3)

    def entries(self):
        return sorted({(e.inner_text() or "").strip()
                       for e in self.p.query_selector_all("button, .el-button, a")
                       if e.is_visible()
                       and ENTRY_PAT.search((e.inner_text() or "").strip() or "")
                       and len((e.inner_text() or "").strip()) < 20})

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
        out["fatal"] = str(e)[:400]
    finally:
        (HERE / "r3_entries.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        for k, v in out.items():
            print(f"{k}: {json.dumps(v, ensure_ascii=False, default=str)[:280]}")


def _run(out):
    with sync_playwright() as pw:
        s = S(pw)
        s.login()

        # ── ① 审核规则库：行内「解释」（立即渲染）──
        s.menu("审核规则库")
        time.sleep(2)
        out["rules_entries"] = s.entries()
        out["rules_shot"] = s.shot("r3-entry-rules")

        # ── ② 即时审核：产生结果 → 期望入口出现 ──
        s.menu("即时审核")
        time.sleep(2)
        filled = False
        try:
            ta = s.p.query_selector("textarea")
            if ta:
                ta.fill("SELECT id, name FROM d40_tab_00 WHERE id = 100")
                filled = True
        except Exception as e:
            out["instant_fill_err"] = str(e)[:150]
        out["instant_sql_filled"] = filled
        for label in ("开始审核", "立即审核", "审核"):
            try:
                s.p.get_by_role("button", name=re.compile(label)).first.click(timeout=4000)
                break
            except Exception:
                continue
        time.sleep(8)
        out["instant_entries"] = s.entries()
        out["instant_has_result"] = ev(
            s.p, "return document.body.innerText.includes('审核结果');")
        out["instant_shot"] = s.shot("r3-entry-instant")

        # 点第一个入口，验证抽屉与上下文
        if out["instant_entries"]:
            try:
                s.p.get_by_role("button", name=out["instant_entries"][0]).first.click(
                    timeout=6000)
                time.sleep(3)
                drawer = s.p.query_selector(".copilot-drawer")
                out["drawer_opened_by_entry"] = bool(
                    drawer and drawer.is_visible())
                out["drawer_text"] = drawer.inner_text()[:300] if drawer else ""
                out["drawer_shot"] = s.shot("r3-entry-drawer")
                # 关闭抽屉，避免遮罩拦截后续操作
                try:
                    s.p.locator(".copilot-drawer").get_by_role(
                        "button", name="关闭").first.click(timeout=5000)
                except Exception:
                    s.p.keyboard.press("Escape")
                time.sleep(1.5)
            except Exception as e:
                out["entry_click_err"] = str(e)[:200]

        # ── ③ 在线元数据审核：提交任务 → SUCCEEDED → 期望入口 ──
        s.menu("在线元数据审核")
        time.sleep(2)
        try:
            sel = s.p.locator(".page-card .el-select").first
            sel.click()
            time.sleep(1.2)
            opts = [e for e in s.p.query_selector_all(".el-select-dropdown__item")
                    if e.is_visible()]
            if opts:
                opts[0].click()
            time.sleep(1)
            s.p.get_by_role("button", name=re.compile("开始|提取|审核")).first.click(
                timeout=6000)
            for _ in range(40):
                time.sleep(3)
                if "SUCCEEDED" in (s.p.inner_text("body") or ""):
                    break
            time.sleep(2)
            out["metadata_entries"] = s.entries()
            out["metadata_shot"] = s.shot("r3-entry-metadata")
        except Exception as e:
            out["metadata_err"] = str(e)[:200]

        out["page_errors"] = s.errs[-6:]
        s.close()

    (HERE / "r3_entries.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for k, v in out.items():
        print(f"{k}: {json.dumps(v, ensure_ascii=False, default=str)[:280]}")


if __name__ == "__main__":
    main()
