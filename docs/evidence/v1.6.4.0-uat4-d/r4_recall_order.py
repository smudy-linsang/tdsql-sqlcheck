"""第四轮补测：相对保留的生效方式（串联 vs 并联）对召回的影响。

随包实现：先按 MIN_SCORE=9.0 过滤，再按 max(FLOOR, top1*RATIO) 过滤（串联）。
处方设计：绝对下限只用于"是否收录"，保留条数由相对比例决定（并联）。
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


def main():
    store.load()
    shipped = kn.MIN_SCORE
    out = {"shipped": {"MIN_SCORE": shipped,
                       "MIN_SCORE_RATIO": kn.MIN_SCORE_RATIO,
                       "MIN_SCORE_FLOOR": kn.MIN_SCORE_FLOOR}}
    # 串联（随包）
    serial = [len(store.bundle.search(q, product_family="TDSQL-MySQL") or [])
              for q in GOLDEN]
    # 并联（处方）：先用绝对下限判"是否收录"，再用相对比例决定条数
    kn.MIN_SCORE = 0.0
    parallel = []
    for q in GOLDEN:
        r = store.bundle.search(q, product_family="TDSQL-MySQL") or []
        if not r or r[0]["score"] < 9.0:
            parallel.append(0)
            continue
        floor = max(kn.MIN_SCORE_FLOOR, r[0]["score"] * kn.MIN_SCORE_RATIO)
        parallel.append(sum(1 for x in r if x["score"] >= floor))
    kn.MIN_SCORE = shipped
    out["serial_recall_avg"] = round(sum(serial) / len(serial), 2)
    out["parallel_recall_avg"] = round(sum(parallel) / len(parallel), 2)
    out["serial_zero"] = sum(1 for n in serial if n == 0)
    out["parallel_zero"] = sum(1 for n in parallel if n == 0)
    out["per_question"] = [{"q": q, "serial": s, "parallel": p}
                           for q, s, p in zip(GOLDEN, serial, parallel)]
    (HERE / "r4_recall_order.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "per_question"},
                     ensure_ascii=False, indent=1))
    print("差异最大的题：")
    for r in sorted(out["per_question"], key=lambda x: x["parallel"] - x["serial"],
                    reverse=True)[:5]:
        print(f"  serial={r['serial']} parallel={r['parallel']}  {r['q'][:30]}")


if __name__ == "__main__":
    main()
