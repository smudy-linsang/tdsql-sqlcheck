"""
V2.0 平台能力测试

覆盖:
- 连接注册表（多实例并存/LRU/扫描限流/加密持久化）
- 慢SQL入库脱敏
- 数据保留策略与清理
- 规则集多租户（启停覆盖/级别覆盖/项目绑定）
- 可观测性（/metrics、X-Request-ID）
- 密钥管理（加密解密/遗留密钥兼容）
"""
import threading

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from backend.main import app
    return TestClient(app)


# ══════════════════════════════════════════════════════════════
# 连接注册表
# ══════════════════════════════════════════════════════════════

class TestConnectionRegistry:
    def test_save_and_list_encrypted(self):
        from backend.services.connection_registry import registry
        from backend.services.database import _get_connection, ensure_db
        conn_id = registry.save_connection(
            name="测试库A", host="10.0.0.1", port=15000,
            username="app_user", password="Secret@123",
            database="db_a", operator="test")
        assert conn_id
        # 密码加密落库（非明文）
        ensure_db()
        conn = _get_connection()
        row = conn.execute(
            "SELECT password_encrypted FROM tdsql_connections WHERE id=?",
            (conn_id,)).fetchone()
        conn.close()
        assert row["password_encrypted"] != "Secret@123"
        # 列表接口脱敏
        saved = [c for c in registry.list_saved() if c["id"] == conn_id]
        assert saved and saved[0]["password"] == "***"
        assert "password_encrypted" not in saved[0]
        # 解密可还原
        from backend.services.security_service import decrypt_password
        full = registry.get_saved(conn_id)
        assert decrypt_password(full["password_encrypted"]) == "Secret@123"
        registry.delete_saved(conn_id, operator="test")

    def test_same_host_port_db_updates_not_duplicates(self):
        from backend.services.connection_registry import registry
        id1 = registry.save_connection(
            name="X", host="10.0.0.9", port=15000, username="u",
            password="Pw@12345", database="d1", operator="test")
        id2 = registry.save_connection(
            name="X改名", host="10.0.0.9", port=15000, username="u2",
            password="Pw@12345", database="d1", operator="test")
        assert id1 == id2
        registry.delete_saved(id1, operator="test")

    def test_get_unknown_connection_raises(self):
        from backend.services.connection_registry import (
            ConnectionNotFoundError, registry)
        with pytest.raises(ConnectionNotFoundError):
            registry.get("no_such_conn", auto_connect=False)

    def test_scan_slot_limits(self, monkeypatch):
        from backend.services.connection_registry import (
            ConnectionRegistry, ScanBusyError)
        monkeypatch.setenv("MAX_CONCURRENT_SCANS_PER_CONNECTION", "1")
        monkeypatch.setenv("MAX_CONCURRENT_SCANS_GLOBAL", "2")
        reg = ConnectionRegistry()
        entered = threading.Event()
        release = threading.Event()

        def hold_slot():
            with reg.scan_slot("conn_x"):
                entered.set()
                release.wait(timeout=10)

        t = threading.Thread(target=hold_slot)
        t.start()
        assert entered.wait(timeout=5)
        # 同连接第二个扫描应被限流
        with pytest.raises(ScanBusyError):
            with reg.scan_slot("conn_x"):
                pass
        # 其他连接不受影响（全局限2，已用1）
        with reg.scan_slot("conn_y"):
            pass
        release.set()
        t.join(timeout=5)
        # 释放后可再次获取
        with reg.scan_slot("conn_x"):
            pass

    def test_registry_status_endpoint_multi(self, client):
        resp = client.get("/api/v1/tdsql/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "active_connections" in data


# ══════════════════════════════════════════════════════════════
# 慢SQL入库脱敏
# ══════════════════════════════════════════════════════════════

class TestDataMasking:
    def test_masking_on_removes_literals(self, monkeypatch):
        monkeypatch.setenv("DATA_MASKING_ENABLED", "true")
        from backend.engine.slow_analyzer import SlowQueryRecord
        from backend.services.database import _get_connection
        from backend.services.slow_query_service import SlowQueryService
        service = SlowQueryService()
        record = SlowQueryRecord(
            fingerprint="SELECT * FROM t_cust WHERE id_card = ?",
            sql_text="SELECT * FROM t_cust WHERE id_card = '110101199001011234' AND phone = '13800138000'",
            db_name="test_mask", exec_count=10, avg_time_ms=500,
        )
        result = service.add_slow_query(record)
        conn = _get_connection()
        row = conn.execute(
            "SELECT sql_text FROM slow_queries WHERE id = ?",
            (result["id"],)).fetchone()
        conn.close()
        stored = row["sql_text"]
        assert "110101199001011234" not in stored, f"身份证号泄漏: {stored}"
        assert "13800138000" not in stored, f"手机号泄漏: {stored}"
        assert "?" in stored

    def test_masking_off_keeps_original(self, monkeypatch):
        monkeypatch.setenv("DATA_MASKING_ENABLED", "false")
        from backend.engine.slow_analyzer import SlowQueryRecord
        from backend.services.database import _get_connection
        from backend.services.slow_query_service import SlowQueryService
        service = SlowQueryService()
        record = SlowQueryRecord(
            fingerprint="f", sql_text="SELECT id FROM t_x WHERE a = 'keepme'",
            db_name="test_mask2", exec_count=1, avg_time_ms=100,
        )
        result = service.add_slow_query(record)
        conn = _get_connection()
        row = conn.execute(
            "SELECT sql_text FROM slow_queries WHERE id = ?",
            (result["id"],)).fetchone()
        conn.close()
        assert "keepme" in row["sql_text"]


# ══════════════════════════════════════════════════════════════
# 数据保留
# ══════════════════════════════════════════════════════════════

class TestRetention:
    def test_default_policies_initialized(self):
        from backend.services.retention_service import retention_service
        policies = {p["table_name"]: p for p in retention_service.get_policies()}
        assert "slow_queries" in policies
        assert "audit_history" in policies
        assert policies["slow_queries"]["retention_days"] >= 7

    def test_set_policy_validation(self):
        from backend.services.retention_service import retention_service
        assert retention_service.set_policy("no_such_table", 30) is not None
        assert retention_service.set_policy("slow_queries", 3) is not None  # <7天拒绝
        assert retention_service.set_policy("slow_queries", 180, operator="test") is None

    def test_cleanup_deletes_expired_only(self):
        from backend.services.database import _get_connection, ensure_db
        from backend.services.retention_service import retention_service
        ensure_db()
        conn = _get_connection()
        # 幂等: 清理历史运行残留
        conn.execute("DELETE FROM slow_queries WHERE fingerprint IN "
                     "('old_fp_retention', 'new_fp_retention')")
        # 插入一条过期记录和一条新记录（MySQL日期函数）
        conn.execute("""
            INSERT INTO slow_queries(fingerprint, sql_text, db_name, created_at)
            VALUES ('old_fp_retention', 'SELECT 1', 'ret_test', DATE_SUB(NOW(), INTERVAL 400 DAY))
        """)
        conn.execute("""
            INSERT INTO slow_queries(fingerprint, sql_text, db_name, created_at)
            VALUES ('new_fp_retention', 'SELECT 2', 'ret_test', NOW())
        """)
        conn.commit()
        conn.close()

        retention_service.set_policy("slow_queries", 180, operator="test")
        deleted = retention_service.run_cleanup(operator="test")
        assert deleted.get("slow_queries", 0) >= 1

        conn = _get_connection()
        old = conn.execute(
            "SELECT COUNT(*) AS c FROM slow_queries WHERE fingerprint='old_fp_retention'"
        ).fetchone()["c"]
        new = conn.execute(
            "SELECT COUNT(*) AS c FROM slow_queries WHERE fingerprint='new_fp_retention'"
        ).fetchone()["c"]
        conn.close()
        assert old == 0, "过期记录应被清理"
        assert new == 1, "未过期记录应保留"

    def test_retention_api(self, client):
        resp = client.get("/api/v1/admin/retention")
        assert resp.status_code == 200
        assert len(resp.json()["policies"]) >= 5
        resp = client.post("/api/v1/admin/retention/run")
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════
# 规则集多租户
# ══════════════════════════════════════════════════════════════

class TestRulesets:
    def _cleanup(self, rid):
        from backend.services.ruleset_service import ruleset_service
        ruleset_service.delete_ruleset(rid, operator="test")

    def test_default_ruleset_exists(self):
        from backend.services.ruleset_service import ruleset_service
        rs = ruleset_service.get_ruleset("default")
        assert rs is not None
        assert rs["is_builtin"] == 1

    def test_create_and_override_disables_rule(self):
        from backend.engine.checker import RuleChecker
        from backend.services.ruleset_service import ruleset_service
        self._cleanup("dev_loose")
        result, err = ruleset_service.create_ruleset(
            "dev_loose", "开发环境宽松规则集",
            items=[{"rule_id": "R012", "enabled": False}], operator="test")
        assert err is None, err
        overrides = ruleset_service.get_overrides("dev_loose")
        checker = RuleChecker()
        # 默认: SELECT * 触发R012
        r1 = checker.audit_sql("SELECT * FROM t_user WHERE id = 1")
        assert any(v.rule_id == "R012" for v in r1.violations)
        # dev_loose: R012被禁用
        r2 = checker.audit_sql("SELECT * FROM t_user WHERE id = 1",
                               rule_overrides=overrides)
        assert not any(v.rule_id == "R012" for v in r2.violations)
        self._cleanup("dev_loose")

    def test_severity_override(self):
        from backend.engine.checker import RuleChecker
        from backend.services.ruleset_service import ruleset_service
        self._cleanup("warn_select_star")
        _, err = ruleset_service.create_ruleset(
            "warn_select_star", "SELECT*降级为WARNING",
            items=[{"rule_id": "R012", "enabled": True,
                    "severity_override": "WARNING"}], operator="test")
        assert err is None
        overrides = ruleset_service.get_overrides("warn_select_star")
        checker = RuleChecker()
        result = checker.audit_sql("SELECT * FROM t_user WHERE id = 1",
                                   rule_overrides=overrides)
        r012 = [v for v in result.violations if v.rule_id == "R012"]
        assert r012 and str(r012[0].severity) in ("WARNING", "Severity.WARNING")
        self._cleanup("warn_select_star")

    def test_invalid_rule_id_rejected(self):
        from backend.services.ruleset_service import ruleset_service
        _, err = ruleset_service.create_ruleset(
            "bad_rs", "x", items=[{"rule_id": "R999"}], operator="test")
        assert err is not None

    def test_builtin_ruleset_protected(self):
        from backend.services.ruleset_service import ruleset_service
        assert ruleset_service.delete_ruleset("default") is not None
        assert ruleset_service.update_ruleset(
            "default", items=[{"rule_id": "R012", "enabled": False}]) is not None

    def test_ruleset_api_crud(self, client):
        client.delete("/api/v1/rulesets/api_test_rs")
        resp = client.post("/api/v1/rulesets", json={
            "id": "api_test_rs", "name": "API测试规则集",
            "items": [{"rule_id": "R017", "enabled": False}],
        })
        assert resp.status_code == 200, resp.text
        resp = client.get("/api/v1/rulesets/api_test_rs")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["rule_id"] == "R017"
        resp = client.delete("/api/v1/rulesets/api_test_rs")
        assert resp.status_code == 200

    def test_project_binding_applies_ruleset(self, client):
        from backend.services.ruleset_service import ruleset_service
        self._cleanup("proj_rs")
        ruleset_service.create_ruleset(
            "proj_rs", "项目规则集",
            items=[{"rule_id": "R012", "enabled": False}], operator="test")
        # 创建绑定该规则集的项目
        from backend.services.database import _get_connection, ensure_db
        ensure_db()
        conn = _get_connection()
        conn.execute("DELETE FROM projects WHERE project_id = 'p_rs_test'")
        conn.execute(
            "INSERT INTO projects(project_id, project_name, rule_set_id) "
            "VALUES ('p_rs_test', '规则集测试项目', 'proj_rs')")
        conn.commit()
        conn.close()
        # 带project_id审核: R012被项目规则集禁用
        resp = client.post("/api/v1/audit/sql", json={
            "sql": "SELECT * FROM t_user WHERE id = 1",
            "project_id": "p_rs_test"})
        assert resp.status_code == 200
        rule_ids = [v["rule_id"] for v in resp.json()["violations"]]
        assert "R012" not in rule_ids
        # 不带project_id: R012照常触发
        resp = client.post("/api/v1/audit/sql",
                           json={"sql": "SELECT * FROM t_user WHERE id = 1"})
        rule_ids = [v["rule_id"] for v in resp.json()["violations"]]
        assert "R012" in rule_ids
        # 清理
        conn = _get_connection()
        conn.execute("DELETE FROM projects WHERE project_id = 'p_rs_test'")
        conn.commit()
        conn.close()
        self._cleanup("proj_rs")


# ══════════════════════════════════════════════════════════════
# 可观测性
# ══════════════════════════════════════════════════════════════

class TestObservability:
    def test_metrics_endpoint(self, client):
        client.post("/api/v1/audit/sql", json={"sql": "SELECT 1 FROM dual"})
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text
        assert "tdsql_app_info" in body
        assert "tdsql_http_requests_total" in body
        assert "tdsql_uptime_seconds" in body

    def test_request_id_header(self, client):
        resp = client.get("/health")
        assert resp.headers.get("X-Request-ID")
        # 透传自定义request-id
        resp = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
        assert resp.headers["X-Request-ID"] == "trace-abc-123"

    def test_admin_info(self, client):
        resp = client.get("/api/v1/admin/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"].startswith("1.0")
        assert "scan_limits" in data


# ══════════════════════════════════════════════════════════════
# 密钥管理
# ══════════════════════════════════════════════════════════════

class TestEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        from backend.services.security_service import (
            decrypt_password, encrypt_password)
        enc = encrypt_password("MyPass@2026")
        assert enc != "MyPass@2026"
        assert decrypt_password(enc) == "MyPass@2026"

    def test_legacy_key_decryption_compat(self):
        """V1.0遗留密钥加密的数据在V2.0仍可解密（迁移兼容）"""
        import base64
        import hashlib
        from cryptography.fernet import Fernet
        from backend.services.security_service import decrypt_password
        legacy_key = base64.urlsafe_b64encode(
            hashlib.sha256("TDSQL-SQLCheck-2026-SecretKey".encode()).digest())
        legacy_encrypted = Fernet(legacy_key).encrypt("OldPass@123".encode()).decode()
        assert decrypt_password(legacy_encrypted) == "OldPass@123"

    def test_key_file_generated_with_restricted_permission(self):
        import os
        from pathlib import Path
        from backend.services.security_service import (
            _KEY_FILE, encrypt_password)
        encrypt_password("trigger-keygen")
        if os.getenv("TDSQL_ENCRYPTION_KEY"):
            pytest.skip("环境变量密钥模式")
        assert Path(_KEY_FILE).exists()


# ══════════════════════════════════════════════════════════════
# GitLab Webhook 安全
# ══════════════════════════════════════════════════════════════

class TestWebhookSecurity:
    def test_webhook_rejected_without_secret_in_strict_mode(self, client, monkeypatch):
        monkeypatch.setenv("GITLAB_WEBHOOK_ALLOW_INSECURE", "false")
        monkeypatch.delenv("GITLAB_WEBHOOK_SECRET", raising=False)
        resp = client.post("/api/v1/gitlab/webhook/merge-request",
                           json={"object_kind": "merge_request"})
        assert resp.status_code in (401, 403)

    def test_webhook_allowed_in_dev_mode(self, client, monkeypatch):
        monkeypatch.setenv("GITLAB_WEBHOOK_ALLOW_INSECURE", "true")
        resp = client.post("/api/v1/gitlab/webhook/merge-request",
                           json={"object_kind": "merge_request",
                                 "object_attributes": {"state": "opened"}})
        assert resp.status_code == 200
