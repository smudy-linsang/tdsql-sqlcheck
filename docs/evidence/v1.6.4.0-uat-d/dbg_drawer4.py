"""抽屉入口探针 v4：以 HTTP 请求为判据，判定点击是否触发 openDrawer。"""
import json
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


def main():
    out = {"utc": utc(), "net": [], "errors": []}
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1680, "height": 1000})
        page = ctx.new_page()
        page.on("request", lambda r: out["net"].append(
            {"phase": "req", "m": r.method, "u": r.url.replace(WEB, "")})
            if "/api/v1/copilot" in r.url else None)
        page.on("response", lambda r: out["net"].append(
            {"phase": "res", "s": r.status, "u": r.url.replace(WEB, "")})
            if "/api/v1/copilot" in r.url else None)
        page.on("pageerror", lambda e: out["errors"].append(
            "pageerror: " + str(e)[:400]))
        page.on("console", lambda m: out["errors"].append(
            f"console.{m.type}: {m.text[:300]}") if m.type == "error" else None)

        page.goto(WEB + "/", wait_until="domcontentloaded")
        page.get_by_test_id("login-username").fill("uat_d_1640")
        page.get_by_test_id("login-password").fill(creds()["uat_d_1640"])
        page.get_by_test_id("login-submit").click()
        page.wait_for_selector("text=治理概览", timeout=30000)
        time.sleep(2)
        out["net_before_click"] = list(out["net"])

        # 定位入口元素并点击
        el = page.get_by_test_id("copilot-open")
        out["entry_visible"] = el.first.is_visible()
        out["entry_box"] = el.first.bounding_box()
        el.first.click()
        time.sleep(3)
        out["net_after_click"] = out["net"][len(out["net_before_click"]):]
        out["drawer_after_click"] = page.evaluate(
            "() => document.querySelectorAll('[class*=copilot]').length")

        # 对照：点击侧栏菜单项（已知可用）验证点击链路
        out["net"].clear()
        try:
            page.get_by_text("Copilot专家助手", exact=True).first.click()
            time.sleep(2.5)
        except Exception as e:
            out["sidebar_click_error"] = str(e)[:200]
        out["net_sidebar_click"] = list(out["net"])
        out["copilot_page_in_dom"] = page.locator(
            "[data-testid='copilot-page']").count()
        page.screenshot(path=str(SHOTS / "dbg-drawer4.png"), full_page=True)
        out["shot"] = "docs/evidence/v1.6.4.0-uat-d/shots/dbg-drawer4.png"
        try:
            b.close()
        except Exception:
            pass
    save("dbg_drawer4", out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:6000])


if __name__ == "__main__":
    main()
