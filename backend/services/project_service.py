"""
TDSQL SQL审核工具 - 项目管理服务 (V1.0)
"""
import json
import logging
from datetime import datetime
from typing import Optional

from backend.models import Project, ProjectCreate
from backend.services.database import _get_connection, ensure_db, log_operation

logger = logging.getLogger("tdsql.project")

# MySQL中TEXT列不带默认值(NULL)，而Project模型的str字段不接受None，
# 行数据构造模型前统一将这些字段的NULL归一为空串
_TEXT_FIELDS_DEFAULT_EMPTY = (
    "tdsql_connection_id", "rule_set_id", "gate_rule_id",
    "gitlab_url", "description", "status",
)


def _row_to_project(row: dict) -> Project:
    data = dict(row)
    for f in _TEXT_FIELDS_DEFAULT_EMPTY:
        if data.get(f) is None:
            data[f] = ""
    return Project(**data)


class ProjectService:
    """项目管理服务"""

    def create_project(self, req: ProjectCreate) -> Project:
        """创建项目"""
        ensure_db()
        project_id = req.project_name.lower().replace(" ", "_")[:32]
        conn = _get_connection()
        try:
            conn.execute("""
                INSERT INTO projects (project_id, project_name, tdsql_connection_id, rule_set_id,
                    gate_rule_id, gitlab_project_id, gitlab_url, description, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')
            """, (
                project_id, req.project_name, req.tdsql_connection_id,
                req.rule_set_id, req.gate_rule_id, req.gitlab_project_id,
                req.gitlab_url, req.description,
            ))
            conn.commit()
            log_operation("system", "create_project", "project", project_id)
        finally:
            conn.close()
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> Optional[Project]:
        """获取项目"""
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
            if not row:
                return None
            return _row_to_project(row)
        finally:
            conn.close()

    def list_projects(self) -> list[Project]:
        """列出所有项目"""
        ensure_db()
        conn = _get_connection()
        try:
            rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
            return [_row_to_project(r) for r in rows]
        finally:
            conn.close()

    def delete_project(self, project_id: str) -> bool:
        """删除项目"""
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute("UPDATE projects SET status = 'inactive' WHERE project_id = ?", (project_id,))
            conn.commit()
            log_operation("system", "delete_project", "project", project_id)
            return conn.total_changes > 0
        finally:
            conn.close()
