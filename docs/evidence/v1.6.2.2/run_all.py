# -*- coding: utf-8 -*-
"""v1.6.2.2 证据面一键复现（第十二轮 BLOCK-12-05）。

    python docs/evidence/v1.6.2.2/run_all.py [--keep]

在**临时目录**里完成全部工作，绝不触碰工作区，也不需要 `git stash`：

  1. 把仓库拷进临时目录，按设计说明书的代码块重建 `parser_legacy.py`；
  2. 校验重建结果的 SHA256 与设计说明书登记的目标哈希一致；
  3. 在临时树上跑 manifest 全量（用例 + 变异断言 + 模糊）；
  4. 跑 `manifest_doc.py` / `codestat.py`，把输出与设计说明书正文逐字比对；
  5. 打印汇总。任一步失败即以非零码退出。

`--keep` 保留临时目录以便排查。
"""
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(DOCS)
DESIGN = os.path.join(DOCS, "DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md")
REL_PARSER = os.path.join("backend", "engine", "parser", "parser_legacy.py")
HASH_MARK = "重建目标 SHA256"


def _design_text():
    return io.open(DESIGN, encoding="utf-8").read()


def expected_hash(doc):
    m = re.search(HASH_MARK + r"[^`]*`([0-9a-f]{64})`", doc)
    return m.group(1) if m else None


def main():
    keep = "--keep" in sys.argv
    doc = _design_text()
    tmp = tempfile.mkdtemp(prefix="v1622-evidence-")
    failures = []
    try:
        work = os.path.join(tmp, "tree")
        shutil.copytree(REPO, work, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "node_modules", "*.pyc"))
        target = os.path.join(work, REL_PARSER)

        print("═══ 1/4 从设计说明书重建产品代码 ═══")
        r = subprocess.run([sys.executable, os.path.join(HERE, "rebuild_from_design.py"),
                            target, DESIGN], capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        sys.stdout.write(r.stderr)
        if r.returncode != 0:
            failures.append("重建失败")
        got = hashlib.sha256(io.open(target, encoding="utf-8").read().encode("utf-8")).hexdigest()
        want = expected_hash(doc)
        if want is None:
            failures.append("设计说明书里找不到登记的目标哈希")
        elif got != want:
            failures.append("重建结果哈希不符：设计登记 %s，实得 %s" % (want, got))
        else:
            print("  SHA256 与设计说明书登记值一致 ✅")

        print("\n═══ 2/4 在重建树上跑 manifest 全量 ═══")
        # ⚠️ 必须跑**临时树里那一份**测试文件：仓库 `pyproject.toml` 声明了
        # `pythonpath = ["."]`，pytest 会把 rootdir 插到 sys.path 最前面。
        # 若跑仓库路径下的测试文件，rootdir 就是仓库，`backend` 会解析到**未打补丁**的主干，
        # 断言全部失败却与设计无关。
        rel_here = os.path.relpath(HERE, REPO)
        test_in_tree = os.path.join(work, rel_here, "test_parser_recovery_manifest.py")
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", test_in_tree],
                           cwd=work, capture_output=True, text=True)
        tail = [l for l in (r.stdout or "").strip().split("\n") if l.strip()][-1:]
        print("  " + ("\n  ".join(tail) if tail else "(无输出)"))
        if r.returncode != 0:
            failures.append("manifest 未全绿")
            sys.stdout.write(r.stdout[-4000:])

        print("\n═══ 3/4 生成器输出与设计说明书正文逐字比对 ═══")
        gen = subprocess.run([sys.executable, os.path.join(HERE, "manifest_doc.py")],
                             cwd=HERE, capture_output=True, text=True).stdout.rstrip("\n")
        print("  §7.1 用例表：%s" % ("一致 ✅" if gen and gen in doc else "不一致 ❌"))
        if not (gen and gen in doc):
            failures.append("§7.1 与 manifest_doc.py 输出不一致")
        stat = subprocess.run([sys.executable, os.path.join(HERE, "codestat.py"),
                               os.path.join(REPO, REL_PARSER), target],
                              capture_output=True, text=True).stdout.rstrip("\n")
        print("  §3.4 规模表：%s" % ("一致 ✅" if stat and stat in doc else "不一致 ❌"))
        if not (stat and stat in doc):
            failures.append("§3.4 与 codestat.py 输出不一致")

        print("\n═══ 4/4 汇总 ═══")
        if failures:
            for f in failures:
                print("  ❌ " + f)
            return 1
        print("  全部通过 ✅")
        return 0
    finally:
        if keep:
            print("\n临时目录保留：%s" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
