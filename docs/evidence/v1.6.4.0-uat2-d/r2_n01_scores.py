"""N-01 证据：MIN_SCORE=0.5 是否真的过滤掉越界主题（打印实际分数分布）。"""
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

QS = [
    ("越界-Oracle", "Oracle 的 ROWNUM 分页在 TDSQL 里应该怎么写？"),
    ("越界-菜谱", "如何做红烧肉？"),
    ("越界-股票", "今天上证指数收盘多少点？"),
    ("在域-元数据", "在线元数据审核任务失败时我应该先看什么证据？"),
    ("在域-分片键", "TDSQL 分布式建表的分片键怎么写？"),
]


def main():
    store.load()
    out = {"min_score": getattr(kn, "MIN_SCORE", None), "rows": []}
    for tag, q in QS:
        r = store.bundle.search(q, product_family="TDSQL-MySQL")
        out["rows"].append({
            "tag": tag, "question": q, "count": len(r),
            "scores": [round(x.get("score", 0), 3) for x in r],
            "titles": [x.get("title") for x in r][:3]})
        print(f"{tag:10s} n={len(r)} scores={out['rows'][-1]['scores'][:6]}")
    (HERE / "r2_n01_scores.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
