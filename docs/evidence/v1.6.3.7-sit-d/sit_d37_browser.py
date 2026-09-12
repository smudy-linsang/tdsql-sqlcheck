"""智能体D / v1.6.3.7 SIT：历史元数据审核记录页真实浏览器核验。

核验点：①「报告ID」列是否出现；②「提取生成的 SQL 文件」列展示还原后的时间戳命名；
③ 按"开始日期=今天"筛选能否命中本次整改后的新记录（本地时间口径）。
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
ACCOUNT, PW = "uat_d_1636", (RUNTIME / "admin.password").read_text(encoding="utf-8").strip()
RESULT = {}


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_context(viewport={"width": 1680, "height": 1000}).new_page()
        page.goto(WEB + "/", wait_until="domcontentloaded")
        page.get_by_test_id("login-username").fill(ACCOUNT)
        page.get_by_test_id("login-password").fill(PW)
        page.get_by_test_id("login-submit").click()
        page.wait_for_selector("text=治理概览", timeout=30000)
        page.get_by_text("SQL审核", exact=True).click()
        page.get_by_text("在线元数据审核", exact=True).click()
        page.wait_for_selector("text=在线元数据提取与文件审核", timeout=20000)
        page.get_by_text("历史元数据审核记录", exact=True).click()
        time.sleep(4)
        page.screenshot(path=str(HERE / "sit-history-list.png"), full_page=True)

        headers = [h.inner_text().strip() for h in
                   page.locator(".el-table__header-wrapper th").all()]
        RESULT["headers_has_report_id"] = "报告ID" in headers
        RESULT["headers_head"] = headers[:12]
        rows = []
        for tr in page.locator(".el-table__body-wrapper tbody tr").all()[:5]:
            rows.append([c.inner_text().strip() for c in tr.locator("td").all()])
        RESULT["rows_head"] = rows
        names = [c for r in rows for c in r if c.endswith(".sql")]
        RESULT["names"] = names
        RESULT["names_all_timestamp_form"] = all(
            re.match(r"^extracted_.+_\d{8}_\d{6}\.sql$", n) for n in names) if names else None
        RESULT["any_uuid_form"] = any(re.search(r"_[0-9a-f]{8}\.sql$", n) for n in names)

        # 按"开始日期=今天"筛选（本地时间口径）
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            page.locator('input[placeholder="开始日期"]').first.fill(today)
            page.keyboard.press("Enter")
            page.get_by_role("button", name="查询").first.click()
            time.sleep(3)
            page.screenshot(path=str(HERE / "sit-history-filter-today.png"), full_page=True)
            cnt = page.locator(".el-table__body-wrapper tbody tr").count()
            RESULT["filter_today"] = {"date": today, "row_count": cnt}
        except Exception as e:  # noqa: BLE001
            RESULT["filter_today"] = {"error": str(e)[:160]}
        b.close()
    RESULT["verdict"] = ("PASS 历史列表展示还原命名且可按当天筛选"
                         if RESULT.get("names_all_timestamp_form")
                         and RESULT.get("filter_today", {}).get("row_count", 0) > 0
                         else "FAIL/需人工判读")
    (HERE / "sit-browser.json").write_text(json.dumps(RESULT, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    print(json.dumps(RESULT, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
