"""为 MIN_SCORE 标定取判别特征：top1 分数、top1-top2 间距、各阈值下的保留数。

目标：找一条能把"越界"与"在域（含弱匹配）"分开的规则，而不是单一绝对阈值。
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

IN_DOMAIN = [
    "即时审核功能怎么使用？", "文件审核支持哪些文件格式？",
    "在线元数据审核是怎么发起的？", "慢SQL记录和EXPLAIN分析在哪里？",
    "扫描结果对比怎么比较两次快照？", "表类型统计里主表和物理子表怎么看？",
    "网关日志分析报告在哪里查看？", "Copilot 专家助手怎么打开？",
    "提示执行器未就绪 EXECUTOR_UNAVAILABLE 怎么处理？",
    "任务状态未知 REQUIRED 是什么意思？",
    "资料过大 CONTEXT_TOO_LARGE 应该怎么办？",
    "网关摘要显示 PARTIAL 表示什么？",
    "TDSQL 分布式建表的分片键怎么写？", "TDSQL 二级分区有什么要求？",
    "TDSQL 和原生 MySQL 有什么差异？", "项目审核规则和厂商语法能力怎么区分？",
    "这个工具能做什么？", "我的数据会被发送到哪里？",
]
OUT_OF_SCOPE = [
    "Oracle 的 ROWNUM 分页在 TDSQL 里应该怎么写？",
    "如何做红烧肉？", "明天北京天气怎么样？",
    "Python 的 pandas 怎么读 CSV？", "请帮我写一首唐诗",
]


def feats(q):
    kn.MIN_SCORE = 0.0                      # 取全量，自行分析
    r = store.bundle.search(q, product_family="TDSQL-MySQL") or []
    s = [round(x["score"], 3) for x in r]
    return {"q": q, "n": len(s), "top1": s[0] if s else 0.0,
            "top2": s[1] if len(s) > 1 else 0.0,
            "gap": round((s[0] - s[1]), 3) if len(s) > 1 else (s[0] if s else 0.0),
            "n_ge5": sum(1 for x in s if x >= 5),
            "n_ge10": sum(1 for x in s if x >= 10),
            "scores": s[:6]}


def main():
    store.load()
    shipped = getattr(kn, "MIN_SCORE", None)
    out = {"shipped_min_score": shipped, "in_domain": [], "out_of_scope": []}
    for q in IN_DOMAIN:
        out["in_domain"].append(feats(q))
    for q in OUT_OF_SCOPE:
        out["out_of_scope"].append(feats(q))
    kn.MIN_SCORE = shipped

    print("=== 在域（18 题）===")
    for r in sorted(out["in_domain"], key=lambda x: x["top1"]):
        print(f"  top1={r['top1']:7.2f} top2={r['top2']:7.2f} gap={r['gap']:7.2f} "
              f"n={r['n']:2d}  {r['q'][:34]}")
    print("=== 越界（5 题）===")
    for r in sorted(out["out_of_scope"], key=lambda x: -x["top1"]):
        print(f"  top1={r['top1']:7.2f} top2={r['top2']:7.2f} gap={r['gap']:7.2f} "
              f"n={r['n']:2d}  {r['q'][:34]}")

    in_top1 = sorted(x["top1"] for x in out["in_domain"])
    oos_top1 = sorted((x["top1"] for x in out["out_of_scope"]), reverse=True)
    out["summary"] = {
        "in_domain_top1_min": in_top1[0], "in_domain_top1_max": in_top1[-1],
        "oos_top1_max": oos_top1[0],
        "separable_by_absolute_top1": in_top1[0] > oos_top1[0],
    }
    (HERE / "r3_n01_features.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n=== 可分性 ===")
    print(json.dumps(out["summary"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
