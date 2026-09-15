"""根因取证 v3：用 DOMParser 复现浏览器 HTML 解析，定位模板截断/移出点。"""
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
  const out = {};
  out.html_len = html.length;
  out.app_found = !!app;
  if (!app) return out;
  out.app_text_len = app.outerHTML.length;
  const q = (sel) => !!app.querySelector(sel);
  out.inside_app = {
    copilot_open: q('[data-testid="copilot-open"]'),
    copilot_page: q('[data-testid="copilot-page"]'),
    copilot_drawer: q('.copilot-drawer'),
    copilot_dialog: q('.copilot-result-answer') || q('.el-dialog'),
    rules_page: q('[data-testid="copilot-page"]'),
    gateway_drawer: !!app.querySelector('.el-drawer[aria-label]'),
  };
  // 列出 #app 的直接子元素（渲染前的静态结构）
  out.app_children = Array.from(app.children).map(
      e => e.tagName + '.' + (e.className || '') + '#' + (e.id || ''));
  // 页面里所有 copilot 相关元素，以及它们的最近祖先是否为 #app
  const all = Array.from(doc.querySelectorAll('[class*=copilot], [data-testid*=copilot]'));
  out.copilot_nodes_total = all.length;
  out.copilot_nodes = all.slice(0, 20).map(e => ({
      tag: e.tagName, cls: (e.className || '').toString().slice(0, 60),
      in_app: app.contains(e),
      parent: e.parentElement ? (e.parentElement.tagName + '.' +
              (e.parentElement.className || '').toString().slice(0, 40)) : null,
  }));
  // 找出 #app 之后（body 下、#app 之外）的元素
  out.body_children = Array.from(doc.body.children).map(
      e => e.tagName + '.' + (e.className || '').toString().slice(0, 40));
  // #app 内最后一个元素，用来判断截断点
  const last = app.lastElementChild;
  out.app_last_child = last ? (last.tagName + '.' +
      (last.className || '').toString().slice(0, 80)) : null;
  // 与文件名相关的标记：Copilot 页/抽屉所在行区间是否被解析进 #app
  out.has_gateway_drawer = !!app.querySelector('.el-drawer');
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
    save("dbg_rootcause3", out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:7000])


if __name__ == "__main__":
    main()
