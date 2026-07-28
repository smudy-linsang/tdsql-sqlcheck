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
        started_at = datetime.now()
        big_tables = self.engine.scan_big_tables(tables_info)
        conn = _get_connection()
        try:
            now = started_at.strftime("%Y-%m-%d")
            for bt in big_tables:
                conn.execute("""
                    REPLACE INTO bigtable_inventory
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

        # V1.3: 旁路生成对比快照。biz_ref_id 带时分秒，使同日多次盘点各成一份快照
        # （解决 D3：inventory 表以"天"为粒度 REPLACE 覆盖，当天无法保留两批次）
        self._create_snapshot(connection_id, now, started_at)

        return self.engine.get_governance_report(big_tables)

    @staticmethod
    def _create_snapshot(connection_id: str, inspection_date: str, started_at):
        """旁路生成大表治理快照，失败仅告警，不影响盘点主流程"""
        try:
            from backend.services.snapshot_extractors.bigtable import extract as bt_extract
            from backend.services import scan_snapshot_service as snap
            from backend.services.connection_registry import registry
            from backend.services.ruleset_service import ruleset_service as _rs_svc

            items, obj_total = bt_extract(connection_id, inspection_date)
            conn_name = ""
            try:
                conn_name = (registry.get_saved(connection_id) or {}).get("name", "")
            except Exception:
                pass
            finished = datetime.now()
            snap.safe_create_snapshot("bigtable", {
                "biz_ref_id": f"{connection_id}:{inspection_date}:{finished.strftime('%H%M%S')}",
                "connection_id": connection_id,
                "connection_name": conn_name,
                "db_name": "",
                "scan_label": f"大表盘点 {inspection_date} {finished.strftime('%H:%M:%S')}",
                "scan_started_at": started_at.isoformat(),
                "scan_finished_at": finished.isoformat(),
                "created_by": "",
                # V1.4：大表治理本身不走规则集，但报告需标注当时全局尺度（对比校验用）
                "rule_set_id": _rs_svc.get_active_rule_set_id(),
            }, items, obj_total)
        except Exception as e:
            logger.warning(f"生成大表治理快照失败: {e}")

    def get_inventory(self, connection_id: str, level: str = "",
                      inspection_date: str = "") -> list[dict]:
        """获取大表清单

        V1.3(D2): 增加 inspection_date 过滤。为空时自动取该实例最近一次盘点日期，
        避免多次盘点后返回跨日期混合数据（同一张表出现多行、清单虚高）。
        """
        ensure_db()
        conn = _get_connection()
        try:
            if not inspection_date:
                row = conn.execute(
                    "SELECT MAX(inspection_date) AS d FROM bigtable_inventory WHERE connection_id = ?",
                    (connection_id,)
                ).fetchone()
                inspection_date = (dict(row).get("d") if row else "") or ""
                if not inspection_date:
                    return []   # 该实例从未盘点
            sql = ("SELECT * FROM bigtable_inventory "
                   "WHERE connection_id = ? AND inspection_date = ?")
            args = [connection_id, inspection_date]
            if level:
                sql += " AND level = ?"
                args.append(level)
            sql += " ORDER BY size_gb DESC"
            rows = conn.execute(sql, args).fetchall()
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
                REPLACE INTO bigtable_classification
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
