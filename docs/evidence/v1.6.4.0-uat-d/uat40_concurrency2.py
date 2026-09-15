"""智能体D / v1.6.4.0 UAT：GATE §5 第 2 条（修正版）—— 双用户占满 runner 并发。

修正点：
  · 单主体活动轮上限固定 1（COPILOT_MAX_ACTIVE_PER_USER=1），故用两个管理员
    账号各持 1 轮，才真正占满 runner 并发 2；
  · 元数据审核任务提交带 Idempotency-Key 头与正确字段名（connection_id/scopes）；
  · P95 采 3 轮取分布，不报单点。
"""
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from uat_d40_api import Api, save, set_gw_mode, utc  # noqa: E402

MAIN_PROBES = ["/api/v1/audit/metadata-jobs?limit=5", "/health"]
ROUNDS = 3
N = 15


def _sample(a, path, n=N):
    lat, status = [], []
    for _ in range(n):
        t0 = time.perf_counter()
        r = a.get(path)
        lat.append((time.perf_counter() - t0) * 1000)
        status.append(r["status"])
        time.sleep(0.03)
    lat.sort()
    return {"p50": round(statistics.median(lat), 1),
            "p95": round(lat[int(len(lat) * 0.95) - 1], 1),
            "max": round(max(lat), 1), "status_set": sorted(set(status))}


def _prep_and_submit(a, tag):
    s = a.post("/api/v1/copilot/sessions", json={
        "scope_kind": "GLOBAL_HELP", "instance_type": "unknown",
        "page_key": "copilot-page"})
    sid = (s.get("body") or {}).get("session_id")
    p = a.post(f"/api/v1/copilot/sessions/{sid}/previews", json={
        "expected_session_revision": 1, "scene": "USAGE_HELP",
        "page_key": "copilot-page",
        "question": f"并发门禁探针 {tag}：在线元数据审核怎么用？",
        "source_refs": []})
    b = p.get("body") or {}
    if p["status"] != 201:
        return {"preview_status": p["status"], "detail": b.get("detail")}
    r = a.post(f"/api/v1/copilot/sessions/{sid}/turns", json={
        "client_request_id": uuid.uuid4().hex,
        "preview_id": b["preview_id"], "snapshot_hash": b["snapshot_hash"],
        "expected_session_revision": b["expected_session_revision"],
        "confirm_data_use": True})
    return {"preview_status": 201, "submit_status": r["status"],
            "turn_id": (r.get("body") or {}).get("turn_id")}


def main():
    out = {"utc": utc(), "rounds": ROUNDS, "n_per_round": N}
    a = Api("uat_d_1640")
    b = Api("uat_d_1640b")
    a.relogin_after(2)
    b.relogin_after(2)

    out["baseline"] = {p: [_sample(a, p) for _ in range(ROUNDS)]
                       for p in MAIN_PROBES}
    print("基线：", json.dumps(out["baseline"], ensure_ascii=False))

    # 占满并发：两个账号各 1 轮，网关慢速保持 30s 在途
    set_gw_mode(8443, "slow")
    set_gw_mode(8444, "slow")
    time.sleep(0.5)
    out["turn_A"] = _prep_and_submit(a, "A")
    time.sleep(1)
    out["turn_B"] = _prep_and_submit(b, "B")
    act = a.get("/api/v1/copilot-admin/health").get("body") or {}
    out["health_under_load"] = act.get("runner")
    print("占用轮次：", json.dumps({k: out[k] for k in ("turn_A", "turn_B")},
                                   ensure_ascii=False))

    out["under_load"] = {p: [_sample(a, p) for _ in range(ROUNDS)]
                         for p in MAIN_PROBES}

    # 负载中的真实在线元数据审核任务
    job = a.client.post("/api/v1/audit/metadata-jobs",
                        json={"connection_id": "d40-dist",
                              "scopes": ["table", "column", "index"]},
                        headers={"Idempotency-Key": uuid.uuid4().hex})
    jb = {}
    try:
        jb = job.json()
    except Exception:
        jb = {"text": job.text[:200]}
    out["metadata_job_submit"] = {"status": job.status_code, "body": jb}
    jid = (jb or {}).get("job_id") if isinstance(jb, dict) else None
    out["metadata_job_id"] = jid
    if jid:
        t0 = time.time()
        final = {}
        while time.time() - t0 < 240:
            final = (a.get(f"/api/v1/audit/metadata-jobs/{jid}").get("body") or {})
            if final.get("state") in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(2)
        out["metadata_job_final"] = {
            "state": final.get("state"), "phase": final.get("phase"),
            "error_code": final.get("error_code"),
            "elapsed_s": round(time.time() - t0, 1)}
    print("元数据审核任务：", json.dumps(out.get("metadata_job_final"),
                                        ensure_ascii=False))

    set_gw_mode(8443, "ok")
    set_gw_mode(8444, "ok")

    out["degradation"] = {}
    for p in MAIN_PROBES:
        base = [r["p95"] for r in out["baseline"][p]]
        load = [r["p95"] for r in out["under_load"][p]]
        bm = statistics.median(base)
        lm = statistics.median(load)
        out["degradation"][p] = {
            "baseline_p95_rounds": base, "load_p95_rounds": load,
            "baseline_p95_median": round(bm, 1),
            "load_p95_median": round(lm, 1),
            "delta_pct": round((lm - bm) / bm * 100, 1) if bm else None,
            "abs_delta_ms": round(lm - bm, 1)}
    save("s8b_concurrency", out)
    print("\n=== P95 劣化（3 轮中位数）===")
    for p, v in out["degradation"].items():
        print(f"  {p:44s} {json.dumps(v, ensure_ascii=False)}")
    a.close()
    b.close()


if __name__ == "__main__":
    main()
