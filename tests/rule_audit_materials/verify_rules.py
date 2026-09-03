# -*- coding: utf-8 -*-
"""121 条规则覆盖验证 harness（本地引擎驱动，无需启动服务）

工作原理
========
直接调用 backend.engine.checker.RuleChecker —— 这正是「文件审核」与
「在线元数据审核」两条链路共用的同一套引擎（audit_service.audit_file_content
与 extract-and-audit 最终都落到 RuleChecker.audit_sql）。因此本 harness
在本地对每条样例 SQL 跑出的规则命中情况，与生产文件审核的结果一致。

测试物料格式约定
================
每个 .sql 文件由若干「样例块」组成。每个样例块以一行注解开头的 @rules 标注：

    -- @case: R003_01
    -- @rules: R003,R004,R005
    -- @rules.dist: R003,R004,R005,R077   (可选：分布式口径的期望，缺省回退 @rules)
    -- @rules.cent: R003,R004            (可选：集中式口径的期望，缺省回退 @rules)
    -- @scope: distributed        (可选: all[默认] / distributed / centralized)
    -- @note: 缺主键 + 缺引擎 + 缺字符集
    CREATE TABLE t_demo (...);

语义：
  @rules       该样例默认应触发的规则集合（精确匹配，多触发/漏触发都判失败）
  @rules.dist  分布式口径专用期望（可选）；@rules.cent 集中式口径专用期望（可选）。
               某口径未给专用期望时回退到 @rules。用于一条语句在两种实例
               类型下合理共触发不同规则的场景（如分布式额外的 R077）。
  @scope  distributed = 仅分布式规则，只在 distributed 口径下断言；
          centralized = 仅集中式相关，只在 centralized 口径下断言；
          all（默认）  = 在 distributed 与 centralized 两种口径下都断言。
  未标注 @rules 的语句（如建表铺垫、纯演示）不参与断言。

运行
====
    python tests/rule_audit_materials/verify_rules.py            # 全量 + 覆盖报告
    python tests/rule_audit_materials/verify_rules.py --verbose  # 打印每条样例明细
    python tests/rule_audit_materials/verify_rules.py --json out.json

退出码：0=全部通过且 121 条规则全覆盖；1=存在失败或未覆盖规则。
"""
import argparse
import json
import os
import re
import sys

# Windows 控制台默认 GBK，强制 stdout/stderr 用 UTF-8，避免中文/emoji 输出崩溃
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 使本脚本可独立运行（无需 pytest 环境）
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from backend.engine.checker import RuleChecker  # noqa: E402
from backend.engine.rules import ALL_RULE_CLASSES  # noqa: E402

ALL_RULE_IDS = sorted({cls().rule_id for cls in ALL_RULE_CLASSES})

# 已知“文件审核路径无法触发”的规则（解析器限制，非规则缺失）：
#   R038 大表禁自增主键：规则检查 col.raw_type 是否含 'auto_increment'，
#        而解析器 raw_type 仅为数据类型 SQL（如 'BIGINT'），AUTO_INCREMENT
#        作为列约束不会进入 raw_type，故任何 CREATE TABLE 都无法触发该规则。
#   R049 表别名规范：规则体在文件审核分支恒 return None（仅占位，未实现检测）。
#   R035 多表同含义字段类型一致：本 harness 走单条 audit_sql（table_metadata=None），
#        无批内跨表依据故不可达；v1.6.3.2/REQ-05A 起 audit_file 批量路径已通过
#        __r035_cross_table_columns__ 保留键构造请求内跨表上下文激活 R035（见
#        tests/test_rules_v1632.py 的跨表用例），此处豁免仅针对单条 harness 路径。
#   R025 禁改分片键：依赖 parsed.alter_actions，而解析器对 ALTER TABLE 恒不
#        填充 alter_actions（实测为空），规则循环从不执行，不可达。
#   R059 禁分布式事务：规则要求 is_begin 且 table_metadata 非空；而 BEGIN 语句
#        无表，with-metadata 端点按 SQL 涉及的表拉元数据，BEGIN 恒得空元数据，不可达。
#   上述规则不列入覆盖要求；R025/R038/R049 待解析器/规则增强后移除豁免。
KNOWN_DEAD = {"R038", "R049", "R035", "R025", "R059"}

# 需真实表元数据（分片键/索引）才能触发的规则，不在文件审核（无元数据）中
# 验证，而由 verify_metadata_rules.py 调用 /api/v1/tdsql/audit/with-metadata
# 端点对云上分布式实例验证（见测试说明书第5章）。
METADATA_DEPENDENT = {"R048", "R055", "R056", "R057", "R058", "R060", "R064"}

_CASE_RE = re.compile(r"--\s*@case:\s*(\S+)", re.IGNORECASE)
_RULES_RE = re.compile(r"--\s*@rules:\s*([A-Za-z0-9_,\s]*)", re.IGNORECASE)
_RULES_DIST_RE = re.compile(r"--\s*@rules\.dist:\s*([A-Za-z0-9_,\s]*)", re.IGNORECASE)
_RULES_CENT_RE = re.compile(r"--\s*@rules\.cent:\s*([A-Za-z0-9_,\s]*)", re.IGNORECASE)
_SCOPE_RE = re.compile(r"--\s*@scope:\s*(\w+)", re.IGNORECASE)
# 任意 @ 元注解行（@case/@rules/@scope/@note 等）：送审前必须剥离，
# 否则注解里的中文/全角括号/关键字会被当成 SQL 内容触发规则（如 R104/R051）。
_META_RE = re.compile(r"^\s*--\s*@\w+")

# 非规则型的诊断码（语法错误等），不计入规则覆盖与期望比对
_DIAG_CODES = {"E999_SYNTAX_ERROR"}


def _parse_rules(s):
    return {r.strip().upper() for r in s.split(",") if r.strip()}


def split_cases(sql_text: str):
    """把 .sql 文件切分为 (case_id, expect, scope, sql) 列表。

    expect 为 {"dist": set|None, "cent": set|None, "base": set}。
    以出现 @rules 标注的注释块作为新样例的起点；该注释块之后、下一个
    @rules 块之前的所有非注释 SQL 归入当前样例。
    """
    lines = sql_text.splitlines()
    cases = []
    cur = None            # dict(case_id, expect, scope, sql_lines)
    pending_meta = {}     # 暂存当前注释块里的 @case/@scope

    def flush():
        nonlocal cur
        if cur and cur["expect"]["base"] is not None:
            # 剥离所有整行注释（@ 注解与普通 -- 注释）：引擎审核时本就会忽略
            # 注释，且注解里的中文/全角括号/COMMIT 等字样若残留会误触发
            # R104/R071 等规则。样例 SQL 只保留真正的语句内容。
            sql_lines = [l for l in cur["sql_lines"]
                         if not l.strip().startswith("--")]
            sql = "\n".join(sql_lines).strip()
            if sql:
                cases.append((cur["case_id"], cur["expect"],
                              cur["scope"], sql))
        cur = None

    for line in lines:
        stripped = line.strip()
        m_rules = _RULES_RE.search(stripped)
        if m_rules and not _RULES_DIST_RE.search(stripped) \
                and not _RULES_CENT_RE.search(stripped):
            flush()
            case_id = pending_meta.get("case_id") or f"auto_{len(cases)+1}"
            scope = pending_meta.get("scope", "all").lower()
            cur = {"case_id": case_id,
                   "expect": {"base": _parse_rules(m_rules.group(1)),
                              "dist": None, "cent": None},
                   "scope": scope, "sql_lines": []}
            pending_meta = {}
            continue
        if stripped.startswith("--"):
            mc = _CASE_RE.search(stripped)
            if mc:
                pending_meta["case_id"] = mc.group(1)
            ms = _SCOPE_RE.search(stripped)
            if ms:
                pending_meta["scope"] = ms.group(1)
                # @scope 可置于 @rules 之后，此时用例已创建，直接更新其 scope
                if cur is not None:
                    cur["scope"] = ms.group(1).lower()
            # 分口径期望：追加到当前样例
            md = _RULES_DIST_RE.search(stripped)
            if md and cur is not None:
                cur["expect"]["dist"] = _parse_rules(md.group(1))
            mce = _RULES_CENT_RE.search(stripped)
            if mce and cur is not None:
                cur["expect"]["cent"] = _parse_rules(mce.group(1))
            # @ 元注解行不进 SQL；普通注释若已在样例内则保留（引擎会自行剥离）
            if cur is not None and not _META_RE.match(line):
                cur["sql_lines"].append(line)
            continue
        if cur is not None:
            cur["sql_lines"].append(line)
    flush()
    return cases


def _violated_rule_ids(checker, sql, instance_type):
    res = checker.audit_sql(sql, instance_type=instance_type)
    return {v.rule_id for v in res.violations if v.rule_id not in _DIAG_CODES}


# MyBatis XML 样例：<select/insert/update/delete> 语句块，其前方注释含 @rules。
_XML_CASE_RE = re.compile(
    r"<!--\s*@case:\s*(\S+)\s*-->\s*"
    r"(?:<!--[^>]*-->\s*)*?"
    r"<!--\s*@rules:\s*([A-Za-z0-9_,\s]*)\s*-->\s*"
    r"(?:<!--[^>]*-->\s*)*?"
    r"<(select|insert|update|delete)\b[^>]*>(.*?)</\1>",
    re.DOTALL | re.IGNORECASE)


def split_xml_cases(xml_text: str, checker):
    """从 MyBatis XML 提取 (case_id, expect, scope, cleaned_sql)。

    语句体用引擎同款 _clean_mybatis_sql 清洗（剥离动态标签、#{}→?），
    保证与生产文件审核（audit_file_content）行为一致。
    """
    cases = []
    for m in _XML_CASE_RE.finditer(xml_text):
        case_id = m.group(1)
        expect = {"base": _parse_rules(m.group(2)), "dist": None, "cent": None}
        body = m.group(4)
        sql = checker._clean_mybatis_sql(body).strip()
        if sql:
            cases.append((case_id, expect, "all", sql))
    return cases


def verify_file(checker, path, verbose=False):
    """返回 (failures, fired_ids)；failures 为描述字符串列表。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    fname = os.path.basename(path)
    return _check_cases(checker, split_cases(text), fname, verbose)


def verify_xml_file(checker, path, verbose=False):
    """校验 MyBatis XML 物料。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    fname = os.path.basename(path)
    return _check_cases(checker, split_xml_cases(text, checker), fname, verbose)


def _check_cases(checker, cases, fname, verbose):
    failures = []
    fired = set()
    for case_id, expect, scope, sql in cases:
        scopes = ["distributed", "centralized"] if scope == "all" else [scope]
        for it in scopes:
            exp = expect["dist" if it == "distributed" else "cent"]
            if exp is None:
                exp = expect["base"]
            actual = _violated_rule_ids(checker, sql, it)
            fired |= actual
            missing = exp - actual
            extra = actual - exp
            if missing or extra:
                failures.append(
                    f"[{fname}::{case_id}@{it}] 期望={sorted(exp)} "
                    f"实际={sorted(actual)} "
                    f"漏触发={sorted(missing)} 多触发={sorted(extra)}")
            elif verbose:
                print(f"  OK {fname}::{case_id}@{it} -> {sorted(actual)}")
    return failures, fired


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    checker = RuleChecker(dialect="mysql")
    sql_dir = os.path.join(_HERE, "sql_audit")
    files = sorted(
        os.path.join(sql_dir, f) for f in os.listdir(sql_dir)
        if f.endswith(".sql")) if os.path.isdir(sql_dir) else []
    xml_dir = os.path.join(_HERE, "mybatis_xml")
    xml_files = sorted(
        os.path.join(xml_dir, f) for f in os.listdir(xml_dir)
        if f.endswith(".xml")) if os.path.isdir(xml_dir) else []

    all_failures = []
    all_fired = set()
    per_file = {}
    for path in files:
        fails, fired = verify_file(checker, path, verbose=args.verbose)
        all_failures += fails
        all_fired |= fired
        per_file[os.path.basename(path)] = {
            "cases_fired": sorted(fired),
            "failures": fails,
        }
    for path in xml_files:
        fails, fired = verify_xml_file(checker, path, verbose=args.verbose)
        all_failures += fails
        all_fired |= fired
        per_file[os.path.basename(path)] = {
            "cases_fired": sorted(fired),
            "failures": fails,
        }

    covered = sorted(rid for rid in ALL_RULE_IDS if rid in all_fired)
    uncovered = sorted(rid for rid in ALL_RULE_IDS
                       if rid not in all_fired
                       and rid not in KNOWN_DEAD
                       and rid not in METADATA_DEPENDENT)
    dead_unfired = sorted(rid for rid in KNOWN_DEAD if rid not in all_fired)
    meta_unfired = sorted(rid for rid in METADATA_DEPENDENT if rid not in all_fired)

    print("=" * 70)
    print(f"规则总数: {len(ALL_RULE_IDS)}  文件审核已覆盖: {len(covered)}  "
          f"未覆盖: {len(uncovered)}")
    print(f"  其中 需元数据验证(走 with-metadata 端点): {len(meta_unfired)} -> {','.join(meta_unfired)}")
    print(f"  其中 已知不可触发(豁免): {len(dead_unfired)} -> {','.join(dead_unfired)}")
    if uncovered:
        print("未覆盖规则: " + ", ".join(uncovered))
    print(f"断言失败: {len(all_failures)} 条")
    for f in all_failures:
        print("  FAIL " + f)
    print("=" * 70)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({
                "total_rules": len(ALL_RULE_IDS),
                "covered": covered,
                "uncovered": uncovered,
                "failures": all_failures,
                "per_file": per_file,
            }, f, ensure_ascii=False, indent=2)
        print(f"明细已写入 {args.json}")

    ok = (not all_failures) and (not uncovered)
    print("结论: " + ("[PASS] 断言全过且规则全覆盖（除已知不可触发）" if ok
                       else "[FAIL] 存在失败或未覆盖规则"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
