"""列出侧边栏菜单项（供 u4 冒烟脚本定位），输出 JSON。"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNTIME = ROOT / "data/reports/uat_d_1636"
WEB = "http://127.0.0.1:8025"
ACCOUNT = "uat_d_1636"
PW = (RUNTIME / "admin.password").read_text(encoding="utf-8").strip()

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 1680, "height": 1000})
    pg = ctx.new_page()
    pg.goto(WEB + "/", wait_until="domcontentloaded")
    pg.get_by_test_id("login-username").fill(ACCOUNT)
    pg.get_by_test_id("login-password").fill(PW)
    pg.get_by_test_id("login-submit").click()
    pg.wait_for_selector(".sidebar", timeout=30000)
    time.sleep(2)
    out = {
        "menu_items": [e.inner_text().strip() for e in pg.locator(".el-menu-item").all()],
        "submenu_titles": [e.inner_text().strip()
                           for e in pg.locator(".el-sub-menu__title").all()],
        "body_len": len(pg.locator("body").inner_text()),
        "version_marks": pg.evaluate(
            "[...document.querySelectorAll('*')].filter(e=>e.children.length===0"
            "&&/v?1\\.6\\.3\\.7/.test(e.textContent)).map(e=>e.textContent.trim()).slice(0,8)"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    (HERE / "menu_probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    b.close()
