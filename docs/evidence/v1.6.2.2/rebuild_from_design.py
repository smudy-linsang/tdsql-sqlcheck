# -*- coding: utf-8 -*-
"""从设计说明书的代码块重建 `parser_legacy.py`（第十二轮 BLOCK-12-05 / MAJOR-12-02）。

**只写入指定的输出路径，绝不改动工作区。** 用法：

    python docs/evidence/v1.6.2.2/rebuild_from_design.py <输出文件> [设计文档路径]

前 10 个 ```python 代码块的约定（顺序即契约）：
    0 §1.1 示意（不参与施工，只做锚点校验）
    1 改动点 0   新增 import
    2 改动点 0c  全部模块级恢复链代码
    3 说明性占位（不参与施工）
    4/5 改动点 2b  首次 Command 重试：改动前 / 改动后
    6/7 改动点 2   except 分支：改动前 / 改动后
    8/9 改动点 3   索引类型判据：改动前 / 改动后
    10/11 改动点 3b 恢复链取用未删分号的原串：改动前 / 改动后
第 12 个及以后的代码块是证据面资产源码（附录 C），不参与重建。
"""
import hashlib
import io
import os
import re
import sys

DEFAULT_DESIGN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md")


def python_blocks(design_path):
    lines = io.open(design_path, encoding="utf-8").read().split("\n")
    blocks, i = [], 0
    while i < len(lines):
        if lines[i].strip().startswith("```python"):
            j, body = i + 1, []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                body.append(lines[j])
                j += 1
            blocks.append("\n".join(body))
            i = j + 1
            continue
        i += 1
    return blocks


def rebuild(baseline_text, blocks):
    src = baseline_text
    illus = "\n".join(re.sub(r"\s+# ←.*$", "", l) for l in blocks[0].split("\n"))
    assert illus in src, "§1.1 示意块与主干不匹配"
    for name, idx in (("2b", 4), ("except", 6), ("idxtype", 8), ("semicolon", 10)):
        assert blocks[idx] in src, "「改动前」块与主干不匹配: " + name
    anchor = "from sqlglot.errors import SqlglotError"
    src = src.replace(anchor, anchor + "\n" + blocks[1].strip(), 1)
    m = re.search(r"\n_TDSQL_DIALECT_RE\s*=\s*re\.compile\((?:.|\n)*?\n\)\n", src)
    assert m, "主干中找不到 _TDSQL_DIALECT_RE"
    head = src[:m.start()].rstrip("\n").split("\n")
    k = len(head)
    while k > 0 and head[k - 1].lstrip().startswith("#"):
        k -= 1
    cut = len("\n".join(head[:k])) + (1 if k else 0)
    src = src[:cut] + blocks[2].rstrip("\n") + "\n\n\n" + src[m.end():].lstrip("\n")
    for before, after in ((4, 5), (6, 7), (8, 9), (10, 11)):
        assert src.count(blocks[before]) == 1, "「改动前」块出现次数不为 1: %d" % before
        src = src.replace(blocks[before], blocks[after], 1)
    return src


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out_path = sys.argv[1]
    design = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DESIGN
    baseline = io.open(out_path, encoding="utf-8").read()
    blocks = python_blocks(design)
    print("设计文档 python 代码块 %d 个（前 12 个参与重建）" % len(blocks))
    src = rebuild(baseline, blocks)
    io.open(out_path, "w", encoding="utf-8").write(src)
    digest = hashlib.sha256(src.encode("utf-8")).hexdigest()
    print("已重建 -> %s" % out_path)
    print("SHA256 = %s" % digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
