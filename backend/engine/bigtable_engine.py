"""
TDSQL SQL审核工具 - 大表治理引擎 (V1.0)

实现大表三级分类(L1/L2/L3)、分区水位监控、变更管控等治理功能。
"""
import re
from datetime import datetime
from typing import Optional

from backend.models import (
    BigTableInfo, PartitionWatermarkInfo, PartitionAdvice, TableClassification,
)


class BigTableClassifier:
    """大表分类器 — 按银行严格口径分三级"""

    # 三级阈值（银行口径）
    L1_SIZE_GB = 50          # L1: 50GB以上
    L1_ROWS = 5000_0000      # L1: 5000万行以上
    L2_SIZE_GB = 200         # L2: 200GB以上
    L2_ROWS = 2_0000_0000    # L2: 2亿行以上
    L3_SIZE_GB = 500         # L3: 500GB以上
    L3_ROWS = 5_0000_0000    # L3: 5亿行以上

    LEVEL_LABELS = {
        "L1": "关注级",
        "L2": "管控级",
        "L3": "严控级",
    }

    def classify(self, size_gb: float, rows: int) -> tuple[str, str]:
        """
        根据表大小和行数分类大表级别。

        Returns:
            (level, level_label)
        """
        if size_gb >= self.L3_SIZE_GB or rows >= self.L3_ROWS:
            return "L3", self.LEVEL_LABELS["L3"]
        elif size_gb >= self.L2_SIZE_GB or rows >= self.L2_ROWS:
            return "L2", self.LEVEL_LABELS["L2"]
        elif size_gb >= self.L1_SIZE_GB or rows >= self.L1_ROWS:
            return "L1", self.LEVEL_LABELS["L1"]
        return "", ""

    def is_big_table(self, size_gb: float, rows: int) -> bool:
        """判断是否为大表"""
        level, _ = self.classify(size_gb, rows)
        return bool(level)


class PartitionMonitor:
    """分区水位监控器"""

    # 分区水位阈值
    WARNING_PERCENT = 70     # 70%告警
    CRITICAL_PERCENT = 85    # 85%严重
    MAX_PARTITIONS = 100     # 最大分区数

    def check_watermark(self, partition_count: int, max_partitions: int = 100) -> PartitionWatermarkInfo:
        """检查分区水位"""
        if max_partitions <= 0:
            max_partitions = self.MAX_PARTITIONS
        percent = (partition_count / max_partitions * 100) if max_partitions > 0 else 0

        if percent >= self.CRITICAL_PERCENT:
            status = "CRITICAL"
        elif percent >= self.WARNING_PERCENT:
            status = "WARNING"
        else:
            status = "NORMAL"

        return PartitionWatermarkInfo(
            partition_count=partition_count,
            status=status,
            watermark_percent=round(percent, 1),
        )

    def get_add_partition_advice(self, table: str, current_count: int,
                                  max_partitions: int = 100) -> Optional[PartitionAdvice]:
        """获取添加分区建议"""
        info = self.check_watermark(current_count, max_partitions)
        if info.status == "NORMAL":
            return None

        # 生成添加分区DDL示例
        ddl = f"-- 表 {table} 分区水位 {info.watermark_percent}%，建议添加新分区\n"
        ddl += f"ALTER TABLE {table} ADD PARTITION (PARTITION p{current_count + 1} VALUES LESS THAN (TO_DAYS('2026-07-01')));"

        return PartitionAdvice(
            table=table,
            conditions=[{"current_count": current_count, "max": max_partitions, "percent": info.watermark_percent}],
            ddl_example=ddl,
        )


class PartitionAdvisor:
    """分区改造建议器"""

    # 6类业务表保留期管理
    TABLE_TYPES = {
        "transaction_log": {"label": "交易流水表", "retention_days": 365, "partition_key": "create_time", "granularity": "month"},
        "operation_log": {"label": "操作日志表", "retention_days": 180, "partition_key": "create_time", "granularity": "month"},
        "audit_log": {"label": "审计日志表", "retention_days": 1095, "partition_key": "create_time", "granularity": "quarter"},
        "temp_data": {"label": "临时数据表", "retention_days": 30, "partition_key": "create_time", "granularity": "day"},
        "business_data": {"label": "业务数据表", "retention_days": 0, "partition_key": "", "granularity": ""},
        "config_data": {"label": "配置数据表", "retention_days": 0, "partition_key": "", "granularity": ""},
    }

    def classify_table(self, table_name: str, description: str = "") -> TableClassification:
        """根据表名自动分类业务类型"""
        name_lower = table_name.lower()

        table_type = "business_data"
        for type_key, type_info in self.TABLE_TYPES.items():
            if type_key.replace("_", "") in name_lower.replace("_", ""):
                table_type = type_key
                break
            # 关键词匹配
            keywords = {
                "transaction_log": ["trans", "trade", "order", "payment"],
                "operation_log": ["oper", "action", "event"],
                "audit_log": ["audit", "trace"],
                "temp_data": ["temp", "tmp", "cache"],
                "config_data": ["config", "setting", "dict"],
            }
            for kw in keywords.get(type_key, []):
                if kw in name_lower:
                    table_type = type_key
                    break

        type_info = self.TABLE_TYPES[table_type]
        return TableClassification(
            connection_id="",
            schema="",
            table=table_name,
            table_type=table_type,
            table_type_label=type_info["label"],
            retention_days=type_info["retention_days"],
            partition_key=type_info["partition_key"],
            partition_granularity=type_info["granularity"],
        )

    def suggest_partition(self, table: str, size_gb: float, rows: int,
                          table_type: str = "") -> Optional[PartitionAdvice]:
        """为大表生成分区改造建议"""
        classifier = BigTableClassifier()
        level, _ = classifier.classify(size_gb, rows)
        if not level:
            return None

        classification = self.classify_table(table)
        if table_type:
            classification.table_type = table_type
            type_info = self.TABLE_TYPES.get(table_type, {})
            classification.partition_key = type_info.get("partition_key", "create_time")
            classification.partition_granularity = type_info.get("granularity", "month")

        if not classification.partition_key:
            classification.partition_key = "create_time"
            classification.partition_granularity = "month"

        # 生成分区DDL
        granularity = classification.partition_granularity or "month"
        ddl = f"-- 表 {table} ({level}级大表，{size_gb}GB/{rows}行) 分区改造建议\n"
        ddl += f"ALTER TABLE {table}\nPARTITION BY RANGE (TO_DAYS({classification.partition_key})) (\n"
        ddl += f"    PARTITION p202601 VALUES LESS THAN (TO_DAYS('2026-02-01')),\n"
        ddl += f"    PARTITION p202602 VALUES LESS THAN (TO_DAYS('2026-03-01')),\n"
        ddl += f"    PARTITION p202603 VALUES LESS THAN (TO_DAYS('2026-04-01')),\n"
        ddl += f"    PARTITION p_future VALUES LESS THAN MAXVALUE\n"
        ddl += f");"

        conditions = [
            {"level": level, "size_gb": size_gb, "rows": rows},
            {"retention_days": classification.retention_days, "granularity": granularity},
        ]

        return PartitionAdvice(
            table=table,
            conditions=conditions,
            ddl_example=ddl,
        )


class BigTableEngine:
    """大表治理引擎 — 整合分类、监控、建议"""

    def __init__(self):
        self.classifier = BigTableClassifier()
        self.partition_monitor = PartitionMonitor()
        self.partition_advisor = PartitionAdvisor()

    def scan_big_tables(self, tables_info: list[dict]) -> list[BigTableInfo]:
        """
        扫描表清单，识别大表并分类。

        Args:
            tables_info: [{"schema": "", "table": "", "size_gb": 0, "rows": 0, ...}]

        Returns:
            大表信息列表
        """
        results = []
        for info in tables_info:
            size_gb = float(info.get("size_gb", 0) or 0)
            rows = int(info.get("rows", 0) or 0)
            level, level_label = self.classifier.classify(size_gb, rows)
            if not level:
                continue

            big_table = BigTableInfo(
                schema=info.get("schema", ""),
                table=info.get("table", ""),
                size_gb=size_gb,
                rows=rows,
                level=level,
                level_label=level_label,
                is_partitioned=info.get("is_partitioned", False),
                partition_count=info.get("partition_count", 0),
                shard_key=info.get("shard_key", ""),
            )
            results.append(big_table)
        return results

    def get_governance_report(self, big_tables: list[BigTableInfo]) -> dict:
        """生成大表治理报告"""
        report = {
            "total_big_tables": len(big_tables),
            "by_level": {"L1": 0, "L2": 0, "L3": 0},
            "unpartitioned": [],
            "partition_advice": [],
            "watermark_alerts": [],
        }

        for bt in big_tables:
            report["by_level"][bt.level] = report["by_level"].get(bt.level, 0) + 1

            if not bt.is_partitioned:
                advice = self.partition_advisor.suggest_partition(
                    bt.table, bt.size_gb, bt.rows
                )
                if advice:
                    report["partition_advice"].append(advice.model_dump())
                report["unpartitioned"].append({"table": bt.table, "level": bt.level, "size_gb": bt.size_gb})
            else:
                wm = self.partition_monitor.check_watermark(bt.partition_count)
                if wm.status != "NORMAL":
                    report["watermark_alerts"].append({
                        "table": bt.table,
                        "level": bt.level,
                        "partition_count": bt.partition_count,
                        "watermark_percent": wm.watermark_percent,
                        "status": wm.status,
                    })

        return report
