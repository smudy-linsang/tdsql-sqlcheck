"""
大表治理（bigtable）问题项抽取器

设计依据：docs/DETAIL-v1.3-扫描结果对比.md §5.3.3

指纹：fp(module, schema_name, table_name, issue_type)
  一张表可命中多个问题类型，各自独立成项。

分级口径沿用 backend/engine/bigtable_engine.py::BigTableClassifier：
  L1 关注级(50GB+) / L2 管控级(200GB+) / L3 严控级(500GB+)
  —— L3 最严重，L1 最轻（设计初稿此处写反，实现以 engine 为准）。
"""
import logging

from backend.services.database import _get_connection
from .base import IssueItem, fp

logger = logging.getLogger(__name__)

# 等级 → 严重度：L3/L2 视为 ERROR，L1 视为 WARNING
_LEVEL_SEVERITY = {"L3": "ERROR", "L2": "ERROR", "L1": "WARNING"}
_LEVEL_LABELS = {"L1": "关注级", "L2": "管控级", "L3": "严控级"}


def extract(connection_id: str, inspection_date: str) -> tuple[list[IssueItem], int]:
    """抽取指定实例某次盘点日期的大表治理问题项"""
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT schema_name, table_name, size_gb, rows_count, level,
                   is_partitioned, partition_count, shard_key
            FROM bigtable_inventory
            WHERE connection_id = ? AND inspection_date = ?
        """, (connection_id, inspection_date)).fetchall()
        # 分区水位异常（同日）
        wm_rows = conn.execute("""
            SELECT schema_name, table_name, watermark_percent, status
            FROM partition_watermarks
            WHERE connection_id = ? AND check_date = ?
        """, (connection_id, inspection_date)).fetchall()
    finally:
        conn.close()

    watermarks = {}
    for w in wm_rows or []:
        wd = dict(w)
        watermarks[(wd.get("schema_name"), wd.get("table_name"))] = wd

    items = []
    for r in rows or []:
        d = dict(r)
        schema, table = d.get("schema_name") or "", d.get("table_name") or ""
        obj = f"{schema}.{table}"
        level = (d.get("level") or "").upper()
        size_gb = float(d.get("size_gb") or 0)
        rows_count = int(d.get("rows_count") or 0)
        base_attrs = {"size_gb": size_gb, "rows_count": rows_count, "level": level}
        detail = (f"体量 {size_gb:.2f}GB / {rows_count} 行 / 等级 "
                  f"{level}{_LEVEL_LABELS.get(level, '')}")

        def _add(itype: str, sev: str, title: str, sug: str, extra: dict = None):
            attrs = dict(base_attrs)
            if extra:
                attrs.update(extra)
            items.append(IssueItem(
                key=fp("bigtable", schema, table, itype),
                object_name=obj, object_type="TABLE", issue_type=itype,
                severity=sev, title=title, detail=detail, suggestion=sug,
                attrs=attrs,
            ))

        _add("OVERSIZE", _LEVEL_SEVERITY.get(level, "WARNING"),
             f"大表 {obj} 体量 {size_gb:.2f}GB（等级 {level}{_LEVEL_LABELS.get(level, '')}）",
             "建议纳入分区/归档治理")

        if not d.get("is_partitioned"):
            _add("NO_PARTITION", "WARNING", f"{obj} 未做分区", "建议按时间维度分区")

        if not (d.get("shard_key") or "").strip():
            _add("NO_SHARD_KEY", "WARNING", f"{obj} 未识别分片键", "确认分布式分片键设置")

        wm = watermarks.get((schema, table))
        if wm and (wm.get("status") or "").upper() not in ("", "NORMAL", "OK"):
            pct = float(wm.get("watermark_percent") or 0)
            _add("PARTITION_WATERMARK", "ERROR",
                 f"{obj} 分区水位异常（{pct:.1f}%，状态 {wm.get('status')}）",
                 "请及时补充预留分区",
                 {"watermark_percent": pct, "watermark_status": wm.get("status") or ""})

    return items, len(rows or [])
