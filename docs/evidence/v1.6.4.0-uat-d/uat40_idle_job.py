"""对照：Copilot 空闲时同一在线元数据审核任务的耗时（与满并发态比较）。"""
import json
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from uat_d40_api import Api, save, utc  # noqa: E402


def main():
    out = {"utc": utc()}
    a = Api("uat_d_1640")
    a.relogin_after(2)
    t0 = time.time()
    job = a.client.post("/api/v1/audit/metadata-jobs",
                        json={"connection_id": "d40-dist",
                              "scopes": ["TABLE", "INDEX"]},
                        headers={"Idempotency-Key": uuid.uuid4().hex})
    try:
        jb = job.json()
    except Exception:
        jb = {"text": job.text[:300]}
    out["submit"] = {"status": job.status_code, "body": jb,
                     "at_s": round(time.time() - t0, 2)}
    jid = jb.get("job_id") if isinstance(jb, dict) else None
    final = {}
    accepted_at = time.time()
    if jid:
        while time.time() - t0 < 600:
            final = (a.get(f"/api/v1/audit/metadata-jobs/{jid}").get("body") or {})
            if final.get("state") in ("COMPLETED", "FAILED", "CANCELLED",
                                      "SUCCEEDED"):
                break
            time.sleep(2)
    out["final"] = {"state": final.get("state"), "phase": final.get("phase"),
                    "error_code": final.get("error_code"),
                    "total_s": round(time.time() - t0, 1)}
    save("s8d_idle_job", out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str)[:1200])
    a.close()


if __name__ == "__main__":
    main()
