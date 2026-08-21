# -*- coding: utf-8 -*-
"""R077/R054 修复前后双侧对比漂移扫描（v1.6.1.9）

对同一批语料分别用基线代码与当前代码执行审核，逐条比对 R077/R054 触发差异。
任何无法解释的变化都导致失败退出——这是防止后续修改悄悄改变判定口径的防线。

用法：
    python tests/qa/verify_r077_r054_drift.py              # 默认与 v1.6.1.9 修复前基线对比
    python tests/qa/verify_r077_r054_drift.py <commit>      # 与指定基线 commit 对比

退出码：0=变化全部符合预期，1=存在异常或无法解释的变化
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# v1.6.1.9 修复前基线（distributed.py 最后一次修改前的 commit）
DEFAULT_BASELINE = "80fe10d"

# 预期变化：只有这 5 张生产表的 R077/R054 触发状态应该改变
EXPECTED_CHANGES = {
    # (文件名关键词, 规则): 改前触发 → 改后不触发
    ("report_03", "R077"): (True, False),   # HASH 分片表误报消除
    ("report_05", "R077"): (True, False),   # 广播表误报消除
    ("report_05", "R054"): (True, False),   # 广播表误报消除
    ("report_08", "R077"): (True, False),
    ("report_08", "R054"): (True, False),
    ("report_11", "R077"): (True, False),
    ("report_11", "R054"): (True, False),
    ("report_13", "R077"): (True, False),
    ("report_13", "R054"): (True, False),
}

# docker/mysql/init.sql 中 4 张表的 R054 误报消除（FIX-4 附带修复）
# 这些表的 DDL 根本没有 shardkey= 子句，SHARDKEY= 只出现在注释标题中，
# 改前 R054 从注释读出列名再报"不在主键中"——彻头彻尾的误报。
EXPECTED_DOCKER_INIT_R054 = 4  # init.sql 中预期 R054 消失的数量


def _load_module_from_source(source: str, module_name: str):
    """从源码字符串动态加载模块"""
    spec = importlib.util.spec_from_loader(module_name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(source, f"<{module_name}>", "exec"), mod.__dict__)
    return mod


def _get_git_file(commit: str, path: str) -> str:
    """从 git 历史读取文件内容"""
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        capture_output=True, text=True, encoding="utf-8",
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
    )
    if result.returncode != 0:
        raise RuntimeError(f"git show {commit}:{path} 失败: {result.stderr}")
    return result.stdout


def _collect_corpus() -> list[tuple[str, str]]:
    """收集全量语料：仓库 .sql 文件 + 生产 fixture"""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    corpus = []

    import glob
    for fpath in glob.glob(os.path.join(repo_root, "**/*.sql"), recursive=True):
        rel = os.path.relpath(fpath, repo_root)
        if any(skip in rel for skip in ("dist", ".git", "node_modules", "_retest_report_worktree")):
            continue
        with open(fpath, encoding="utf-8", errors="replace") as f:
            content = f.read()
        for chunk in content.split(";\n"):
            chunk = chunk.strip()
            if not chunk:
                continue
            lines = [l for l in chunk.splitlines() if l.strip() and not l.strip().startswith("--")]
            if lines:
                corpus.append((rel, chunk))

    return corpus


def _audit_with_module(mod, sql: str) -> tuple[bool, bool]:
    """用指定模块的 R077/R054 审核一条 SQL，返回 (r077_triggered, r054_triggered)"""
    from backend.engine.parser import SQLParser
    parser = SQLParser()
    parsed = parser.parse(sql)

    r077_cls = getattr(mod, "R077CreateTableMustHaveShardKey", None)
    r054_cls = getattr(mod, "R054ShardKeyMustBePrimaryKey", None)

    r077_hit = False
    r054_hit = False

    if r077_cls:
        v = r077_cls().check(parsed)
        r077_hit = v is not None
    if r054_cls:
        v = r054_cls().check(parsed)
        r054_hit = v is not None

    return r077_hit, r054_hit


def main():
    baseline = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASELINE
    print(f"基线 commit: {baseline}")
    print(f"当前代码: HEAD (工作目录)")
    print()

    # 加载两个版本的 distributed.py
    old_source = _get_git_file(baseline, "backend/engine/rules/distributed.py")
    new_source_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "backend", "engine", "rules", "distributed.py")
    with open(new_source_path, encoding="utf-8") as f:
        new_source = f.read()

    print(f"基线 distributed.py: {len(old_source)} 字节")
    print(f"当前 distributed.py: {len(new_source)} 字节")
    print()

    # 动态加载
    old_mod = _load_module_from_source(old_source, "distributed_old")
    new_mod = _load_module_from_source(new_source, "distributed_new")

    # 收集语料
    corpus = _collect_corpus()
    total = len(corpus)
    print(f"语料总数: {total}")

    # 双侧对比
    errors = 0
    changes = []  # (source, sql_preview, rule, old_hit, new_hit)

    for i, (source, sql) in enumerate(corpus):
        try:
            old_r077, old_r054 = _audit_with_module(old_mod, sql)
            new_r077, new_r054 = _audit_with_module(new_mod, sql)

            for rule, old_hit, new_hit in [
                ("R077", old_r077, new_r077),
                ("R054", old_r054, new_r054),
            ]:
                if old_hit != new_hit:
                    changes.append((source, sql[:120], rule, old_hit, new_hit))
        except Exception as e:
            errors += 1
            print(f"[ERROR] #{i+1} {source}: {e}")

    print(f"解析成功: {total - errors}")
    print(f"异常数: {errors}")
    print()

    # 分析变化
    print(f"{'='*70}")
    print(f"变化总数: {len(changes)}")
    print(f"{'='*70}")

    explained = 0
    unexplained = []

    for source, preview, rule, old_hit, new_hit in changes:
        # 检查是否在预期变化清单中
        matched = False
        for (keyword, exp_rule), (exp_old, exp_new) in EXPECTED_CHANGES.items():
            if keyword in source and rule == exp_rule and old_hit == exp_old and new_hit == exp_new:
                explained += 1
                print(f"  [预期] {source} | {rule}: {'触发' if old_hit else '无'} → {'触发' if new_hit else '无'}")
                matched = True
                break

        if not matched:
            # docker/init.sql 的 FIX-4 附带修复：注释污染型 R054 误报消除
            if "docker" in source and "init.sql" in source and rule == "R054" and old_hit and not new_hit:
                explained += 1
                print(f"  [预期·FIX-4附带] {source} | {rule}: 触发 → 无（注释污染误报修复）")
                matched = True

        if not matched:
            unexplained.append((source, preview, rule, old_hit, new_hit))
            direction = "新增触发" if new_hit else "不再触发"
            print(f"  [未解释] {source} | {rule}: {'触发' if old_hit else '无'} → {'触发' if new_hit else '无'} ({direction})")
            print(f"    SQL: {preview}...")

    print()
    print(f"已解释: {explained}/{len(changes)}")
    print(f"未解释: {len(unexplained)}/{len(changes)}")

    ok = True
    if errors > 0:
        print(f"\n[FAIL] 存在 {errors} 条解析异常")
        ok = False
    if unexplained:
        print(f"[FAIL] 存在 {len(unexplained)} 条无法解释的变化")
        ok = False
    if len(changes) == 0:
        print("[WARN] 双侧对比零变化——修复可能未生效")

    if ok and explained == len(changes) and len(changes) > 0:
        print(f"\n[PASS] 全部 {len(changes)} 条变化均已解释，无异常")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
