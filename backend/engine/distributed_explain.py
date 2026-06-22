"""
TDSQL SQL审核工具 - 分布式EXPLAIN分析引擎 (V1.0)

分析TDSQL分布式EXPLAIN输出，判断是否命中单SET、是否广播。
"""
import re
from typing import Optional

from backend.models import DistributedExplainReport


class DistributedExplainAnalyzer:
    """分布式EXPLAIN分析器"""

    def analyze(self, explain_output: list[dict], sql: str = "",
                table_metadata: Optional[dict] = None) -> DistributedExplainReport:
        """
        分析分布式EXPLAIN输出。

        TDSQL分布式EXPLAIN输出格式：
        - hit_set: 命中的SET列表（如["set1"]表示单SET，["set1","set2",...]表示多SET）
        - shard_key_value: 分片键值
        - scan_type: FULL_SCAN / INDEX_SCAN / SINGLE_SET

        Args:
            explain_output: 分布式EXPLAIN输出行列表
            sql: 原始SQL
            table_metadata: 表元数据

        Returns:
            DistributedExplainReport 分析报告
        """
        report = DistributedExplainReport()
        warnings = []

        # 分析每行EXPLAIN输出
        hit_sets = set()
        for row in explain_output:
            if not isinstance(row, dict):
                continue
            # TDSQL分布式EXPLAIN字段
            hit_set = row.get("hit_set", row.get("set", ""))
            if hit_set:
                hit_sets.add(str(hit_set))

            scan_type = str(row.get("scan_type", row.get("type", ""))).upper()
            if scan_type in ("FULL_SCAN", "ALL"):
                warnings.append({
                    "level": "ERROR",
                    "message": f"SET {hit_set} 执行全表扫描",
                    "detail": row,
                })

            # 检查是否广播
            is_broadcast = row.get("is_broadcast", False)
            if is_broadcast:
                warnings.append({
                    "level": "ERROR",
                    "message": f"检测到广播操作，SQL将发送到所有SET执行",
                    "detail": row,
                })

        # 判断是否命中单SET
        if len(hit_sets) == 1:
            report.shard_key_in_where = True
        elif len(hit_sets) > 1:
            report.shard_key_in_where = False
            warnings.append({
                "level": "WARNING",
                "message": f"SQL命中多SET({', '.join(sorted(hit_sets))})，未命中单SET，可能导致跨SET广播",
                "detail": {"hit_sets": list(hit_sets)},
            })

        # 如果没有EXPLAIN输出，基于SQL文本和元数据做静态分析
        if not explain_output and sql and table_metadata:
            static_warnings = self._static_analysis(sql, table_metadata)
            warnings.extend(static_warnings)
            if not warnings:
                report.shard_key_in_where = True  # 无问题默认假设命中

        report.warnings = warnings
        return report

    def _static_analysis(self, sql: str, table_metadata: dict) -> list[dict]:
        """基于SQL文本和元数据的静态分析"""
        warnings = []
        sql_lower = sql.lower()

        # 提取表名
        tables = []
        for pattern in [r"\bfrom\s+([a-zA-Z_][a-zA-Z0-9_.]*)", r"\bjoin\s+([a-zA-Z_][a-zA-Z0-9_.]*)"]:
            for m in re.finditer(pattern, sql, re.IGNORECASE):
                t = m.group(1).strip("`\"")
                if t and t not in tables:
                    tables.append(t)

        has_where = " where " in sql_lower

        for table in tables:
            meta = table_metadata.get(table, {})
            shard_key = meta.get("shard_key", "")
            is_shard = meta.get("is_shard_table", False)

            if is_shard and shard_key:
                if not has_where:
                    warnings.append({
                        "level": "ERROR",
                        "message": f"分片表 {table} 查询无WHERE条件，将扫描所有SET",
                        "detail": {"table": table, "shard_key": shard_key},
                    })
                elif shard_key.lower() not in sql_lower:
                    warnings.append({
                        "level": "WARNING",
                        "message": f"分片表 {table} 的WHERE条件未包含分片键 '{shard_key}'，可能命中多SET",
                        "detail": {"table": table, "shard_key": shard_key},
                    })

        return warnings
