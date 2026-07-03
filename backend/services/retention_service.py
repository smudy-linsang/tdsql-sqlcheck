"""
TDSQL SQL审核工具 - 数据保留服务 (V2.0)

数据生命周期管理：按表配置保留天数，定期清理过期数据，
防止服务数百个库时本地存储无限膨胀。

- 保留策略存于 retention_policies 表（默认策略见 database._init_default_data）
- 调度器每日执行清理（仅leader副本），也可通过 API 手动触发
- 存储引擎为 MySQL/InnoDB，删除后空间由引擎复用（如需物理回缩可离线 OPTIMIZE TABLE）
"""
import logging
from typing import Optional

from backend.services.database import _get_connection, ensure_db, log_operation

logger = logging.getLogger("tdsql.retention")

# 允许配置保留策略的表及其时间字段
CLEANABLE_TABLES = {
    "slow_queries": "created_at",
    "audit_history": "created_at",
    "scan_tasks": "created_at",
    "alerts": "created_at",
    "operation_logs": "created_at",
    "gate_audit_logs": "created_at",
    "fingerprint_stats": "created_at",
}


class RetentionService:
    """数据保留策略管理与清理执行"""

    def get_policies(self) -> list[dict]:
        """获取全部保留策略"""
        ensure_db()
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM retention_policies ORDER BY table_name").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def set_policy(self, table_name: str, retention_days: int,
                   enabled: bool = True, operator: str = "") -> Optional[str]:
        """设置保留策略，返回错误信息或None"""
        if table_name not in CLEANABLE_TABLES:
            return f"不支持配置保留策略的表: {table_name}（可选: {', '.join(CLEANABLE_TABLES)}）"
        if retention_days < 7:
            return "保留天数不能少于7天（防止误配置导致数据丢失）"
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute("""
                INSERT INTO retention_policies(table_name, retention_days, enabled, updated_at)
                VALUES (?, ?, ?, NOW())
                ON DUPLICATE KEY UPDATE
                    retention_days=VALUES(retention_days),
                    enabled=VALUES(enabled),
                    updated_at=NOW()
            """, (table_name, retention_days, 1 if enabled else 0))
            conn.commit()
            log_operation(operator, "set_retention_policy", "retention",
                          table_name, f"days={retention_days} enabled={enabled}")
            return None
        finally:
            conn.close()

    def run_cleanup(self, operator: str = "system") -> dict:
        """
        执行一次数据清理。

        Returns:
            {table_name: deleted_count}
        """
        ensure_db()
        deleted = {}
        conn = _get_connection()
        try:
            policies = conn.execute(
                "SELECT * FROM retention_policies WHERE enabled = 1").fetchall()
            for p in policies:
                table = p["table_name"]
                days = p["retention_days"]
                time_col = CLEANABLE_TABLES.get(table)
                if not time_col:
                    continue
                try:
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE {time_col} < DATE_SUB(NOW(), INTERVAL ? DAY)",
                        (int(days),))
                    if cursor.rowcount > 0:
                        deleted[table] = cursor.rowcount
                except Exception as e:
                    logger.warning("清理表 %s 失败: %s", table, e)
            conn.commit()
        finally:
            conn.close()

        total = sum(deleted.values())
        if total > 0:
            logger.info("数据保留清理完成: %s (共%d条)", deleted, total)
            log_operation(operator, "retention_cleanup", "retention", "",
                          f"deleted={deleted}")
        return deleted


# 全局单例
retention_service = RetentionService()
