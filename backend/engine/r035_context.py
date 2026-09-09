# -*- coding: utf-8 -*-
"""R035 批内跨表字段类型检查的有界见证索引（v1.6.3.5 / FIX-01 / D01）。

设计出处：docs/DETAIL-v1.6.3.5-大库在线元数据审核稳定性修复.md §4.3。

背景：v1.6.3.2（REQ-05A）为实现 R035 跨表字段类型一致性，曾在
`RuleChecker.audit_file` 中全量预解析全部语句（AST 常驻），并为每条语句把
"此前累积的整张同名列索引"做 `{k: list(v)}` 全量浅拷贝，构成 O(N²) 内存膨胀，
6000+ 表大库时击穿 Web worker 内存致进程猝死（在线元数据审核 "Failed to fetch"）。

本模块用**有界见证索引**替代全历史快照：每个列名只保留至多 5 个见证引用槽位，
即可在不丢失"首个冲突来源"语义的前提下，把单批空间从 O(N²) 降到 O(U)（U=不同列名数）。

语义保持不变（DETAIL §4.1）：
  · 每个逻辑语句只解析一次；按原始顺序先与此前历史比，再把本语句的成功
    CREATE 列加入历史；无当前语句自引用、未来引用或跨请求缓存。
  · 列索引键为列名 lower()；表名精确字符串相等；类型比较沿用规范化 `type`，
    原始 `raw_type` 仅用于错误消息原文。
  · 解析失败 / 非 CREATE / 无表名 / 无列名的语句不进入历史。

等价性：anchor + outside_anchor_table + outside_anchor_type 三组见证覆盖了
"查当前表 T、类型 Y"的三种情形，使投影内首个冲突 = 完整历史扫描的首个冲突；
独立模型（DETAIL 附录 B）已对 66,430 组历史 / 1,062,880 次查询实测 0 差异。
"""

from dataclasses import dataclass, field
from typing import Optional

# R035 元数据保留键（与 checker.R035CrossTableFieldType.CROSS_KEY 一致；
# 此处为常量副本，避免 engine→rules 反向依赖）
R035_CROSS_KEY = "__r035_cross_table_columns__"


@dataclass
class _ColumnSummary:
    """单个列名的有界见证槽位（DETAIL §4.3）。

    只持有标量字段（table_name/type/raw_type/statement_index/column_index），
    绝不持有 ParsedSQL、AST 或完整 SQL 文本引用，保证 O(U) 空间。
    """
    anchor: Optional[dict] = None                 # 最早有效引用，固定不替换
    outside_anchor_table: list = field(default_factory=list)   # 见下
    outside_anchor_type: list = field(default_factory=list)    # 见下

    def add(self, ref: dict) -> None:
        """加入一条历史引用（按语句/列原始顺序调用）。

        - outside_anchor_table：table != anchor.table 的历史中，最早两个**不同规范
          type**的首条引用（满 2 后不替换，按既定属性去重，不是"最后两条"）。
        - outside_anchor_type：type != anchor.type 的历史中，最早两个**不同表名**的
          首条引用。
        """
        if self.anchor is None:
            self.anchor = ref
            return
        anchor = self.anchor
        a_table = anchor["table_name"]
        a_type = anchor["type"]
        # outside_anchor_table：不同表，保留最早两个不同 type 的首条
        if (ref["table_name"] != a_table and len(self.outside_anchor_table) < 2
                and all(item["type"] != ref["type"] for item in self.outside_anchor_table)):
            self.outside_anchor_table.append(ref)
        # outside_anchor_type：不同 type，保留最早两个不同 table 的首条
        if (ref["type"] != a_type and len(self.outside_anchor_type) < 2
                and all(item["table_name"] != ref["table_name"] for item in self.outside_anchor_type)):
            self.outside_anchor_type.append(ref)

    def refs(self) -> list:
        """对外投影：合并 anchor + outside_anchor_table + outside_anchor_type，
        按 (statement_index, column_index) 去重并排序，至多 5 条（实测最大 4）。"""
        groups = ([self.anchor] if self.anchor else [])
        groups += self.outside_anchor_table + self.outside_anchor_type
        seen = {}
        for r in groups:
            seen[(r["statement_index"], r["column_index"])] = r
        return [seen[k] for k in sorted(seen)]


class R035PriorIndex:
    """R035 批内跨表字段类型的请求局部有界见证索引。

    生命周期为单次 audit 请求；请求结束即释放，不做跨请求缓存。
    """

    def __init__(self) -> None:
        self._by_col: dict[str, _ColumnSummary] = {}

    def project_for_column(self, col_name: str) -> list:
        """返回某列名（lower）的当前有界见证引用列表（按历史顺序）。

        供 R035.check 消费：与旧的"完整历史快照"在首个冲突来源上完全等价。
        无历史返回空列表（首个出现的列名不报）。
        """
        s = self._by_col.get(col_name.lower())
        return s.refs() if s is not None else []

    def project_for_columns(self, columns: list) -> dict:
        """为当前语句的列集合构造 meta 投影：{列名lower: [见证引用]}。

        只投影当前语句涉及的列名（R035.check 只读这些键），不为全历史建全量字典。
        """
        proj: dict[str, list] = {}
        for col in columns:
            nm = (col.get("name") or "").lower()
            if nm:
                proj[nm] = self.project_for_column(nm)
        return proj

    def add_columns(self, table_name: str, columns: list, statement_index: int) -> None:
        """把一条成功 CREATE TABLE 的列追加进历史（必须在审核之后调用）。

        columns 元素须含 name/type/raw_type（沿用 parser 产出结构）。
        """
        for col_index, col in enumerate(columns):
            nm = (col.get("name") or "").lower()
            if not nm:
                continue
            ctype = col.get("type") or ""
            ref = {
                "table_name": table_name,
                "type": ctype,
                "raw_type": col.get("raw_type") or ctype,
                "statement_index": statement_index,
                "column_index": col_index,
            }
            self._by_col.setdefault(nm, _ColumnSummary()).add(ref)
