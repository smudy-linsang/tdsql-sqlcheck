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
from unittest.mock import patch

from backend.main import app
from backend.services.connection_errors import (
    AuthenticationFailedError, ConnectionRefusedError_, DatabaseNotFoundError,
    translate_db_error,
)
from backend.services.database import _get_connection, ensure_db

client = TestClient(app)

OFFLINE_CONN_ID = "o19_offline_instance"


class TestTranslateDbError:
    def test_connection_refused(self):
        import pymysql
        exc = pymysql.err.OperationalError(
            2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061])")
        assert isinstance(translate_db_error(exc), ConnectionRefusedError_)

    def test_access_denied(self):
        import pymysql
        exc = pymysql.err.OperationalError(1045, "Access denied for user 'root'@'localhost'")
        assert isinstance(translate_db_error(exc), AuthenticationFailedError)

    def test_unknown_database(self):
        import pymysql
        exc = pymysql.err.OperationalError(1049, "Unknown database 'no_such_db'")
        assert isinstance(translate_db_error(exc), DatabaseNotFoundError)

    def test_unknown_exception_maps_to_none(self):
        """v1.6.2.2-UAT-O-24：未知程序异常不得被翻译成连接失败，必须返回 None"""
        assert translate_db_error(RuntimeError("synthetic programming defect")) is None
        assert translate_db_error(AttributeError("no attribute x")) is None
        assert translate_db_error(TypeError("bad type")) is None

    def test_errno_driven_mapping(self):
        """errno 优先：仅从可信驱动异常链提取（v1.6.2.2-UAT-O-25）"""
        import pymysql
        assert isinstance(
            translate_db_error(pymysql.err.OperationalError(2003, "Can't connect")),
            ConnectionRefusedError_)
        assert isinstance(
            translate_db_error(pymysql.err.OperationalError(1045, "Access denied")),
            AuthenticationFailedError)
        assert isinstance(
            translate_db_error(pymysql.err.OperationalError(1049, "Unknown database")),
            DatabaseNotFoundError)
        # 异常链中的驱动异常同样可识别
        outer = RuntimeError("wrapper")
        outer.__cause__ = pymysql.err.OperationalError(2003, "Can't connect")
        assert isinstance(translate_db_error(outer), ConnectionRefusedError_)
        # 普通 Exception 携带整数 args 不得被误认为 errno
        assert translate_db_error(Exception(2003, "fake")) is None


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


class TestUnknownErrorStays500:
    """v1.6.2.2-UAT-O-24/O-25：未知程序异常不得伪装成 422 连接失败"""

    @pytest.mark.parametrize("exc", [
        RuntimeError("synthetic programming defect"),
        AttributeError("synthetic attribute error"),
        TypeError("synthetic type error"),
        # v1.6.2.2-UAT-O-25：消息含连接短语的程序异常同样是程序缺陷，必须 500
        RuntimeError("can't connect to internal cache"),
        AttributeError("connection refused while reading object"),
        TypeError("timed out during pickle decode"),
        RuntimeError("access denied to in-memory registry"),
        RuntimeError("unknown database handle in context"),
    ])
    def test_program_defect_returns_500_with_request_id(self, exc):
        # 直调转换器：程序异常一律不得映射
        assert translate_db_error(exc) is None
        # API 层：必须返回 500 + X-Request-ID，不得返回 422
        with patch("backend.services.connection_registry.registry.get_saved",
                   side_effect=exc):
            resp = client.post("/api/v1/daily-inspect/run",
                               json={"connection_id": OFFLINE_CONN_ID,
                                     "inspect_date": "2026-08-28"})
        assert resp.status_code == 500, \
            f"{type(exc).__name__} 不得被包装成 422，实际 {resp.status_code}"
        assert "X-Request-ID" in resp.headers, "500 响应必须携带请求ID便于定位"
class TestOSErrorFamilyNotTrusted:
    """v1.6.2.2-UAT-O-28：OSError 全家族不得凭消息伪装成数据库连接错误

    文件系统/证书/密钥/缓存/序列化错误即使消息含 access denied/unknown database
    也必须返回 None（API 层 500）。类型 × 短语 × errno × 直调矩阵。
    """

    @pytest.mark.parametrize("exc", [
        PermissionError("access denied reading encryption key"),
        PermissionError("access denied writing audit log"),
        FileNotFoundError("unknown database catalog file"),
        FileNotFoundError("can't connect to keystore file"),
        IsADirectoryError("unknown database directory"),
        NotADirectoryError("connection refused by path component"),
        OSError("access denied on shared memory segment"),
        OSError("unknown database file handle"),
        OSError(13, "Permission denied reading key file"),          # EACCES
        OSError(2, "No such file or directory: 'unknown database'"), # ENOENT
        BlockingIOError("can't connect while buffer full"),
    ])
    def test_oserror_family_never_maps(self, exc):
        assert translate_db_error(exc) is None, \
            f"{type(exc).__name__} 携带连接短语也不得被映射: {exc}"

    @pytest.mark.parametrize("exc", [
        PermissionError("access denied reading encryption key"),
        FileNotFoundError("unknown database catalog file"),
    ])
    def test_oserror_family_api_returns_500(self, exc):
        """API 层：文件系统/权限类异常必须 500，不得伪装 422"""
        with patch("backend.services.connection_registry.registry.get_saved",
                   side_effect=exc):
            resp = client.post("/api/v1/daily-inspect/run",
                               json={"connection_id": OFFLINE_CONN_ID,
                                     "inspect_date": "2026-08-28"})
        assert resp.status_code == 500, \
            f"{type(exc).__name__} 必须 500，实际 {resp.status_code}"
        assert "X-Request-ID" in resp.headers
        assert "实例连接失败" not in resp.text
        assert "认证失败" not in resp.text
        assert "数据库不存在" not in resp.text

    @pytest.mark.parametrize("exc", [
        ConnectionRefusedError("connection refused"),
        ConnectionResetError("connection reset by peer"),
        ConnectionAbortedError("connection aborted"),
        TimeoutError("timed out"),
        OSError(10061, "No connection could be made because the target machine actively refused it"),
        OSError(10060, "connection timed out"),
    ])
    def test_real_network_errors_still_map(self, exc):
        """连接异常族（精确类型或明确网络 errno）仍应正确映射连接拒绝 422"""
        assert isinstance(translate_db_error(exc), ConnectionRefusedError_)
