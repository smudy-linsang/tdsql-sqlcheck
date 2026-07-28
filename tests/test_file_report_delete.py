"""
文件审核报告批量删除测试
"""
import pytest
from fastapi import HTTPException
from backend.api.sql_audit import batch_delete_file_reports
from backend.services.database import _get_connection, ensure_db


class _FakeState:
    def __init__(self, role, username):
        self.role = role
        self.username = username


class _FakeClient:
    host = "127.0.0.1"


class _FakeRequest:
    def __init__(self, role="admin", username="admin"):
        self.state = _FakeState(role, username)
        self.client = _FakeClient()


@pytest.mark.asyncio
async def test_file_report_batch_delete_permissions():
    ensure_db()
    conn = _get_connection()
    conn.execute(
        """INSERT INTO audit_history (audit_type, source, total_sql, passed, failed, pass_rate, created_by)
           VALUES ('file', 'test_delete.sql', 1, 1, 0, 100.0, 'test_user')"""
    )
    conn.commit()
    report_id = conn.execute("SELECT LAST_INSERT_ID() AS id").fetchone()["id"]
    conn.close()

    # Developer user should be blocked with 403 Forbidden
    dev_req = _FakeRequest(role="developer", username="dev_user")
    with pytest.raises(HTTPException) as exc_info:
        await batch_delete_file_reports({"ids": [report_id]}, dev_req)
    assert exc_info.value.status_code == 403

    # Admin user can delete successfully
    admin_req = _FakeRequest(role="admin", username="admin_user")
    res = await batch_delete_file_reports({"ids": [report_id]}, admin_req)
    assert res["status"] == "SUCCESS"
    assert report_id in res["deleted_ids"]
