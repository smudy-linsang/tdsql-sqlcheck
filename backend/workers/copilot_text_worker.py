# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：T09 纯文本 SQL 复核受控子进程（CP-W06，DETAIL §5.4）。

以固定模块 `python -m backend.workers.copilot_text_worker` 由 runner 派生；
从 stdin 读 JSON {sql, instance_type, rule_overrides_hash}，向 stdout 写单个
JSON 结果：
  {validation_mode:'TEXT_ONLY', parse_status, violations, skipped_checks,
   rule_snapshot_hash, semantic_equivalence:'NOT_PROVEN', executable}

禁止：连接目标库、调用 _save_audit_history/在线分片键补查、读取任意文件路径。
"""
from __future__ import annotations

import json
import sys


def _result(**kw) -> dict:
    base = {
        "validation_mode": "TEXT_ONLY",
        "parse_status": "UNKNOWN",
        "violations": [],
        "skipped_checks": [],
        "rule_snapshot_hash": "",
        "semantic_equivalence": "NOT_PROVEN",
        "executable": False,
    }
    base.update(kw)
    return base


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        print(json.dumps(_result(parse_status="INPUT_INVALID"), ensure_ascii=False))
        return 2
    sql = str(payload.get("sql") or "")
    instance_type = str(payload.get("instance_type") or "unknown")
    if instance_type not in ("distributed", "centralized", "unknown"):
        instance_type = "unknown"
    if not sql.strip():
        print(json.dumps(_result(parse_status="EMPTY"), ensure_ascii=False))
        return 0
    try:
        from backend.engine.checker import RuleChecker
        checker = RuleChecker()
        report = checker.audit_sql(sql, instance_type=instance_type)
        violations = []
        # audit_sql 返回 AuditResult（单条）；violations 逐条提取（限长）
        for v in (getattr(report, "violations", None) or [])[:50]:
            sev = getattr(v, "severity", "")
            if hasattr(sev, "value"):
                sev = sev.value
            violations.append({
                "rule_id": getattr(v, "rule_id", ""),
                "severity": str(sev),
                "message": str(getattr(v, "message", ""))[:300],
            })
        skipped = []
        # R035 跨表上下文检查在纯文本模式下未执行
        skipped.append("R035_CROSS_TABLE_CONTEXT")
        print(json.dumps(_result(
            parse_status="PARSED",
            violations=violations,
            skipped_checks=skipped,
            executable=False,  # executable 由服务端按四态合同最终判定
        ), ensure_ascii=False))
        return 0
    except Exception:
        print(json.dumps(_result(
            parse_status="PARSE_FAILED",
            skipped_checks=["RULECHECK_UNAVAILABLE"],
        ), ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
