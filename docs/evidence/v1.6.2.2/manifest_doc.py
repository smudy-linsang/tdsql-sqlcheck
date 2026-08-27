# -*- coding: utf-8 -*-
"""从 manifest 生成设计说明书的用例表与计数（第十一轮 BLOCK-11-07 第 2 条）。

任何章节都不得人工维护第二份计数；本脚本的输出即正文。
"""
import sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parser_recovery_manifest import CASES, MUTATIONS, FUZZ

KLASS_ORDER = ["pos", "neg", "pos_known", "unsupported_unproven",
               "characterization", "ruleset", "spans", "contract"]
GROUP_TITLE = {
    "A": "DEF-1 索引类型判据 + AST 契约", "B": "DEF-2 正向恢复",
    "C": "DEF-2 产品边界（sqlglot 能力边界）", "D": "负向 / 防次生灾害",
    "E": "失败关闭", "F": "生产回放（精确规则集合）", "T": "TDSQL 方言组合",
    "N": "作用域负向", "X": "方言尾子句安全交叉矩阵", "Y": "方言语法严格性与语句边界",
    "Z": "方法参数与表名精确形态", "W": "目标上下文完整性",
    "H1": "key_part 非法", "H2": "key_part 官方合法", "H2b": "key_part 含 ASC/DESC",
    "H3": "分区子句非法", "H4": "官方二级分区 Range/List", "H4c": "官方合法但 sqlglot 不支持",
    "H4b": "官方未列的分区方法", "H5": "表选项值非法", "H6": "表选项官方合法",
    "H6b": "表选项无证据", "P1": "PRIMARY COMMENT 官方合法", "P2": "PRIMARY COMMENT 非法近邻",
    "R11-01": "可执行注释（BLOCK-11-01）", "R11-02": "表尾迁移图（BLOCK-11-02）",
    "R11-03": "广播哨兵分型（BLOCK-11-03）", "R11-06": "列属性（BLOCK-11-06）",
    "R11-M1": "FULLTEXT/SPATIAL 入口（MAJOR-11-01）",
    "TY-P": "官方类型：必须恢复", "TY-D": "官方类型：DEFAULT/ON UPDATE 精度",
    "TY-K": "官方类型：sqlglot 不支持（KFN-3）", "TY-N": "类型越界/非法：必须失败关闭",
    "R12-EC": "可执行注释位置 × 主表尾 atom（BLOCK-12-01）",
    "R12-SC": "语句终止符集成路径（BLOCK-12-02）",
    "R12-SC-K": "终止符后普通注释（KFN-4）",
    "R12-TY": "官方类型产生式矩阵（BLOCK-12-03）",
    "R12-TY-K": "官方类型：sqlglot 不支持（KFN-4）",
    "R12-CN": "具名 PRIMARY 约束（MAJOR-12-01）",
    "R12-CS": "字符集拼写的跨版本词法差异（Rev.N 自查）",
}


def compose(gs):
    c = collections.Counter(x.klass for x in CASES if x.group in gs)
    return "×".join([]) or "  ".join("%s×%d" % (k, c[k]) for k in KLASS_ORDER if c[k])


def table(groups, title):
    out = ["| 子组 | 例数 | 说明 | 分类构成 |", "|---|---:|---|---|"]
    tot = 0
    for g in groups:
        n = sum(1 for x in CASES if x.group == g)
        tot += n
        out.append("| **%s** | %d | %s | %s |" % (g, n, GROUP_TITLE.get(g, ""), compose([g])))
    out.append("| **合计** | **%d** | —— | %s |" % (tot, compose(groups)))
    return "**%s**\n\n" % title + "\n".join(out)


def main():
    order = [x.group for x in CASES]
    seen, groups = set(), []
    for g in order:
        if g not in seen:
            seen.add(g); groups.append(g)
    main_g = [g for g in groups if not (g.startswith("H") or g.startswith("P")
                                        or g.startswith("R11") or g.startswith("R12")
                                        or g.startswith("TY"))]
    print("<!-- 本节由 docs/evidence/v1.6.2.2/manifest_doc.py 生成，请勿手改 -->\n")
    print(table(main_g, "§7.1 主用例表"), "\n")
    print(table([g for g in groups if g.startswith("H")], "§7.1a H 组"), "\n")
    print(table([g for g in groups if g.startswith("P")], "§7.1b P 组（DEF-3）"), "\n")
    print(table([g for g in groups if g.startswith("R11")], "§7.1c R11 组（第十一轮复审反例）"), "\n")
    print(table([g for g in groups if g.startswith("TY")], "§7.1d TY 组（官方数据类型双向闭合矩阵）"), "\n")
    print(table([g for g in groups if g.startswith("R12")], "§7.1e R12 组（第十二轮复审反例，按维度生成）"), "\n")
    kc = collections.Counter(x.klass for x in CASES)
    pc = collections.Counter(x.prov for x in CASES)
    print("**全局计数（唯一真源）**\n")
    print("| 项 | 值 |")
    print("|---|---:|")
    print("| manifest 用例总数 | **%d** |" % len(CASES))
    for k in KLASS_ORDER:
        if kc[k]:
            print("| 其中 `%s` | %d |" % (k, kc[k]))
    n_suite = len(MUTATIONS)
    n_assert = sum(1 + len(s["muts"]) for s in MUTATIONS)
    print("| 变异门禁：套数 | **%d** |" % n_suite)
    print("| 变异门禁：逐条断言数（每套 = 1 个正确候选 + N 个变异候选） | **%d** |" % n_assert)
    print("| 模糊测试（seed=%d，整体计 1 个 pytest item） | **%d** 条输入 |" % (
        FUZZ["seed"], FUZZ["n"]))
    print("| **`pytest --collect-only -q` 应收集** | **%d** = 用例 %d + 变异套 %d + 模糊 1 |" % (
        len(CASES) + n_suite + 1, len(CASES), n_suite))
    print()
    print("> **三个口径不要混用**（第十二轮 MINOR-12-01）：`用例数` 是逐条 SQL；")
    print("> `逐条断言数` 是变异测试内部的 `assert` 次数；`collect 数` 是 pytest item 数——")
    print("> 一套变异是 **1 个** item 但含多条断言，模糊测试是 **1 个** item 但跑 6000 条输入。")
    print()
    print("**证据来源分布**\n")
    print("| provenance | 例数 |")
    print("|---|---:|")
    for p, n in sorted(pc.items(), key=lambda kv: -kv[1]):
        print("| `%s` | %d |" % (p, n))
    print()
    kfn = [x for x in CASES if x.klass in ("pos_known", "unsupported_unproven")]
    print("**已知假阴性 / 未证实能力登记（由 manifest 生成）**\n")
    print("| 类别 | cid | 形态 | 理由 |")
    print("|---|---|---|---|")
    for x in kfn:
        print("| %s | %s | `%s` | %s |" % (
            "KFN-A（官方合法、暂不支持）" if x.klass == "pos_known" else "KFN-B（未证实能力）",
            x.cid, x.label.replace("|", "\\|"), x.note))


if __name__ == "__main__":
    main()
