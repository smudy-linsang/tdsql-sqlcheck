"""智能体D / v1.6.4.0 第四轮 UAT：复测第三轮 4 项修复。

① R2-B03（阻断）：自检 → **断言 tested_revision 被写入** → 启用 200 → 确有出站
② R3-M01：阈值 9.0 + 相对保留 —— 在域命中率与越界拒绝率同时达标
③ R3-M02：标记存在时受理路径被拒
④ M01 防回退：出站仍逐字节等于冻结投影
"""
import json
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
R2 = HERE.parents[1] / "evidence/v1.6.4.0-uat2-d"
ROOT = HERE.parents[2]
for p in (str(HERE), str(R1), str(R2), str(ROOT)):
    sys.path.insert(0, p)
import _boot  # noqa: F401,E402

from uat_d40_api import (Api, gateway_events, save, utc, wait_turn)  # noqa: E402
from uat2_d40_core import _decrypt_projection, _outbound  # noqa: E402

CAPS = {"context_tokens": 32768, "max_output_field": "max_tokens",
        "supports_temperature": True, "supports_json_schema": False,
        "supports_json_object": True, "supports_store_false": True}
REAL_TABLE = "d40_tab_00"


# ══════════════════════════════════════════════════════════════════
# ① 自检端到端（必须断言 tested_revision 被写入）
# ══════════════════════════════════════════════════════════════════
def selftest_e2e(a):
    out = {"steps": []}
    name = f"UAT4-自检-{uuid.uuid4().hex[:6]}"
    r = a.post("/api/v1/copilot-admin/providers", json={
        "name": name, "endpoint_id": "ep-uat-a",
        "protocol": "OPENAI_COMPAT_CHAT", "model_id": "uat4-selftest",
        "auth_mode": "BEARER", "capabilities": CAPS,
        "secret_action": "REPLACE", "secret": "sk-uat4"})
    out["steps"].append({"POST /providers": {"status": r["status"],
                                             "body": r.get("body")}})
    pid = (r.get("body") or {}).get("id")
    out["provider_id"] = pid
    out["provider_name"] = name
    if not pid:
        return out

    en1 = a.put(f"/api/v1/copilot-admin/providers/{pid}/enabled",
                json={"expected_revision": 1, "enabled": True})
    out["steps"].append({"PUT enabled(未自检)": {
        "status": en1["status"],
        "code": ((en1.get("body") or {}).get("detail") or {}).get("code")
        if isinstance((en1.get("body") or {}).get("detail"), dict) else None}})

    mark = len(gateway_events())
    st = a.post(f"/api/v1/copilot-admin/providers/{pid}/self-tests",
                json={"client_request_id": uuid.uuid4().hex,
                      "expected_provider_revision": 1})
    out["steps"].append({"POST /self-tests": {"status": st["status"]}})
    tid = (st.get("body") or {}).get("turn_id")
    out["selftest_turn_id"] = tid
    term = wait_turn(a, tid, timeout=150) if tid else {}
    tb = term.get("body") or {}
    out["selftest_terminal"] = tb.get("state")
    out["selftest_error"] = tb.get("error_code")

    evs = [_outbound(e) for e in gateway_events()[mark:]]
    out["selftest_outbound_count"] = len(evs)
    if evs:
        out["selftest_outbound"] = {
            "port": evs[0]["port"], "keys": evs[0]["payload_keys"],
            "has_real_identifier": REAL_TABLE in evs[0]["body"],
            "has_tools": '"tools"' in evs[0]["body"],
            "question_head": str(evs[0]["payload"].get("question"))[:80]}

    prov = (a.get("/api/v1/copilot-admin/providers").get("body") or {}).get(
        "items", [])
    item = next((p for p in prov if p["id"] == pid), {})
    out["provider_after_selftest"] = {k: item.get(k) for k in
                                      ("enabled", "revision", "tested_revision")}
    # ★ 关键断言：tested_revision 必须被写入且等于 revision
    out["tested_revision_written"] = (
        item.get("tested_revision") is not None
        and int(item.get("tested_revision") or -1) == int(item.get("revision") or 0))

    en2 = a.put(f"/api/v1/copilot-admin/providers/{pid}/enabled",
                json={"expected_revision": 1, "enabled": True})
    out["steps"].append({"PUT enabled(自检后)": {"status": en2["status"],
                                                "body": en2.get("body")}})
    out["enable_succeeded"] = en2["status"] == 200
    return out


# ══════════════════════════════════════════════════════════════════
# ② 阈值复测（随包代码）
# ══════════════════════════════════════════════════════════════════
def threshold_check():
    from backend.services.copilot import knowledge as kn
    from backend.services.copilot.knowledge import store
    store.load()
    golden = [
        ("即时审核功能怎么使用？", ["即时审核"]), ("文件审核支持哪些文件格式？", ["文件审核"]),
        ("在线元数据审核是怎么发起的？", ["在线元数据审核"]),
        ("慢SQL记录和EXPLAIN分析在哪里？", ["慢SQL"]),
        ("扫描结果对比怎么比较两次快照？", ["扫描结果对比"]),
        ("表类型统计里主表和物理子表怎么看？", ["表类型统计"]),
        ("网关日志分析报告在哪里查看？", ["网关日志"]),
        ("Copilot 专家助手怎么打开？", ["Copilot"]),
        ("提示执行器未就绪 EXECUTOR_UNAVAILABLE 怎么处理？", ["执行器"]),
        ("任务状态未知 REQUIRED 是什么意思？", ["状态"]),
        ("资料过大 CONTEXT_TOO_LARGE 应该怎么办？", ["过大"]),
        ("网关摘要显示 PARTIAL 表示什么？", ["PARTIAL"]),
        ("TDSQL 分布式建表的分片键怎么写？", ["分片键"]),
        ("TDSQL 二级分区有什么要求？", ["分区"]),
        ("TDSQL 和原生 MySQL 有什么差异？", ["MySQL"]),
        ("项目审核规则和厂商语法能力怎么区分？", ["厂商"]),
        ("这个工具能做什么？", ["工具"]), ("我的数据会被发送到哪里？", ["数据"]),
    ]
    oos = ["Oracle 的 ROWNUM 分页在 TDSQL 里应该怎么写？", "如何做红烧肉？",
           "明天北京天气怎么样？", "Python 的 pandas 怎么读 CSV？",
           "请帮我写一首唐诗", "Kubernetes 的 Pod 怎么重启？"]
    hit, recall, zero = 0, [], []
    for q, must in golden:
        r = store.bundle.search(q, product_family="TDSQL-MySQL") or []
        text = " ".join(json.dumps(x, ensure_ascii=False) for x in r)
        ok = all(m in text for m in must)
        hit += 1 if ok else 0
        recall.append(len(r))
        if not r:
            zero.append(q)
    oos_nonzero = [q for q in oos
                   if store.bundle.search(q, product_family="TDSQL-MySQL")]
    return {"min_score": getattr(kn, "MIN_SCORE", None),
            "min_score_ratio": getattr(kn, "MIN_SCORE_RATIO", None),
            "min_score_floor": getattr(kn, "MIN_SCORE_FLOOR", None),
            "golden_hit": hit, "golden_total": len(golden),
            "golden_hit_rate": round(hit / len(golden), 3),
            "recall_avg": round(sum(recall) / len(recall), 2),
            "zero_recall_questions": zero,
            "oos_rejected": len(oos) - len(oos_nonzero), "oos_total": len(oos),
            "oos_leaked": oos_nonzero}


# ══════════════════════════════════════════════════════════════════
# ④ M01 防回退：出站 == 冻结投影
# ══════════════════════════════════════════════════════════════════
def projection_regression(a):
    subj = (a.get("/api/v1/copilot/capabilities").get("body") or {}).get("subject_id")
    out = []
    for scene, refs, q, draft in (
            ("RULE_EXPLAIN", [{"kind": "rule", "rule_ids": ["R001"]}],
             "请解释 R001 的要求", None),
            ("SQL_ADVISE", [], f"请给出 SELECT * FROM {REAL_TABLE} 的修改建议",
             f"SELECT id FROM {REAL_TABLE} WHERE id = 1")):
        s = a.post("/api/v1/copilot/sessions", json={
            "scope_kind": "INSTANCE", "connection_id": "d40-dist",
            "instance_type": "distributed", "page_key": "audit-sql"})
        sid = (s.get("body") or {}).get("session_id")
        mark = len(gateway_events())
        body = {"expected_session_revision": 1, "scene": scene,
                "page_key": "audit-sql", "question": q, "source_refs": refs}
        if draft:
            body["draft"] = {"kind": "SQL", "text": draft}
        p = a.post(f"/api/v1/copilot/sessions/{sid}/previews", json=body)
        pb = p.get("body") or {}
        row = {"scene": scene, "preview_status": p["status"]}
        if p["status"] != 201:
            row["error"] = str(pb.get("detail"))[:200]
            out.append(row)
            time.sleep(22)
            continue
        frozen = _decrypt_projection(pb["preview_id"], subj)
        sub = a.post(f"/api/v1/copilot/sessions/{sid}/turns", json={
            "client_request_id": uuid.uuid4().hex,
            "preview_id": pb["preview_id"], "snapshot_hash": pb["snapshot_hash"],
            "expected_session_revision": pb["expected_session_revision"],
            "confirm_data_use": True})
        tid = (sub.get("body") or {}).get("turn_id")
        term = wait_turn(a, tid, timeout=120) if tid else {}
        evs = [_outbound(e) for e in gateway_events()[mark:]]
        row.update({
            "terminal": (term.get("body") or {}).get("state"),
            "outbound_count": len(evs),
            "matches_frozen": [e["user"] == frozen["projection"] for e in evs],
            "frozen_evidence": len(json.loads(frozen["projection"]).get("evidence") or []),
            "frozen_knowledge": len(json.loads(frozen["projection"]).get("knowledge") or [])})
        r = a.get(f"/api/v1/copilot/turns/{tid}/result") if tid else {}
        row["answer_source"] = (r.get("body") or {}).get("answer_source")
        out.append(row)
        time.sleep(22)
    return out


def main():
    out = {"utc": utc()}
    a = Api("uat_d_1640")
    a.relogin_after(2)
    print("=== ① 自检端到端 ===")
    out["selftest"] = selftest_e2e(a)
    for k, v in out["selftest"].items():
        if k != "steps":
            print(f"  {k}: {json.dumps(v, ensure_ascii=False, default=str)[:200]}")
    for s in out["selftest"]["steps"]:
        for k, v in s.items():
            print(f"    {k}: {json.dumps(v, ensure_ascii=False, default=str)[:180]}")

    print("=== ② 阈值复测 ===")
    out["threshold"] = threshold_check()
    print(json.dumps(out["threshold"], ensure_ascii=False, indent=1)[:900])

    print("=== ④ 出站投影防回退 ===")
    out["projection"] = projection_regression(a)
    for r in out["projection"]:
        print(f"  {json.dumps(r, ensure_ascii=False, default=str)[:280]}")

    save("r4_core", out)
    a.close()


if __name__ == "__main__":
    main()
