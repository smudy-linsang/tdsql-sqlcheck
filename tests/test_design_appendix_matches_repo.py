# -*- coding: utf-8 -*-
"""G14 设计附录 ↔ 仓库落盘文件的逐字一致性门禁（SIT2 / DEF-SIT2-01 ③）。

设计文档是"照图施工级"：附录 A.1～A.4 即唯一可施工源。第一轮 SIT 整改时
仓库测试文件加了 2 条用例、API 文件头改了版本号，但设计附录未回填，
导致"照 Rev.N 附录施工只能得到 110 项，丢掉的恰是防 DEF-SIT-01 复发的守卫"。

本用例把 SIT2 报告 §6.1.3③ 的比对脚本固化为可执行门禁：
附录与仓库任何一处不一致即红灯，替代人工"附录逐字核对"验收项。
"""
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_DOC = _REPO / "docs" / "DESIGN-v1.6.3.0-深度诊断表类型统计子模块详细设计说明书.md"

_MAP = {
    "A.1": "backend/services/table_type_stats_service.py",
    "A.2": "backend/api/table_type_stats.py",
    "A.3": "backend/schema/v13/130_table_type_stats.sql",
    "A.4": "tests/test_table_type_stats.py",
}


def _extract_appendix_blocks():
    """抽出附录 A 各小节中最大的代码围栏块（即成品代码）。"""
    if not _DOC.exists():
        pytest.skip("设计文档不存在（附录一致性门禁无从比对）")
    lines = _DOC.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("## 14. 附录 A"))
    end = next(i for i, l in enumerate(lines) if l.startswith("## 15. 附录 B"))
    seg = lines[start:end]
    secs = [(i, re.match(r"^###\s+(A\.\d+)", l).group(1))
            for i, l in enumerate(seg) if re.match(r"^###\s+A\.\d+", l)]
    blocks = {}
    for idx, (i, name) in enumerate(secs):
        if name not in _MAP:
            continue
        j = secs[idx + 1][0] if idx + 1 < len(secs) else len(seg)
        body = seg[i:j]
        fences = [k for k, l in enumerate(body) if l.startswith("```")]
        pairs = list(zip(fences[0::2], fences[1::2]))
        assert pairs, f"附录 {name} 内没有找到任何完整代码围栏"
        a, b = max(pairs, key=lambda p: p[1] - p[0])
        blocks[name] = "\n".join(body[a + 1:b]) + "\n"
    return blocks


@pytest.mark.parametrize("name", sorted(_MAP))
def test_design_appendix_matches_repo(name):
    """附录 A.N 的最大代码块必须与仓库落盘文件逐字一致。"""
    blocks = _extract_appendix_blocks()
    assert name in blocks, f"设计文档附录缺少 {name} 小节或其代码块"
    design = blocks[name]
    repo_file = _REPO / _MAP[name]
    assert repo_file.exists(), f"仓库文件缺失: {_MAP[name]}"
    repo = repo_file.read_text(encoding="utf-8")
    if design != repo:
        d_lines, r_lines = design.split("\n"), repo.split("\n")
        first_diff = next(
            (i for i in range(min(len(d_lines), len(r_lines)))
             if d_lines[i] != r_lines[i]),
            min(len(d_lines), len(r_lines)))
        pytest.fail(
            f"附录 {name}（{_MAP[name]}）与仓库落盘不一致：\n"
            f"  设计行数={len(d_lines) - 1} 仓库行数={len(r_lines) - 1} "
            f"首个差异在第 {first_diff + 1} 行\n"
            f"  设计: {d_lines[first_diff]!r}\n"
            f"  仓库: {r_lines[first_diff]!r}\n"
            "照图施工级文档要求二者逐字一致：改仓库必须回填附录，改附录必须落盘。")
