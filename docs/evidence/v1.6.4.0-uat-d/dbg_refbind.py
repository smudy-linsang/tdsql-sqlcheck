"""根因取证 v6：模板中 `copilot.xxx` 绑定的是 Ref 对象本身（未被解包）的观测证据。

app.js：const copilot = createCopilotState({...}) 返回**普通对象包 ref**
（{drawerVisible: ref(false), resultVisible: ref(false), ...}），
模板里 v-model="copilot.drawerVisible" 取到的是 Ref 对象（恒真），
el-drawer/el-dialog 内部 watch(modelValue) 因“值从未变化”而不响应：
  · 首次渲染即视为打开（遮罩常驻、拦截点击）
  · 关闭按钮写回的是对象属性，不触发重新渲染
本脚本在**未登录的干净页**上取证，不做任何点击。
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))
import _boot  # noqa: F401,E402

from playwright.sync_api import sync_playwright  # noqa: E402

from uat_d40_api import save, utc  # noqa: E402

WEB = "http://127.0.0.1:8025"
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)

PROBE = r"""
() => {
  const vis = (el) => {
    if (!el) return false;
    const o = el.closest('.el-overlay') || el;
    return getComputedStyle(o).display !== 'none';
  };
  const drawers = Array.from(document.querySelectorAll('.el-drawer'));
  const dialogs = Array.from(document.querySelectorAll('.el-dialog'));
  const overlays = Array.from(document.querySelectorAll('.el-overlay'));
  return {
    url: location.pathname,
    logged_in: !!document.querySelector('.app-layout'),
    login_form_visible: !!document.querySelector('[data-testid="login-submit"]'),
    copilot_drawer_count: document.querySelectorAll('.copilot-drawer').length,
    copilot_drawer_visible: vis(document.querySelector('.copilot-drawer')),
    copilot_dialog_present: !!document.querySelector('.copilot-result-answer')
        || dialogs.some(d => (d.getAttribute('aria-label') || '') === 'Copilot 回答'),
    visible_dialogs: dialogs.filter(vis).map(d => d.getAttribute('aria-label')
        || (d.querySelector('.el-dialog__title') || {}).innerText || '?'),
    visible_drawers: drawers.filter(vis).map(d => d.getAttribute('aria-label')
        || (d.querySelector('.el-drawer__title') || {}).innerText || '?'),
    visible_overlays: overlays.filter(o => getComputedStyle(o).display !== 'none').length,
    blocked_by: (() => {
      const btn = document.querySelector('[data-testid="login-submit"]');
      if (!btn) return null;
      const r = btn.getBoundingClientRect();
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (!top || top === btn || btn.contains(top)) return null;
      const ov = top.closest('.el-overlay');
      return {tag: top.tagName, cls: String(top.className).slice(0, 60),
              overlay_label: ov ? (ov.querySelector('[aria-label]') || {}).getAttribute?.('aria-label') : null};
    })(),
  };
}
"""


def main():
    out = {"utc": utc()}
    with sync_playwright() as pw:
        for label, do_login in (("anonymous", False), ("after_login", True)):
            b = pw.chromium.launch(channel="chrome", headless=True)
            ctx = b.new_context(viewport={"width": 1680, "height": 1000})
            page = ctx.new_page()
            page.goto(WEB + "/", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            out[f"{label}_before"] = page.evaluate(PROBE)
            if do_login:
                try:
                    page.get_by_test_id("login-username").fill("uat_d_1640", timeout=5000)
                    from uat_d40_api import creds
                    page.get_by_test_id("login-password").fill(creds()["uat_d_1640"],
                                                               timeout=5000)
                    page.get_by_test_id("login-submit").click(timeout=5000)
                    page.wait_for_selector("text=治理概览", timeout=20000)
                    page.wait_for_timeout(2000)
                except Exception as e:
                    out[f"{label}_login_error"] = str(e)[:300]
                out[f"{label}_after"] = page.evaluate(PROBE)
            page.screenshot(path=str(SHOTS / f"dbg-refbind-{label}.png"),
                            full_page=False)
            out[f"{label}_shot"] = \
                f"docs/evidence/v1.6.4.0-uat-d/shots/dbg-refbind-{label}.png"
            b.close()
    save("dbg_refbind", out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:5000])


if __name__ == "__main__":
    main()
