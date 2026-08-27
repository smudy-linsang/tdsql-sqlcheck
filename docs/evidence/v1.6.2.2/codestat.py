# -*- coding: utf-8 -*-
"""从最终补丁自动生成 diff stat、函数清单与唯一性检查（第十一轮 MINOR-11-02）。

用法：python docs/evidence/v1.6.2.2/codestat.py <基线文件> <目标文件>
正文的 §3.4 规模数字必须由本脚本输出，不得人工维护。
"""
import ast, io, sys, difflib, collections

REL = "backend/engine/parser/parser_legacy.py"


def top_defs(src):
    """模块级 def 名 -> (起始行, 行数)；同时收集模块级赋值名。"""
    tree = ast.parse(src)
    fns, consts = collections.OrderedDict(), collections.OrderedDict()
    dup_fn, dup_const = [], []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if n.name in fns:
                dup_fn.append(n.name)
            fns[n.name] = (n.lineno, (n.end_lineno or n.lineno) - n.lineno + 1)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    if t.id in consts:
                        dup_const.append(t.id)
                    consts[t.id] = n.lineno
        elif isinstance(n, ast.ClassDef):
            fns["class " + n.name] = (n.lineno, (n.end_lineno or n.lineno) - n.lineno + 1)
    return fns, consts, dup_fn, dup_const


def main():
    base_p, new_p = sys.argv[1], sys.argv[2]
    base = io.open(base_p, encoding="utf-8").read()
    new = io.open(new_p, encoding="utf-8").read()
    bl, nl = base.splitlines(), new.splitlines()
    add = dele = 0
    for line in difflib.unified_diff(bl, nl, n=0, lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            add += 1
        elif line.startswith("-") and not line.startswith("---"):
            dele += 1
    bf, bc, _, _ = top_defs(base)
    nf, nc, dupf, dupc = top_defs(new)

    print("<!-- 本节由 docs/evidence/v1.6.2.2/codestat.py 生成，请勿手改 -->\n")
    print("**`%s` 规模（自动生成）**\n" % REL)
    print("| 项 | 基线 | 目标 | 变化 |")
    print("|---|---:|---:|---:|")
    print("| 文件行数 | %d | %d | %+d |" % (len(bl), len(nl), len(nl) - len(bl)))
    print("| 模块级函数/类 | %d | %d | %+d |" % (len(bf), len(nf), len(nf) - len(bf)))
    print("| 模块级常量 | %d | %d | %+d |" % (len(bc), len(nc), len(nc) - len(bc)))
    print("| diff 行 | —— | —— | +%d / -%d |" % (add, dele))
    print()
    added = [k for k in nf if k not in bf]
    removed = [k for k in bf if k not in nf]
    changed = [k for k in nf if k in bf and nf[k][1] != bf[k][1]]
    print("**新增函数（%d 个）**\n" % len(added))
    print("| 函数 | 起始行 | 行数 |")
    print("|---|---:|---:|")
    for k in added:
        print("| `%s` | %d | %d |" % (k, nf[k][0], nf[k][1]))
    print()
    print("**删除函数（%d 个）**：%s\n" % (len(removed), ", ".join("`%s`" % k for k in removed) or "无"))
    print("**行数发生变化的既有函数（%d 个）**\n" % len(changed))
    if changed:
        print("| 函数 | 基线行数 | 目标行数 |")
        print("|---|---:|---:|")
        for k in changed:
            print("| `%s` | %d | %d |" % (k, bf[k][1], nf[k][1]))
    print()
    print("**唯一性检查**\n")
    print("| 检查 | 结果 |")
    print("|---|---|")
    print("| 模块级函数重复定义 | %s |" % ("❌ " + ", ".join(dupf) if dupf else "✅ 无"))
    print("| 模块级常量重复定义 | %s |" % ("❌ " + ", ".join(dupc) if dupc else "✅ 无"))
    print("| 语法可解析 | ✅ |")
    return 1 if (dupf or dupc) else 0


if __name__ == "__main__":
    sys.exit(main())
