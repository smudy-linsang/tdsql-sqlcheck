"""验证标定方案：绝对下限 9.0 + 相对保留，能否同时满足"在域召回"和"越界拒绝未知"。

变体：
  A 仅绝对阈值 10.0（当前随包）
  B 仅绝对阈值 9.0
  C 绝对下限 9.0 + 相对保留（保留 score >= max(4.0, top1*0.30)）
  D 绝对下限 9.0 + gap 判别（top1-top2 >= 1.5 才认为命中）
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parents[1] / "evidence/v1.6.4.0-uat-d"
ROOT = HERE.parents[2]
sys.path.insert(0, str(R1))
sys.path.insert(0, str(ROOT))
import _boot  # noqa: F401,E402

from backend.services.copilot import knowledge as kn  # noqa: E402
from backend.services.copilot.knowledge import store  # noqa: E402

GOLDEN = [
    ("即时审核功能怎么使用？", ["即时审核"]),
    ("文件审核支持哪些文件格式？", ["文件审核"]),
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
    ("这个工具能做什么？", ["工具"]),
    ("我的数据会被发送到哪里？", ["数据"]),
]
OOS = ["Oracle 的 ROWNUM 分页在 TDSQL 里应该怎么写？", "如何做红烧肉？",
       "明天北京天气怎么样？", "Python 的 pandas 怎么读 CSV？",
       "请帮我写一首唐诗", "Kubernetes 的 Pod 怎么重启？"]


def raw(q):
    kn.MIN_SCORE = 0.0
    return store.bundle.search(q, product_family="TDSQL-MySQL") or []


def keep(scores, variant):
    if not scores:
        return []
    top1 = scores[0]
    if variant == "A":
        return [s for s in scores if s >= 10.0]
    if variant == "B":
        return [s for s in scores if s >= 9.0]
    if variant == "C":
        if top1 < 9.0:
            return []
        floor = max(4.0, top1 * 0.30)
        return [s for s in scores if s >= floor]
    if variant == "D":
        if top1 < 9.0:
            return []
        gap = top1 - (scores[1] if len(scores) > 1 else 0.0)
        return [s for s in scores if s >= 4.0] if gap >= 1.5 else []
    return scores


def evaluate(variant):
    hit, recall, zero = 0, [], 0
    for q, must in GOLDEN:
        r = raw(q)
        scores = [x["score"] for x in r]
        kept = keep(scores, variant)
        text = " ".join(json.dumps(x, ensure_ascii=False)
                        for x, s in zip(r, scores) if s in kept)
        ok = all(m in text for m in must)
        hit += 1 if ok else 0
        recall.append(len(kept))
        if not kept:
            zero += 1
    oos_nonzero = 0
    oos_detail = []
    for q in OOS:
        r = raw(q)
        kept = keep([x["score"] for x in r], variant)
        oos_nonzero += 1 if kept else 0
        oos_detail.append({"q": q, "kept": len(kept)})
    return {"variant": variant, "golden_hit": hit, "golden_total": len(GOLDEN),
            "golden_hit_rate": round(hit / len(GOLDEN), 3),
            "recall_avg": round(sum(recall) / len(recall), 2),
            "zero_recall_questions": zero,
            "oos_nonzero": oos_nonzero, "oos_total": len(OOS),
            "oos_detail": oos_detail}


def main():
    store.load()
    shipped = getattr(kn, "MIN_SCORE", None)
    out = {"shipped_min_score": shipped, "variants": []}
    for v in ("A", "B", "C", "D"):
        res = evaluate(v)
        out["variants"].append(res)
        print(f"[{v}] 黄金集 {res['golden_hit']}/{res['golden_total']}"
              f" ({res['golden_hit_rate']:.0%}) | 平均召回 {res['recall_avg']}"
              f" | 零召回 {res['zero_recall_questions']}"
              f" | 越界误答 {res['oos_nonzero']}/{res['oos_total']}")
    kn.MIN_SCORE = shipped
    (HERE / "r3_n01_variants.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
