"""智能体D / v1.6.3.6 补丁复测：三种"悬挂残留"形态下前端是否仍被锁死。

用法：python probe_published_orphan_d36.py [published|accepted|running_stale]
  published     PUBLISHED 悬挂 + 槽无归属（UAT36-01 本体）
  accepted      ACCEPTED  幽灵 + 槽无归属（D-04 本体）
  running_stale RUNNING   + 心跳超 JOB_TIMEOUT（§2.1b：只暴露 stale_running，按钮须可用）
期望（修复后）：三种形态下任务卡照常展示，**已选实例后两个按钮均可用**，且给出对应提示。
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                   "SQLCHECK_DB_NAME": "uat_d_1636_meta", "AUTH_ENABLED": "true",
                   "REPORT_OUTPUT_DIR": str(ROOT / "data/reports/uat_d_1636/reports")})
sys.path.insert(0, str(ROOT))

from backend.services import metadata_audit_repository as R  # noqa: E402
from backend.services.database import _get_connection  # noqa: E402
from backend.services.metadata_audit_repository import repository as repo  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

HERE = Path(__file__).resolve().parent
RUNTIME = ROOT / "data/reports/uat_d_1636"
WEB = "http://127.0.0.1:8025"
ACCOUNT, PW = "uat_d_1636", (RUNTIME / "admin.password").read_text(encoding="utf-8").strip()
MODE = sys.argv[1] if len(sys.argv) > 1 else "published"

# ① 造悬挂任务
key = "orphan" + MODE + datetime.now(timezone.utc).strftime("%H%M%S")
job, _ = repo.create_job(
    created_by=ACCOUNT, request_id="D36-orphan", idempotency_key=key, request_hash=key,
    connection_id="d36-dist", db_name="uat_d_1636_dist",
    request_json=json.dumps({"connection_id": "d36-dist", "database": "uat_d_1636_dist",
                             "scopes": ["TABLE"]}),
    execution_context_json=json.dumps({"instance_type": "distributed",
                                       "connection_name": "D36-分布式库-含子表命名"}),
    connection_fingerprint="fp", report_deadline_seconds=1800)
jid = job["id"]
token = "tok" + key
repo.claim_next_accepted("runner-orphan", token, R._now())
results = [{"sql": "CREATE TABLE t (id INT)", "sql_type": "CREATE", "passed": True,
            "file_path": "f.sql", "line_number": 1, "violations": []}]
cols = ("extracted_schema", "extracted_orphan.sql", 1, 1, 0, 0, 0, 100.0,
        json.dumps(results, ensure_ascii=False), ACCOUNT, "", None, "",
        "2026-09-11 00:00:00.000000", "d36-dist", "uat_d_1636_dist", None,
        "distributed", "probe", 0, None, 0, 0, 0)
rid = None
if MODE == "published":
    repo.cas_state(jid, token, (R.STATE_RUNNING,), R.STATE_PUBLISHING, phase=R.PHASE_PERSISTING)
    rid = repo.publish(jid, token, audit_columns_values=cols,
                       results_json=json.dumps(results, ensure_ascii=False))
elif MODE == "running_stale":
    # 只回拨心跳/更新时间到远超 JOB_TIMEOUT(1800s)；**不动 created_at**，
    # 否则该任务会掉出前端兜底查询的"最新 20 条"窗口，测不到 stale_running 呈现面。
    past = (datetime.now(timezone.utc) - timedelta(seconds=7200)).strftime("%Y-%m-%d %H:%M:%S.%f")
    cx0 = _get_connection()
    cx0.execute("UPDATE metadata_audit_jobs SET updated_at=?, heartbeat_at=? WHERE id=?",
                (past, past, jid))
    cx0.commit(); cx0.close()
    cx0 = _get_connection()
    cx0.execute("UPDATE metadata_audit_slot SET active_job_id=? WHERE id=1", (jid,))
    cx0.commit(); cx0.close()
# accepted 模式：不做任何状态推进，也不让它进槽（槽保持空闲＝D-04 形态）

# ② 释放槽（published / accepted 两种形态：任务仍在、槽不指向它）
if MODE != "running_stale":
    cx = _get_connection()
    cx.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
    cx.commit(); cx.close()
j = repo.get_job(jid)
pre = {"mode": MODE, "job_id": jid, "report_id": rid, "state": j["state"], "phase": j["phase"],
       "slot_active_job": (repo.slot_state() or {}).get("active_job_id")}

# ③ 真实浏览器观察
out = {"prepared": pre}
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
    time.sleep(8)
    # 诊断：浏览器上下文里直接查"非终态任务"，判断前端恢复逻辑是否有可用线索
    out["api_list_nonterminal"] = page.evaluate("""async () => {
        const t = localStorage.getItem('tdsql_token') || '';
        const r = await fetch('/api/v1/audit/metadata-jobs?limit=20',
                              {headers: {'Authorization': 'Bearer ' + t}});
        const d = await r.json().catch(() => ({}));
        return (d.items || []).filter(j => !['SUCCEEDED','FAILED','CANCELLED'].includes(j.state))
            .map(j => ({job_id: j.job_id, state: j.state, slot_owned: j.slot_owned,
                        stale_running: j.stale_running}));
    }""")
    card = page.locator(".page-card", has_text="元数据审核任务 #")
    out["card_text"] = card.first.inner_text() if card.count() else ""
    out["page_has_task_card"] = card.count() > 0
    out["page_body_head"] = page.locator("body").inner_text()[:260]
    # 先选实例——否则「拉取」按钮因未选实例而禁用（正常行为），会掩盖 extractAuditing 的影响
    form = page.locator(".page-card", has_text="在线元数据提取与文件审核")
    form.locator(".el-select").first.click()
    page.locator('.el-select-dropdown__item:has-text("D36-分布式库-含子表命名")').first.click()
    page.keyboard.press("Escape")
    time.sleep(1)
    for label in ("拉取元数据并执行文件审核", "恢复查看上次任务"):
        loc = page.get_by_role("button", name=label)
        out[label] = {"count": loc.count(),
                      "disabled": loc.first.is_disabled() if loc.count() else None}
    page.screenshot(path=str(HERE / f"d36-b7-orphan-{MODE}.png"), full_page=True)
    b.close()

expects = {
    "published": "结果已落库但未收口",
    "accepted": "该任务已不在执行器上运行",
    "running_stale": "长时间无执行器心跳",
}
out["expected_hint"] = expects[MODE]
out["hint_present"] = expects[MODE] in out.get("card_text", "")
out["verdict"] = ("PASS 已修复：悬挂任务不再锁死界面（已选实例后两按钮均可用）"
                  if not out.get("拉取元数据并执行文件审核", {}).get("disabled")
                  and not out.get("恢复查看上次任务", {}).get("disabled")
                  and out["hint_present"]
                  else "FAIL 仍被锁死或提示缺失")

# ④ 收尾
if MODE == "published":
    repo.complete(jid, token, exit_code=0, cleanup_ok=True)
else:
    repo.cas_state(jid, token, (R.STATE_RUNNING, R.STATE_ACCEPTED), R.STATE_FAILED,
                   phase=R.PHASE_CLEANUP, error_code="RETEST_CLEANUP",
                   error_message="复测收尾", extra={"finished_at": R._now()})
cx = _get_connection()
cx.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
cx.commit(); cx.close()
out["cleanup"] = repo.get_job(jid)["state"]
(HERE / f"probe-orphan-{MODE}.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
