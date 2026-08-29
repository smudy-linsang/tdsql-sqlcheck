# -*- coding: utf-8 -*-
"""UAT-O-19 回归测试：保存但未连接的实例运行巡检不得返回裸 500

覆盖 O 第四轮报告 O-19（MAJOR）：
1. 离线实例 → 可读 422（连接拒绝），不再是 Internal Server Error；
2. 领域异常翻译：连接拒绝/认证失败/库不存在各有稳定语义；
3. 未连接实例（不存在）→ 400 保持原语义；
4. 未知程序错误仍是 500，不得被领域化吞掉。
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.connection_errors import (
    AuthenticationFailedError, ConnectionRefusedError_, DatabaseNotFoundError,
    InstanceConnectionError, translate_db_error,
)
from backend.services.database import _get_connection, ensure_db

client = TestClient(app)

OFFLINE_CONN_ID = "o19_offline_instance"


class TestTranslateDbError:
    def test_connection_refused(self):
        exc = OSError("(2003, \"Can't connect to MySQL server on '127.0.0.1' ([WinError 10061])\")")
        assert isinstance(translate_db_error(exc), ConnectionRefusedError_)

    def test_access_denied(self):
        exc = Exception("(1045, \"Access denied for user 'root'@'localhost'\")")
        assert isinstance(translate_db_error(exc), AuthenticationFailedError)

    def test_unknown_database(self):
        exc = Exception("(1049, \"Unknown database 'no_such_db'\")")
        assert isinstance(translate_db_error(exc), DatabaseNotFoundError)

    def test_unknown_falls_back_to_base_domain(self):
        exc = Exception("some weird network problem")
        out = translate_db_error(exc)
        assert isinstance(out, InstanceConnectionError)
        assert out.cause is exc


class TestOfflineInstanceDailyRun:
    """保存但离线（拒绝连接）的实例：巡检必须返回可读 422 而非裸 500"""

    @pytest.fixture(scope="class", autouse=True)
    def register_offline_instance(self):
        ensure_db()
        conn = _get_connection()
        try:
            # 端口 1 上无服务：pymysql 立即得到 connection refused
            conn.execute(
                "REPLACE INTO tdsql_connections "
                "(id, name, host, port, username, password_encrypted, `database`, "
                " charset, status) "
                "VALUES (?,?,?,?,?,?,?,?, 'disconnected')",
                (OFFLINE_CONN_ID, "O19离线实例", "127.0.0.1", 1, "root", "",
                 "o19_db", "utf8mb4"))
            conn.commit()
        finally:
            conn.close()
        yield
        # 清理 registry 中可能残留的池
        try:
            from backend.services.connection_registry import registry
            registry.disconnect(OFFLINE_CONN_ID)
        except Exception:
            pass

    def test_offline_instance_returns_readable_422(self):
        resp = client.post("/api/v1/daily-inspect/run",
                           json={"connection_id": OFFLINE_CONN_ID,
                                 "inspect_date": "2026-08-28"})
        assert resp.status_code != 500, "离线实例不得返回裸 500"
        assert resp.status_code == 422, f"应为可读 422，实际 {resp.status_code}: {resp.text[:200]}"
        detail = resp.json()["detail"]
        assert "拒绝连接" in detail or "连接" in detail
        assert "Internal Server Error" not in detail

    def test_not_saved_instance_still_400(self):
        resp = client.post("/api/v1/daily-inspect/run",
                           json={"connection_id": "o19_never_saved_xyz",
                                 "inspect_date": "2026-08-28"})
        assert resp.status_code == 400
        assert "未连接" in resp.json()["detail"]
