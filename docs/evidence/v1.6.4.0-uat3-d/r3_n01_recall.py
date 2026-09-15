"""智能体D / v1.6.4.0 第三轮 UAT：MIN_SCORE=10.0 的知识召回影响量化。

对照（同进程、同检索函数，只改阈值常量）：
  阈值 10.0（当前随包值）  vs  阈值 0.5（第二轮值）  vs  阈值 0（无过滤）
判据：18 题黄金集的必需事实命中率；以及在域问题的平均召回条数。
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
    ("USER_GUIDE", "即时审核功能怎么使用？", ["即时审核"]),
    ("USER_GUIDE", "文件审核支持哪些文件格式？", ["文件审核"]),
    ("USER_GUIDE", "在线元数据审核是怎么发起的？", ["在线元数据审核"]),
    ("USER_GUIDE", "慢SQL记录和EXPLAIN分析在哪里？", ["慢SQL"]),
    ("USER_GUIDE", "扫描结果对比怎么比较两次快照？", ["扫描结果对比"]),
    ("USER_GUIDE", "表类型统计里主表和物理子表怎么看？", ["表类型统计"]),
    ("USER_GUIDE", "网关日志分析报告在哪里查看？", ["网关日志"]),
    ("USER_GUIDE", "Copilot 专家助手怎么打开？", ["Copilot"]),
    ("VERIFIED_CASE", "提示执行器未就绪 EXECUTOR_UNAVAILABLE 怎么处理？", ["执行器"]),
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
OOS = [("越界-Oracle", "Oracle 的 ROWNUM 分页在 TDSQL 里应该怎么写？"),
       ("越界-菜谱", "如何做红烧肉？"),
       ("越界-天气", "明天北京天气怎么样？")]


def measure(threshold):
    kn.MIN_SCORE = threshold
    hit, recall, rows = 0, [], []
    for auth, q, must in GOLDEN:
        r = store.bundle.search(q, product_family="TDSQL-MySQL") or []
        text = " ".join(json.dumps(x, ensure_ascii=False) for x in r)
        ok = all(m in text for m in must)
        hit += 1 if ok else 0
        recall.append(len(r))
        rows.append({"q": q, "n": len(r), "hit": ok,
                     "top": round(r[0]["score"], 2) if r else None})
    oos_rows = []
    for tag, q in OOS:
        r = store.bundle.search(q, product_family="TDSQL-MySQL") or []
        oos_rows.append({"tag": tag, "n": len(r),
                         "top": round(r[0]["score"], 2) if r else None})
    return {"threshold": threshold, "golden_hit": hit, "golden_total": len(GOLDEN),
            "golden_hit_rate": round(hit / len(GOLDEN), 3),
            "recall_avg": round(sum(recall) / len(recall), 2),
            "recall_zero_questions": sum(1 for n in recall if n == 0),
            "out_of_scope_nonzero": sum(1 for x in oos_rows if x["n"] > 0),
            "rows": rows, "oos": oos_rows}


def main():
    store.load()
    shipped = getattr(kn, "MIN_SCORE", None)
    out = {"shipped_min_score": shipped}
    for th in (shipped, 0.5, 0.0):
        out[f"threshold_{th}"] = measure(th)
        m = out[f"threshold_{th}"]
        print(f"阈值 {th:>5}: 黄金集命中 {m['golden_hit']}/{m['golden_total']} "
              f"({m['golden_hit_rate']:.0%}) | 平均召回 {m['recall_avg']} 条 | "
              f"零召回题数 {m['recall_zero_questions']} | "
              f"越界仍返 {m['out_of_scope_nonzero']}/{len(OOS)}")
    # 还原随包值
    kn.MIN_SCORE = shipped
    out["zero_recall_detail"] = [
        r["q"] for r in out[f"threshold_{shipped}"]["rows"] if r["n"] == 0]
    (HERE / "r3_n01_recall.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n随包阈值下零召回的题：")
    for q in out["zero_recall_detail"]:
        print("   -", q)


if __name__ == "__main__":
    main()
