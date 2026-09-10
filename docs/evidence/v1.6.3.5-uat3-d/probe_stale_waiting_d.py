"""智能体D / v1.6.3.5 UAT：过期未认领任务的回收判定（O 报告 R2-01 第5条）。

受控复现：runner 停止期间，用 repository 直接受理一个任务（等价于"受理后执行器
在认领前死亡"），观察 30 秒启动期限之后系统是否判失败并释放唯一槽。

证据边界：这是**状态级受控复现**，不是"受理瞬间杀掉 runner"的竞态复现；
它检验的是"过期 WAITING 是否有回收方"这一代码事实。
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                   "SQLCHECK_DB_NAME": "uat_d_1635_r3_meta", "AUTH_ENABLED": "true"})
sys.path.insert(0, str(ROOT))

from backend.services.metadata_audit_repository import repository  # noqa: E402
from backend.services import metadata_job_process as jp  # noqa: E402

HERE = Path(__file__).resolve().parent
KEY = "dstale" + datetime.now(timezone.utc).strftime("%H%M%S")

job, created = repository.create_job(
    created_by="uat_d_1635_r3", request_id="D-stale-probe",
    idempotency_key=KEY, request_hash="0" * 64,
    connection_id="d-r3-local", db_name="uat_d_1635_r3_target",
    request_json=json.dumps({"scopes": ["TABLE"]}),
    execution_context_json=json.dumps({"connection_name": "D-UAT3-63表-本机模拟目标"}),
    connection_fingerprint="0" * 64, report_deadline_seconds=1800)

jid = job["id"]
samples = []
t0 = time.time()
for i in range(10):
    time.sleep(5)
    j = repository.get_job(jid)
    slot = repository.slot_state()
    samples.append({
        "t_plus_s": int(time.time() - t0),
        "state": j["state"], "phase": j["phase"],
        "slot_active_job": (slot or {}).get("active_job_id"),
        "start_deadline_s": jp.MetadataLimits.START_TIMEOUT,
    })

out = {"probe": "stale_waiting_recovery", "created_new": created, "job_id": jid,
       "deadline_at": job.get("deadline_at"), "samples": samples,
       "still_waiting_after_50s": samples[-1]["state"] == "ACCEPTED",
       "slot_still_occupied": samples[-1]["slot_active_job"] == jid,
       "verdict": ("过期未认领任务未被回收，唯一槽持续被占用"
                   if samples[-1]["state"] == "ACCEPTED" and samples[-1]["slot_active_job"] == jid
                   else "已被回收")}

# 收尾：把探针任务置失败并释放槽，避免污染后续场景
try:
    repository.cas_state(jid, job.get("attempt_token") or "", ("ACCEPTED",),
                         "FAILED", phase="CLEANUP", error_code="PROBE_CLEANUP",
                         error_message="D-UAT3 探针收尾")
    repository.release_slot(jid)
    out["cleanup"] = "已置 FAILED 并释放槽"
except Exception as e:  # noqa: BLE001
    # attempt_token 为空时 CAS 不命中，直接释放槽
    repository.release_slot(jid)
    out["cleanup"] = f"直接释放槽（{type(e).__name__}）"

(HERE / "probe-stale-waiting.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
