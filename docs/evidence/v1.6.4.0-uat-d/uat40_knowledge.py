"""智能体D / v1.6.4.0 UAT：GATE §5 第 3 条 —— 知识包检索质量黄金集（修正版）。

双轨判定：
  轨道1（HTTP）：预览接口返回的 model_projection_preview.knowledge_count
                 —— 证明"服务端实际选中的知识条数"。
  轨道2（同源函数）：直接调用后端 knowledge.store.search(query)
                 —— 取回被选中条目的正文，逐题核对必需事实是否被检索到。
两轨用同一检索实现，轨道1 用于对账，轨道2 用于判分。
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from uat_d40_api import Api, save, utc  # noqa: E402

GOLDEN = [
    ("USER_GUIDE", "即时审核功能怎么使用？", ["即时审核"]),
    ("USER_GUIDE", "文件审核支持哪些文件格式？", ["文件审核"]),
    ("USER_GUIDE", "在线元数据审核是怎么发起的？", ["在线元数据审核"]),
    ("USER_GUIDE", "慢SQL记录和EXPLAIN分析在哪里？", ["慢SQL"]),
    ("USER_GUIDE", "扫描结果对比怎么比较两次快照？", ["扫描结果对比"]),
    ("USER_GUIDE", "表类型统计里主表和物理子表怎么看？", ["表类型统计"]),
    ("USER_GUIDE", "网关日志分析报告在哪里查看？", ["网关日志"]),
    ("USER_GUIDE", "Copilot 专家助手怎么打开？", ["Copilot"]),
    ("VERIFIED_CASE", "提示执行器未就绪 EXECUTOR_UNAVAILABLE 怎么处理？",
     ["执行器"]),
    ("VERIFIED_CASE", "任务状态未知 REQUIRED 是什么意思？", ["状态"]),
    ("VERIFIED_CASE", "资料过大 CONTEXT_TOO_LARGE 应该怎么办？", ["过大"]),
    ("VERIFIED_CASE", "网关摘要显示 PARTIAL 表示什么？", ["PARTIAL"]),
    ("VENDOR_SYNTAX", "TDSQL 分布式建表的分片键怎么写？", ["分片键"]),
    ("VENDOR_SYNTAX", "TDSQL 二级分区有什么要求？", ["分区"]),
    ("VENDOR_SYNTAX", "TDSQL 和原生 MySQL 有什么差异？", ["MySQL"]),
    ("VENDOR_SYNTAX", "项目审核规则和厂商语法能力怎么区分？", ["厂商"]),
    ("PUBLIC", "这个工具能做什么？", ["工具"]),
    ("PUBLIC", "我的数据会被发送到哪里？", ["数据"]),
]
OUT_OF_SCOPE = "Oracle 的 ROWNUM 分页在 TDSQL 里应该怎么写？"


def _http_count(a, question, scene="USAGE_HELP"):
    s = a.post("/api/v1/copilot/sessions", json={
        "scope_kind": "GLOBAL_HELP", "instance_type": "unknown",
        "page_key": "copilot-page"})
    sid = (s.get("body") or {}).get("session_id")
    p = a.post(f"/api/v1/copilot/sessions/{sid}/previews", json={
        "expected_session_revision": 1, "scene": scene,
        "page_key": "copilot-page", "question": question, "source_refs": []})
    b = p.get("body") or {}
    if p["status"] != 201:
        return {"status": p["status"], "err": b.get("detail")}
    mp = b.get("model_projection_preview") or {}
    return {"status": 201, "knowledge_count": mp.get("knowledge_count"),
            "projection_mode": mp.get("projection_mode")}


def main():
    import os
    os.environ.update({
        "SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
        "SQLCHECK_DB_USER": "root", "SQLCHECK_DB_PASSWORD": "tdsql_test_2024",
        "SQLCHECK_DB_NAME": "uat_d_1640_meta", "AUTH_ENABLED": "true",
        "COPILOT_KEYRING_FILE": str(ROOT / "data/reports/uat_d_1640/copilot-keyring.json"),
        "COPILOT_ENDPOINTS_FILE": str(ROOT / "data/reports/uat_d_1640/copilot-endpoints.json"),
    })
    from backend.services.copilot.knowledge import store
    st = store.load()
    print(f"[knowledge] load() -> {st}")
    info = store.status_info()
    print(f"[knowledge] bundle={info.get('bundle_id')}")

    a = Api("uat_d_1640")
    a.relogin_after(2)

    rows, hit, counts = [], 0, []
    for i, (auth, q, must) in enumerate(GOLDEN):
        if i % 4 == 0:            # 每 4 题走一次 HTTP 对账，避开 10 次/分钟限速
            h = _http_count(a, q)
            counts.append({"q": q, **h})
        else:
            h = {"status": "skipped"}
        kn = (store.bundle.search(q) if store.bundle else None) or []
        text = " ".join(json.dumps(k, ensure_ascii=False) for k in kn)
        ok = all(m in text for m in must)
        hit += 1 if ok else 0
        rows.append({"authority": auth, "question": q, "required": must,
                     "retrieved": len(kn), "hit": ok,
                     "titles": [k.get("title") for k in kn][:4],
                     "http": h})
        print(f"[{'HIT ' if ok else 'MISS'}] {auth:14s} {q[:32]:34s} "
              f"检索={len(kn)} http_count={h.get('knowledge_count')} "
              f"标题={rows[-1]['titles'][:2]}")

    kn_oos = (store.bundle.search(OUT_OF_SCOPE) if store.bundle else None) or []
    print(f"[{'OK  ' if not kn_oos else 'WARN'}] OUT_OF_SCOPE 检索={len(kn_oos)}"
          f" 标题={[k.get('title') for k in kn_oos][:3]}")

    total = len(GOLDEN)
    out = {"utc": utc(), "total": total, "hit": hit,
           "retrieval_hit_rate": round(hit / total, 4),
           "http_reconciliation": counts,
           "out_of_scope_question": OUT_OF_SCOPE,
           "out_of_scope_retrieved": len(kn_oos),
           "out_of_scope_unknown_ok": len(kn_oos) == 0,
           "rows": rows, "bundle": info}
    save("s6_knowledge_quality", out)
    print(f"\n检索命中率 = {hit}/{total} = {out['retrieval_hit_rate']:.1%}")
    http_ok = [c for c in counts if c.get("status") == 201]
    print("HTTP 对账 knowledge_count:", [c.get("knowledge_count") for c in http_ok])
    a.close()


if __name__ == "__main__":
    main()
