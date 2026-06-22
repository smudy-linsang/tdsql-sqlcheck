"""
TDSQL SQL审核工具 - 监控告警服务 (V1.0)
"""
import json
import logging
from datetime import datetime
from typing import Optional

from backend.models import AlertInfo, AlertRuleConfig
from backend.services.database import _get_connection, ensure_db

logger = logging.getLogger("tdsql.monitor")


class MonitorService:
    """监控告警服务"""

    def get_alert_rules(self) -> list[dict]:
        """获取告警规则"""
        ensure_db()
        conn = _get_connection()
        try:
            rows = conn.execute("SELECT * FROM alert_rules ORDER BY metric_name").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def set_alert_rule(self, rule: AlertRuleConfig) -> bool:
        """设置告警规则"""
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO alert_rules
                (metric_name, warning_threshold, urgent_threshold, check_interval_sec,
                 notify_webhook, notify_email, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rule.metric_name, rule.warning_threshold, rule.urgent_threshold,
                rule.check_interval_sec, rule.notify_webhook, rule.notify_email,
                1 if rule.enabled else 0, datetime.now().isoformat(),
            ))
            conn.commit()
            return True
        finally:
            conn.close()

    def create_alert(self, alert: AlertInfo) -> int:
        """创建告警"""
        ensure_db()
        conn = _get_connection()
        try:
            cursor = conn.execute("""
                INSERT INTO alerts
                (connection_id, metric_name, metric_value, level, threshold, message, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')
            """, (
                alert.connection_id, alert.metric, alert.value,
                alert.level, 0, alert.message,
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_active_alerts(self, connection_id: str = "") -> list[dict]:
        """获取活跃告警"""
        ensure_db()
        conn = _get_connection()
        try:
            if connection_id:
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE status = 'active' AND connection_id = ? ORDER BY created_at DESC",
                    (connection_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE status = 'active' ORDER BY created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def acknowledge_alert(self, alert_id: int, acknowledged_by: str = "system") -> bool:
        """确认告警"""
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE alerts SET status = 'acknowledged', acknowledged_by = ?, acknowledged_at = ? WHERE id = ?",
                (acknowledged_by, datetime.now().isoformat(), alert_id)
            )
            conn.commit()
            return conn.total_changes > 0
        finally:
            conn.close()

    def evaluate_metric(self, connection_id: str, metric_name: str, value: float) -> Optional[AlertInfo]:
        """评估指标是否触发告警"""
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM alert_rules WHERE metric_name = ? AND enabled = 1", (metric_name,)
            ).fetchone()
            if not row:
                return None
            if value >= row["urgent_threshold"]:
                level = "CRITICAL"
            elif value >= row["warning_threshold"]:
                level = "WARNING"
            else:
                return None
            return AlertInfo(
                metric=metric_name, value=value, level=level,
                connection_id=connection_id,
                message=f"{metric_name}={value} 超过{level}阈值({row[f'{level.lower()}_threshold'] if level == 'WARNING' else row['urgent_threshold']})",
            )
        finally:
            conn.close()
