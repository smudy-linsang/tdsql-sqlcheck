"""抽屉入口探针 v3：只做可观测行为取证（不依赖 Vue 内部结构）。"""
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
    out = {"utc": utc(), "errors": []}
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1680, "height": 1000})
        page = ctx.new_page()
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

        out["before"] = {
            "el_drawer_count": page.locator(".el-drawer").count(),
            "copilot_drawer_count": page.locator(".copilot-drawer").count(),
            "copilot_drawer_any": page.evaluate(
                "() => document.querySelectorAll('[class*=copilot]').length"),
            "text_copilot_drawer_present":
                page.get_by_text("Copilot 专家助手", exact=True).count(),
        }
        out["errors"].append("--- CLICK ---")
        page.get_by_test_id("copilot-open").click()
        time.sleep(3)
        out["after"] = {
            "el_drawer_count": page.locator(".el-drawer").count(),
            "copilot_drawer_count": page.locator(".copilot-drawer").count(),
            "copilot_drawer_any": page.evaluate(
                "() => document.querySelectorAll('[class*=copilot]').length"),
            "visible_overlays": page.evaluate(
                "() => Array.from(document.querySelectorAll('.el-overlay'))"
                ".filter(o => getComputedStyle(o).display !== 'none').length"),
            "drawers": page.evaluate(
                "() => Array.from(document.querySelectorAll('.el-drawer'))"
                ".map(d => ({cls: d.className,"
                " label: d.getAttribute('aria-label')||'',"
                " head: (d.querySelector('.el-drawer__title')||{}).innerText||'',"
                " display: getComputedStyle(d.closest('.el-overlay')||d).display}))"),
            "copilot_related_dom": page.evaluate(
                "() => Array.from(document.querySelectorAll('[class*=copilot]'))"
                ".slice(0,10).map(e => e.tagName + '.' + e.className)"),
        }
        page.screenshot(path=str(SHOTS / "dbg-drawer3.png"), full_page=False)
        out["shot"] = "docs/evidence/v1.6.4.0-uat-d/shots/dbg-drawer3.png"
        try:
            b.close()
        except Exception:
            pass
    save("dbg_drawer3", out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:5000])


if __name__ == "__main__":
    main()
