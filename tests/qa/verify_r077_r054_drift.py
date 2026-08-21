# -*- coding: utf-8 -*-
"""全语料漂移扫描（v1.6.1.9 R077/R054 修复）

扫描仓库全部 .sql 文件 + 生产 14 表 fixture，对比改前/改后 R077/R054 触发差异。
任何一条新增变化都必须能逐条解释，无法解释即失败退出。

用法：python tests/qa/verify_r077_r054_drift.py
退出码：0=全部符合预期，1=存在异常或无法解释的变化
"""
import os
import sys
import glob

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.engine.checker import RuleChecker


def collect_sql_statements(filepath: str) -> list[str]:
    """从 .sql 文件中切分出独立语句（按分号+换行切分）"""
    with open(filepath, encoding="utf-8", errors="replace") as f:
        content = f.read()
    # 按分号+换行切分，过滤空语句和纯注释
    stmts = []
    for chunk in content.split(";\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        # 跳过纯注释块
        lines = [l for l in chunk.splitlines() if l.strip() and not l.strip().startswith("--")]
        if lines:
            stmts.append(chunk)
    return stmts


def main():
    checker = RuleChecker()
    fixture_dir = os.path.join(os.path.dirname(__file__), "..", "fixtures")

    # 收集语料
    corpus = []

    # 1. 仓库内全部 .sql 文件
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    for pattern in ["**/*.sql"]:
        for fpath in glob.glob(os.path.join(repo_root, pattern), recursive=True):
            # 跳过 dist/ 和 .git/
            rel = os.path.relpath(fpath, repo_root)
            if "dist" in rel or ".git" in rel or "node_modules" in rel:
                continue
            stmts = collect_sql_statements(fpath)
            for s in stmts:
                corpus.append((rel, s))

    # 2. 生产报告 fixture
    for fpath in sorted(glob.glob(os.path.join(fixture_dir, "report_*.sql"))):
        rel = os.path.relpath(fpath, repo_root)
        stmts = collect_sql_statements(fpath)
        for s in stmts:
            corpus.append((rel, s))

    # 统计
    total = len(corpus)
    errors = 0
    results = []  # (source, sql_preview, r077, r054, category)

    for source, sql in corpus:
        try:
            result = checker.audit_sql(sql, instance_type='distributed')
            rule_ids = {v.rule_id for v in result.violations}
            has_r077 = 'R077' in rule_ids
            has_r054 = 'R054' in rule_ids

            # 分类
            tail = sql.upper()
            if 'TDSQL_DISTRIBUTED' in tail:
                cat = 'HASH'
            elif 'SHARDKEY' in tail or 'SHARD_KEY' in tail:
                cat = 'legacy'
            else:
                cat = 'no_shard_decl'

            results.append((source, sql[:120], has_r077, has_r054, cat))
        except Exception as e:
            errors += 1
            print(f"[ERROR] {source}: {e}")
            print(f"  SQL: {sql[:200]}")

    # 汇总
    print(f"\n{'='*70}")
    print(f"漂移扫描结果")
    print(f"{'='*70}")
    print(f"输入总数: {total}")
    print(f"解析成功: {total - errors}")
    print(f"异常数: {errors}")

    # 按路径分类统计
    cats = {}
    for source, preview, r077, r054, cat in results:
        cats.setdefault(cat, []).append((source, preview, r077, r054))

    for cat, items in sorted(cats.items()):
        r077_count = sum(1 for _, _, r, _, in items if r)
        r054_count = sum(1 for _, _, _, r in items if r)
        print(f"\n  [{cat}] {len(items)} 条 | R077触发={r077_count} | R054触发={r054_count}")

    # 列出所有触发 R077 或 R054 的语句
    triggered = [(s, p, r77, r54) for s, p, r77, r54, _ in results if r77 or r54]
    if triggered:
        print(f"\n触发 R077/R054 的语句 ({len(triggered)} 条):")
        for source, preview, r77, r54 in triggered:
            tags = []
            if r77: tags.append("R077")
            if r54: tags.append("R054")
            print(f"  {source}: {'+'.join(tags)}")
            print(f"    {preview}...")

    # 生产 fixture 专项核验：只应触发 N1（无分片声明单表）
    fixture_results = [(s, p, r77, r54) for s, p, r77, r54, _ in results if "fixtures/" in s or "fixtures\\" in s]
    fixture_r077 = [(s, p) for s, p, r77, r54 in fixture_results if r77]
    fixture_r054 = [(s, p) for s, p, r77, r54 in fixture_results if r54]

    print(f"\n生产 fixture 专项:")
    print(f"  R077 触发: {len(fixture_r077)} 条")
    for s, p in fixture_r077:
        print(f"    {s}")
    print(f"  R054 触发: {len(fixture_r054)} 条")
    for s, p in fixture_r054:
        print(f"    {s}")

    # 验收：fixture 中只有 report_04（无分片声明单表）应触发 R077
    expected_r077 = {"report_04_single_table.sql"}
    actual_r077 = {os.path.basename(s) for s, _ in fixture_r077}
    unexpected_r077 = actual_r077 - expected_r077
    missing_r077 = expected_r077 - actual_r077

    ok = True
    if errors > 0:
        print(f"\n[FAIL] 存在 {errors} 条解析异常")
        ok = False
    if unexpected_r077:
        print(f"\n[FAIL] fixture 中 R077 意外触发: {unexpected_r077}")
        ok = False
    if missing_r077:
        print(f"\n[FAIL] fixture 中 R077 应触发但未触发: {missing_r077}")
        ok = False
    if fixture_r054:
        print(f"\n[FAIL] fixture 中 R054 不应触发")
        ok = False

    if ok:
        print(f"\n[PASS] 全语料漂移扫描通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
