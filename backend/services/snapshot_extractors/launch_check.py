"""上线检查（launch_check）问题项抽取器

设计依据：docs/DESIGN-v1.5.2-上线检查历史保留与对比.md §7.2

指纹：fp("launch_check", check_id, 数据库, 表名, 列名)
  C01 为唯一的聚合行（无表名），指纹改用 排序规则 作为区分位。

【红线】度量值（表数量/索引数/字段数/字符数）一律进 attrs，严禁进指纹。
       否则索引数 5→8 会被判成"旧问题已解决 + 新问题出现"，制造虚假整改。

命名说明（三个命名空间的对应关系，勿混淆）：
  前端路由 schema-check  ←→  inspection_type='schema_check'  ←→  快照 module='launch_check'
  取 launch_check 而非 schema_check，是为了与既有模块 schema_audit
  （在线元数据审核）在同一下拉框中清晰可分。
"""
import logging

from .base import IssueItem, fp

logger = logging.getLogger(__name__)

# 明细行中的固定列名（SchemaInspector 的 SQL 用中文别名，见 schema_inspector.py）
_K_DB, _K_TABLE, _K_COL = "数据库", "表名", "列名"

# 各检查项的【区分位】与【度量位】定义，逐项依据见设计文档 §7.2
#   extra_key : 参与指纹的附加列（除 库/表/列 外）
#   metrics   : 进 attrs 的度量列（【严禁】进指纹）
#   attrs     : 进 attrs 的属性列（用于 CHANGED 判定，不进指纹）
_CHECK_SPEC = {
    "C01": {"extra_key": ["排序规则"], "metrics": ["表数量"], "attrs": []},
    "C02": {"extra_key": [], "metrics": [], "attrs": ["类型", "排序规则"]},
    "C03": {"extra_key": [], "metrics": [], "attrs": ["排序规则"]},
    "C04": {"extra_key": [], "metrics": [], "attrs": ["类型", "排序规则"]},
    "C05": {"extra_key": [], "metrics": ["字符数"], "attrs": []},
    "C06": {"extra_key": [], "metrics": ["索引数"], "attrs": []},
    "C07": {"extra_key": [], "metrics": [], "attrs": []},
    "C08": {"extra_key": [], "metrics": [], "attrs": ["类型"]},
    "C09": {"extra_key": [], "metrics": [], "attrs": ["当前注释"]},
    "C10": {"extra_key": [], "metrics": [], "attrs": ["当前注释"]},
    "C11": {"extra_key": [], "metrics": ["字段数"], "attrs": []},
    "C12": {"extra_key": [], "metrics": [], "attrs": ["类型"]},
}

# 未登记的检查项（将来新增 C13+ 时）的保守兜底：
#   全部非 库/表/列 的列都当作 attrs，不进指纹、不当度量。
# 这样新增检查项即使漏改本文件也只会退化成"属性变化不敏感"，
# 而不会因把度量写进指纹而制造虚假整改。
_DEFAULT_SPEC = {"extra_key": [], "metrics": [], "attrs": None}   # None = 全部剩余列


def _num(v):
    """度量值转数字；转不了就原样返回（用于 CHANGED 的数值比较）"""
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return v


def extract(results: list, database_filter: str = "") -> tuple[list, int]:
    """从 SchemaInspector.inspect() 的【完整】结果抽取问题项。

    Args:
        results: inspect() 的返回值（内存中完整数据，【不是】写库时截断到
                 100 行的副本 —— 用截断副本会让快照静默丢失问题项，
                 下次对比时它们会显示为"已解决"）
        database_filter: 本次检查范围，空串表示全部数据库

    Returns:
        (IssueItem 列表, 被检查对象总数)
    """
    items = []
    object_keys = set()

    for check in results or []:
        cid = str(check.get("id") or "")
        if check.get("error"):
            continue                     # 执行失败的检查项不产出问题项
        spec = _CHECK_SPEC.get(cid, _DEFAULT_SPEC)
        sev = (check.get("severity") or "WARNING").upper()
        cname = check.get("name") or cid
        sug = check.get("suggestion") or ""

        for row in (check.get("rows") or []):
            if not isinstance(row, dict):
                continue
            db = str(row.get(_K_DB, "") or "")
            table = str(row.get(_K_TABLE, "") or "")
            col = str(row.get(_K_COL, "") or "")

            # ── 指纹区分位 ──
            extra = [str(row.get(k, "") or "") for k in spec["extra_key"]]
            key = fp("launch_check", cid, db, table, col, *extra)

            # ── attrs：度量 + 属性 ──
            attrs = {}
            for k in spec["metrics"]:
                if k in row:
                    attrs[k] = _num(row[k])
            attr_keys = spec["attrs"]
            if attr_keys is None:        # 兜底：除 库/表/列 外全部当属性
                attr_keys = [k for k in row
                             if k not in (_K_DB, _K_TABLE, _K_COL)]
            for k in attr_keys:
                if k in row:
                    attrs[k] = row[k]

            # ── 展示文案 ──
            obj = ".".join(p for p in (db, table) if p) or db
            obj_full = f"{obj}.{col}" if col else obj
            detail = " | ".join(f"{k}: {v}" for k, v in row.items())

            items.append(IssueItem(
                key=key,
                object_name=obj_full,
                object_type="COLUMN" if col else ("TABLE" if table else "SCHEMA"),
                issue_type=cid,
                severity=sev,
                title=f"[{cid}] {cname}：{obj_full}",
                detail=detail,
                suggestion=sug,
                attrs=attrs,
            ))
            if obj:
                object_keys.add(obj)

    return items, len(object_keys)
