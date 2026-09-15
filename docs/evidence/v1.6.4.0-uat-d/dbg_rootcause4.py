"""根因取证 v4：打印 Copilot 关键节点的完整祖先链，定位 HTML 结构错位点。"""
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

PROBE = r"""
async () => {
  const html = await (await fetch('/', {cache: 'no-store'})).text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const app = doc.querySelector('#app');
  const path = (el) => {
    const parts = [];
    let n = el;
    while (n && n !== app) {
      parts.unshift(n.tagName + (n.className ? '.' + String(n.className).split(' ')[0] : '')
                   + (n.getAttribute && n.getAttribute('data-testid')
                      ? '[' + n.getAttribute('data-testid') + ']' : ''));
      n = n.parentElement;
    }
    return parts.join(' > ');
  };
  const out = {};
  const picks = [
    ['copilot-open', '[data-testid="copilot-open"]'],
    ['copilot-page', '[data-testid="copilot-page"]'],
    ['copilot-drawer', '.copilot-drawer'],
    ['copilot-dialog', 'el-dialog:last-of-type'],
  ];
  out.paths = {};
  for (const [k, sel] of picks) {
    const el = app.querySelector(sel);
    out.paths[k] = el ? path(el) : 'NOT_FOUND';
  }
  // #app 直接子元素的 class / 属性前 80 字符
  out.app_children = Array.from(app.children).map(e => ({
    tag: e.tagName,
    cls: String(e.className || '').slice(0, 50),
    attrs: Array.from(e.attributes).map(a => a.name + '=' + a.value.slice(0, 30))
             .slice(0, 4).join(' '),
    child_count: e.children.length,
    text_head: (e.textContent || '').trim().slice(0, 60),
  }));
  // app-layout 是否包含 copilot-page
  const layout = app.querySelector('.app-layout');
  out.layout_contains_copilot_page = layout
      ? layout.contains(app.querySelector('[data-testid="copilot-page"]')) : null;
  out.layout_children_count = layout ? layout.children.length : null;
  out.layout_last_child = layout && layout.lastElementChild
      ? layout.lastElementChild.tagName + '.' +
        String(layout.lastElementChild.className || '').slice(0, 60) : null;
  // main 内容区容器
  const main = app.querySelector('.main-content, .content-area, main');
  out.main_info = main ? (main.tagName + '.' + String(main.className || '')) : null;
  out.main_children_count = main ? main.children.length : null;
  out.main_last_child = main && main.lastElementChild
      ? main.lastElementChild.tagName + '.' +
        String(main.lastElementChild.className || '').slice(0, 60) : null;
  return out;
}
"""


def main():
    out = {"utc": utc()}
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1680, "height": 1000})
        page = ctx.new_page()
        page.goto(WEB + "/", wait_until="domcontentloaded")
        page.get_by_test_id("login-username").fill("uat_d_1640")
        page.get_by_test_id("login-password").fill(creds()["uat_d_1640"])
        page.get_by_test_id("login-submit").click()
        page.wait_for_selector("text=治理概览", timeout=30000)
        time.sleep(2)
        out["parse"] = page.evaluate(PROBE)
        try:
            b.close()
        except Exception:
            pass
    save("dbg_rootcause4", out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:7000])


if __name__ == "__main__":
    main()
