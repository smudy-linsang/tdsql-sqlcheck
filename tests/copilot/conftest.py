# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：Copilot 测试共享 fixture。

使用独立元数据库 tdsql_sqlcheck_test（与 tests/conftest.py 一致），
不新建库；Copilot 相关表在测试库中由 ensure_db + B组维护入口保证。
"""
import os

import pytest

# 与 tests/conftest.py 同库（tdsql_sqlcheck_test），复用其环境
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("DATA_MASKING_ENABLED", "false")
os.environ.setdefault("SQLCHECK_DB_HOST", "127.0.0.1")
os.environ.setdefault("SQLCHECK_DB_PORT", "13306")
os.environ.setdefault("SQLCHECK_DB_USER", "root")
os.environ.setdefault("SQLCHECK_DB_PASSWORD", "tdsql_test_2024")
os.environ.setdefault("SQLCHECK_DB_NAME", "tdsql_sqlcheck_test")


@pytest.fixture(scope="session")
def copilot_db(tmp_path_factory):
    """确保核心 + A组 + B组 schema 就绪；返回 DB 是否可用。"""
    # 会话级 keyring：preview/hmac 依赖
    import base64
    import json
    kdir = tmp_path_factory.mktemp("copilot_session")
    key = base64.b64encode(os.urandom(32)).decode()
    kf = kdir / "copilot-keyring.json"
    kf.write_text(json.dumps({"schema_version": 1, "active_kid": "key-sit",
                              "keys": {"key-sit": key}}), encoding="utf-8")
    from backend.services.copilot.crypto import reset_keyring_cache
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("COPILOT_KEYRING_FILE", str(kf))
            reset_keyring_cache()
            from backend.services.database import ensure_db, _get_connection
            ensure_db()
            conn = _get_connection()
            try:
                conn.execute("SELECT 1")
            finally:
                conn.close()
            # B组应用失败必须报错，不能把结构回归包装成“数据库不可用”而跳过。
            from backend.services.copilot import schema as schema_mod
            rc = schema_mod.apply_business_schema()
            assert rc == 0, f"B组 schema 应用失败 rc={rc}"
            yield True
    finally:
        reset_keyring_cache()


@pytest.fixture()
def client(copilot_db, monkeypatch):
    """API 测试需要真实认证（Copilot 按设计拒绝匿名/ AUTH_ENABLED=false 旁路）。

    同时钉住配置函数层（DB 开关优先于环境变量，仅设环境变量不够）。
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from backend import config as _cfg
    monkeypatch.setattr(_cfg, "auth_enabled", lambda: True)
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture()
def copilot_conn(copilot_db):
    from backend.services.database import _get_connection
    conn = _get_connection()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture()
def copilot_accepting(copilot_db):
    """把 Copilot 运行态置为可受理（部署闸 + DB 开关 + runner 心跳）。"""
    from backend.services.copilot.repository import RuntimeRepo
    from backend.services.database import _get_connection
    conn = _get_connection()
    try:
        settings = RuntimeRepo.settings(conn)
        settings["enabled"] = True
        rt = RuntimeRepo.get(conn) or {}
        RuntimeRepo.save_settings(conn, settings,
                                  int(rt.get("config_revision") or 1))
        conn.commit()  # 先提交再换连接改 accepting，避免同行锁自锁
    finally:
        conn.close()
    RuntimeRepo.set_accepting(True, "test-runner")
    import os
    os.environ["COPILOT_ENABLED"] = "true"
    yield
    os.environ.pop("COPILOT_ENABLED", None)
    conn = _get_connection()
    try:
        RuntimeRepo.set_accepting(False)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def keyring_file(tmp_path, monkeypatch):
    """临时 keyring 文件（32 字节 AES 密钥）。"""
    import base64
    import json
    key = base64.b64encode(os.urandom(32)).decode()
    p = tmp_path / "copilot-keyring.json"
    p.write_text(json.dumps({
        "schema_version": 1, "active_kid": "key-test",
        "keys": {"key-test": key}}), encoding="utf-8")
    monkeypatch.setenv("COPILOT_KEYRING_FILE", str(p))
    from backend.services.copilot.crypto import reset_keyring_cache
    reset_keyring_cache()
    yield str(p)
    reset_keyring_cache()


@pytest.fixture()
def endpoints_file(tmp_path, monkeypatch):
    """临时端点批准清单。"""
    import json
    p = tmp_path / "copilot-endpoints.json"
    p.write_text(json.dumps({
        "schema_version": 1,
        "endpoints": [{
            "endpoint_id": "ep-test", "scheme": "https",
            "canonical_host": "llm-gw.internal", "port": 443,
            "base_path": "/v1", "data_zone": "INTERNAL",
            "privacy_profile": "INTERNAL_REDACTED",
            "allows_schema_identifiers": False,
            "allowed_resolved_cidrs": ["10.0.0.0/8"], "tls_ca_ref": "internal",
        }]}), encoding="utf-8")
    monkeypatch.setenv("COPILOT_ENDPOINTS_FILE", str(p))
    from backend.services.copilot.policy import reset_policy_cache
    reset_policy_cache()
    yield str(p)
    reset_policy_cache()
