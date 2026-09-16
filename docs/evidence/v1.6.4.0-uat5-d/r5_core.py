"""智能体D / v1.6.4.0 第五轮 UAT：F-3 召回 / M01 防回退 / F-2 失败归因。"""
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

from uat_d40_api import (Api, gateway_events, save, set_gw_mode, utc,  # noqa: E402
                         wait_turn)
from uat2_d40_core import _decrypt_projection, _outbound  # noqa: E402

CAPS = {"context_tokens": 32768, "max_output_field": "max_tokens",
        "supports_temperature": True, "supports_json_schema": False,
        "supports_json_object": True, "supports_store_false": True}
REAL_TABLE = "d40_tab_00"


def f3_recall():
    from backend.services.copilot import knowledge as kn
    from backend.services.copilot.knowledge import store
    store.load()
    golden = ["即时审核功能怎么使用？", "文件审核支持哪些文件格式？",
              "在线元数据审核是怎么发起的？", "慢SQL记录和EXPLAIN分析在哪里？",
              "扫描结果对比怎么比较两次快照？", "表类型统计里主表和物理子表怎么看？",
              "网关日志分析报告在哪里查看？", "Copilot 专家助手怎么打开？",
              "提示执行器未就绪 EXECUTOR_UNAVAILABLE 怎么处理？",
              "任务状态未知 REQUIRED 是什么意思？",
              "资料过大 CONTEXT_TOO_LARGE 应该怎么办？",
              "网关摘要显示 PARTIAL 表示什么？",
              "TDSQL 分布式建表的分片键怎么写？", "TDSQL 二级分区有什么要求？",
              "TDSQL 和原生 MySQL 有什么差异？", "项目审核规则和厂商语法能力怎么区分？",
              "这个工具能做什么？", "我的数据会被发送到哪里？"]
    oos = ["Oracle 的 ROWNUM 分页在 TDSQL 里应该怎么写？", "如何做红烧肉？",
           "明天北京天气怎么样？", "Python 的 pandas 怎么读 CSV？",
           "请帮我写一首唐诗", "Kubernetes 的 Pod 怎么重启？"]
    recall = [len(store.bundle.search(q, product_family="TDSQL-MySQL") or [])
              for q in golden]
    oos_hit = [q for q in oos if store.bundle.search(q,
                                                     product_family="TDSQL-MySQL")]
    return {"min_score": kn.MIN_SCORE, "ratio": kn.MIN_SCORE_RATIO,
            "floor": kn.MIN_SCORE_FLOOR,
            "recall_avg": round(sum(recall) / len(recall), 2),
            "zero_recall": sum(1 for n in recall if n == 0),
            "oos_rejected": f"{len(oos) - len(oos_hit)}/{len(oos)}",
            "oos_leaked": oos_hit, "per_q": dict(zip(golden, recall))}


def projection_regression(a):
    subj = (a.get("/api/v1/copilot/capabilities").get("body") or {}).get("subject_id")
    rows = []
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
            row["error"] = str(pb.get("detail"))[:160]
            rows.append(row)
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
        row.update({"terminal": (term.get("body") or {}).get("state"),
                    "outbound_count": len(evs),
                    "matches_frozen": [e["user"] == frozen["projection"] for e in evs],
                    "frozen_evidence": len(json.loads(frozen["projection"]).get("evidence") or []),
                    "frozen_knowledge": len(json.loads(frozen["projection"]).get("knowledge") or [])})
        r = a.get(f"/api/v1/copilot/turns/{tid}/result") if tid else {}
        row["answer_source"] = (r.get("body") or {}).get("answer_source")
        rows.append(row)
        time.sleep(22)
    return rows


def f2_failure_attribution(a):
    """模型不可达时，自检轮应报模型侧原因，而不是'所选资料不可用'。"""
    name = f"UAT5-归因-{uuid.uuid4().hex[:6]}"
    r = a.post("/api/v1/copilot-admin/providers", json={
        "name": name, "endpoint_id": "ep-uat-a",
        "protocol": "OPENAI_COMPAT_CHAT", "model_id": "uat5-attr",
        "auth_mode": "BEARER", "capabilities": CAPS,
        "secret_action": "REPLACE", "secret": "sk-uat5"})
    pid = (r.get("body") or {}).get("id")
    out = {"provider_id": pid, "create_status": r["status"]}
    if not pid:
        return out
    set_gw_mode(8443, "unavail503")
    set_gw_mode(8444, "unavail503")
    time.sleep(0.5)
    try:
        st = a.post(f"/api/v1/copilot-admin/providers/{pid}/self-tests",
                    json={"client_request_id": uuid.uuid4().hex,
                          "expected_provider_revision": 1})
        out["self_test_status"] = st["status"]
        tid = (st.get("body") or {}).get("turn_id")
        term = wait_turn(a, tid, timeout=150) if tid else {}
        tb = term.get("body") or {}
        out["terminal"] = tb.get("state")
        out["error_code"] = tb.get("error_code")
        out["is_model_related"] = (tb.get("error_code") in
                                   ("PROVIDER_UNAVAILABLE", "PROVIDER_CONNECT_FAILED",
                                    "PROVIDER_TIMEOUT", "DEGRADED") or
                                   tb.get("state") == "DEGRADED")
        res = a.get(f"/api/v1/copilot/turns/{tid}/result") if tid else {}
        rb = res.get("body") or {}
        out["answer_source"] = rb.get("answer_source")
        out["model_block"] = rb.get("model")
        out["limitations"] = ((rb.get("answer") or {}).get("limitations") or [])[:2]
        prov = (a.get("/api/v1/copilot-admin/providers").get("body") or {}).get(
            "items", [])
        item = next((p for p in prov if p["id"] == pid), {})
        out["tested_revision"] = item.get("tested_revision")
    finally:
        set_gw_mode(8443, "ok")
        set_gw_mode(8444, "ok")
    return out


def main():
    out = {"utc": utc()}
    a = Api("uat_d_1640")
    a.relogin_after(2)
    print("=== F-3 召回（并联后）===")
    out["f3"] = f3_recall()
    print(json.dumps({k: v for k, v in out["f3"].items() if k != "per_q"},
                     ensure_ascii=False, indent=1))
    print("=== M01 防回退 ===")
    out["projection"] = projection_regression(a)
    for r in out["projection"]:
        print(f"  {json.dumps(r, ensure_ascii=False, default=str)[:260]}")
    print("=== F-2 失败归因（模型 503）===")
    out["f2"] = f2_failure_attribution(a)
    print(json.dumps(out["f2"], ensure_ascii=False, indent=1)[:900])
    save("r5_core", out)
    a.close()


if __name__ == "__main__":
    main()
