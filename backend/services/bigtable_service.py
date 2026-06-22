"""
TDSQL SQL审核工具 - 大表治理服务 (V1.0)
"""
import json
import logging
from datetime import datetime
from typing import Optional

from backend.engine.bigtable_engine import BigTableEngine, BigTableClassifier, PartitionAdvisor
from backend.models import BigTableInfo, TableClassification
from backend.services.database import _get_connection, ensure_db

logger = logging.getLogger("tdsql.bigtable")


class BigTableService:
    """大表治理服务"""

    def __init__(self):
        self.engine = BigTableEngine()

    def save_inventory(self, connection_id: str, tables_info: list[dict]) -> dict:
        """保存大表盘点结果"""
        ensure_db()
        big_tables = self.engine.scan_big_tables(tables_info)
        conn = _get_connection()
        try:
            now = datetime.now().strftime("%Y-%m-%d")
            for bt in big_tables:
                conn.execute("""
                    INSERT OR REPLACE INTO bigtable_inventory
                    (connection_id, schema_name, table_name, size_gb, size_mb, rows_count,
                     level, is_partitioned, partition_count, has_global_index, shard_key, inspection_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    connection_id, bt.schema, bt.table, bt.size_gb,
                    bt.size_gb * 1024, bt.rows, bt.level,
                    1 if bt.is_partitioned else 0, bt.partition_count,
                    0, bt.shard_key, now,
                ))
            conn.commit()
        finally:
            conn.close()
        return self.engine.get_governance_report(big_tables)

    def get_inventory(self, connection_id: str, level: str = "") -> list[dict]:
        """获取大表清单"""
        ensure_db()
        conn = _get_connection()
        try:
            if level:
                rows = conn.execute(
                    "SELECT * FROM bigtable_inventory WHERE connection_id = ? AND level = ? ORDER BY size_gb DESC",
                    (connection_id, level)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bigtable_inventory WHERE connection_id = ? ORDER BY size_gb DESC",
                    (connection_id,)
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def classify_table(self, table_name: str) -> TableClassification:
        """分类表类型"""
        advisor = PartitionAdvisor()
        return advisor.classify_table(table_name)

    def get_governance_report(self, connection_id: str) -> dict:
        """获取治理报告"""
        inventory = self.get_inventory(connection_id)
        big_tables = [BigTableInfo(
            schema=t.get("schema_name", ""), table=t.get("table_name", ""),
            size_gb=t.get("size_gb", 0), rows=t.get("rows_count", 0),
            level=t.get("level", ""), level_label=BigTableClassifier.LEVEL_LABELS.get(t.get("level", ""), ""),
            is_partitioned=bool(t.get("is_partitioned", 0)),
            partition_count=t.get("partition_count", 0),
            shard_key=t.get("shard_key", ""),
        ) for t in inventory]
        return self.engine.get_governance_report(big_tables)

    def save_classification(self, connection_id: str, schema: str, table: str,
                            table_type: str, retention_days: int = 0) -> bool:
        """保存表分类"""
        ensure_db()
        advisor = PartitionAdvisor()
        type_info = advisor.TABLE_TYPES.get(table_type, {})
        conn = _get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO bigtable_classification
                (connection_id, schema_name, table_name, table_type, table_type_label,
                 retention_days, partition_key, partition_granularity, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                connection_id, schema, table, table_type,
                type_info.get("label", ""), retention_days or type_info.get("retention_days", 0),
                type_info.get("partition_key", ""), type_info.get("granularity", ""),
                datetime.now().isoformat(),
            ))
            conn.commit()
            return True
        finally:
            conn.close()
