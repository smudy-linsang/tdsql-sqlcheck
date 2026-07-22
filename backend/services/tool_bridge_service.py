"""
ToolBridge 离线工具箱服务：负责管理离线诊断工具调度与历史执行追溯
"""
import json
import logging
import uuid
from datetime import datetime
from typing import Optional
from backend.services.database import _get_connection, ensure_db

logger = logging.getLogger("tdsql.tool_bridge")


class ToolBridgeService:
    def create_run_task(self, tool_name: str, connection_id: str, params: dict, operator: str) -> str:
        """创建工具箱调度任务并插入 tool_runs 表"""
        ensure_db()
        run_id = f"tr_{uuid.uuid4().hex[:12]}"
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO tool_runs (run_id, tool_name, target_connection, params_json, status, created_by)
                VALUES (%s, %s, %s, %s, 'RUNNING', %s)
            """, (run_id, tool_name, connection_id, json.dumps(params), operator))
            conn.commit()
            return run_id
        finally:
            conn.close()

    def update_run_status(self, run_id: str, status: str, error_msg: Optional[str] = None):
        """更新任务状态与结束时间"""
        ensure_db()
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE tool_runs
                SET status = %s, error_message = %s, finished_at = NOW()
                WHERE run_id = %s
            """, (status, error_message or "", run_id))
            conn.commit()
        finally:
            conn.close()

    def get_run_history(self, limit: int = 20) -> list[dict]:
        """获取工具运行历史"""
        ensure_db()
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM tool_runs ORDER BY created_at DESC LIMIT %s
            """, (limit,))
            return cursor.fetchall()
        finally:
            conn.close()


tool_bridge_service = ToolBridgeService()
