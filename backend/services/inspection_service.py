"""
TDSQL SQL审核工具 - 巡检服务 (V1.0)
"""
import json
import logging
from datetime import datetime
from typing import Optional

from backend.models import InspectionTaskInfo, InspectionResultInfo
from backend.services.database import _get_connection, ensure_db

logger = logging.getLogger("tdsql.inspection")


class InspectionService:
    """巡检服务"""

    def create_task(self, connection_id: str, inspection_type: str) -> int:
        """创建巡检任务"""
        ensure_db()
        conn = _get_connection()
        try:
            cursor = conn.execute("""
                INSERT INTO inspection_tasks (connection_id, inspection_type, status)
                VALUES (?, ?, 'pending')
            """, (connection_id, inspection_type))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_task_status(self, task_id: int, status: str, error_message: str = "",
                           report_path: str = ""):
        """更新任务状态"""
        ensure_db()
        conn = _get_connection()
        try:
            now = datetime.now().isoformat()
            if status == "running":
                conn.execute("UPDATE inspection_tasks SET status = ?, started_at = ? WHERE id = ?",
                             (status, now, task_id))
            elif status in ("completed", "failed"):
                conn.execute("UPDATE inspection_tasks SET status = ?, completed_at = ?, error_message = ?, report_path = ? WHERE id = ?",
                             (status, now, error_message, report_path, task_id))
            else:
                conn.execute("UPDATE inspection_tasks SET status = ? WHERE id = ?", (status, task_id))
            conn.commit()
        finally:
            conn.close()

    def save_result(self, task_id: int, result: InspectionResultInfo):
        """保存巡检结果"""
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute("""
                INSERT INTO inspection_results
                (task_id, category, severity, schema_name, table_name, metric_name, metric_value, threshold, message, suggestion)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task_id, result.category, result.severity,
                result.schema_name, result.table_name,
                result.metric_name, result.metric_value, result.threshold,
                result.message, result.suggestion,
            ))
            conn.commit()
        finally:
            conn.close()

    def get_task(self, task_id: int) -> Optional[dict]:
        """获取巡检任务"""
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute("SELECT * FROM inspection_tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                return None
            task = dict(row)
            results = conn.execute("SELECT * FROM inspection_results WHERE task_id = ?", (task_id,)).fetchall()
            task["results"] = [dict(r) for r in results]
            return task
        finally:
            conn.close()

    def list_tasks(self, connection_id: str = "", limit: int = 20) -> list[dict]:
        """列出巡检任务"""
        ensure_db()
        conn = _get_connection()
        try:
            if connection_id:
                rows = conn.execute(
                    "SELECT * FROM inspection_tasks WHERE connection_id = ? ORDER BY created_at DESC LIMIT ?",
                    (connection_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM inspection_tasks ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
