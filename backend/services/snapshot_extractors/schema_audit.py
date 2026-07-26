"""
在线元数据审核（schema_audit）问题项抽取器

设计依据：docs/DETAIL-v1.3-扫描结果对比.md §5.3.1

指纹：fp(module, node, object_name, object_type, rule_id, disc[, seq])
  node 为 set 预留位，当前恒传 ""（F1 经代理审核逻辑 schema，端点内无 set 变量）。
"""
import json
import logging

from .base import IssueItem, fp, stable_text, parse_object

logger = logging.getLogger(__name__)


def _severity_str(sev) -> str:
    return (sev.value if hasattr(sev, "value") else str(sev or "WARNING")).upper()


def _build(obj_name: str, obj_type: str, rule_id: str, sev: str,
           message: str, suggestion: str, node: str,
           per_obj_rule: dict) -> IssueItem:
    """构造单个问题项，处理同对象同规则多次命中的区分位"""
    disc = fp(stable_text(message))[:8]
    ck = (obj_name, rule_id, disc)
    per_obj_rule[ck] = per_obj_rule.get(ck, 0) + 1
    seq = per_obj_rule[ck]
    key = fp("schema_audit", node, obj_name, obj_type, rule_id, disc,
             "" if seq == 1 else str(seq))
    return IssueItem(
        key=key,
        object_name=obj_name,
        object_type=obj_type,
        issue_type=rule_id,
        severity=sev,
        title=f"[{rule_id}] {message}"[:500],
        detail=message or "",
        suggestion=suggestion or "",
        attrs={"severity": sev, "node": node},
    )


def extract(results, db_name: str = "", node: str = "") -> tuple[list[IssueItem], int]:
    """从内存中的 AuditResult 列表抽取问题项（扫描实时路径）。

    必须消费内存对象而非 audit_history.results_json —— 后者 sql 被截断为 500 字符。
    """
    items, objects, per_obj_rule = [], set(), {}
    for r in results or []:
        sql = getattr(r, "sql", "") or ""
        obj_name, obj_type = parse_object(sql, db_name)
        objects.add(obj_name)
        for v in (getattr(r, "violations", None) or []):
            items.append(_build(
                obj_name, obj_type,
                getattr(v, "rule_id", "") or "",
                _severity_str(getattr(v, "severity", None)),
                getattr(v, "message", "") or "",
                getattr(v, "suggestion", "") or "",
                node, per_obj_rule,
            ))
    return items, len(objects)


def extract_from_json(results_json: str, db_name: str = "",
                      node: str = "") -> tuple[list[IssueItem], int]:
    """从 audit_history.results_json 抽取（存量回填路径）。

    sql 字段虽被截断为 500 字符，但对象名位于语句开头，不影响解析。
    """
    try:
        data = json.loads(results_json or "[]")
    except (ValueError, TypeError):
        logger.warning("results_json 解析失败，跳过")
        return [], 0

    items, objects, per_obj_rule = [], set(), {}
    for r in data or []:
        if not isinstance(r, dict):
            continue
        obj_name, obj_type = parse_object(r.get("sql", ""), db_name)
        objects.add(obj_name)
        for v in (r.get("violations") or []):
            if not isinstance(v, dict):
                continue
            items.append(_build(
                obj_name, obj_type,
                v.get("rule_id", "") or "",
                (v.get("severity") or "WARNING").upper(),
                v.get("message", "") or "",
                v.get("suggestion", "") or "",
                node, per_obj_rule,
            ))
    return items, len(objects)
