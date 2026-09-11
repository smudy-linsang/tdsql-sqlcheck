"""智能体D / v1.6.3.6 UAT：受控复现「PUBLISHED 悬挂任务锁死前端」。

步骤：① 造一个已发布（PUBLISHED）但未 complete 的任务；② 让唯一槽不指向它
（等价于 runner 在 complete() 未命中后仍执行了 release_slot，或人工清槽/升级残留）；
③ 用真实浏览器进入在线元数据审核页，观察任务卡与两个按钮的状态；④ 收尾清理。
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
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

# ① 造 PUBLISHED 悬挂任务
key = "orphan" + datetime.now(timezone.utc).strftime("%H%M%S")
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
repo.cas_state(jid, token, (R.STATE_RUNNING,), R.STATE_PUBLISHING, phase=R.PHASE_PERSISTING)
rid = repo.publish(jid, token, audit_columns_values=cols,
                   results_json=json.dumps(results, ensure_ascii=False))
# ② 释放槽（任务仍停在 PUBLISHED）
cx = _get_connection()
cx.execute("UPDATE metadata_audit_slot SET active_job_id=NULL WHERE id=1")
cx.commit(); cx.close()
j = repo.get_job(jid)
pre = {"job_id": jid, "report_id": rid, "state": j["state"], "phase": j["phase"],
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
    time.sleep(5)
    card = page.locator(".page-card", has_text="元数据审核任务 #")
    out["card_text"] = card.first.inner_text() if card.count() else ""
    for label in ("拉取元数据并执行文件审核", "恢复查看上次任务"):
        loc = page.get_by_role("button", name=label)
        out[label] = {"count": loc.count(),
                      "disabled": loc.first.is_disabled() if loc.count() else None}
    page.screenshot(path=str(HERE / "d36-b7-published-orphan.png"), full_page=True)
    b.close()

out["verdict"] = ("REPRO 命中：PUBLISHED 悬挂 + 槽无归属 → 两个按钮同时禁用，用户无法自救"
                  if out.get("拉取元数据并执行文件审核", {}).get("disabled")
                  and out.get("恢复查看上次任务", {}).get("disabled")
                  else "未复现")

# ④ 收尾：把悬挂任务收敛为 SUCCEEDED（保留审计痕迹）
repo.complete(jid, token, exit_code=0, cleanup_ok=True)
out["cleanup"] = repo.get_job(jid)["state"]
(HERE / "probe-published-orphan.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
