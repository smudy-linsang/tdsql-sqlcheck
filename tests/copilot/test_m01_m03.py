# -*- coding: utf-8 -*-
"""M-01/M-03：真实预览投影闸与 settings 门禁；测试数据、配置均隔离。"""
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest


class TestM01ProjectionGate:
    @pytest.mark.parametrize(
        "primary_allowed,fallback_allowed,deploy_allowed,grant_allowed,has_route,expected",
        [
            (True, True, True, True, True, "SCHEMA_IDENTIFIERS"),
            (False, True, True, True, True, "ALIASED"),
            (True, False, True, True, True, "ALIASED"),
            (False, False, True, True, True, "ALIASED"),
            (True, None, True, True, True, "SCHEMA_IDENTIFIERS"),
            (True, True, False, True, True, "ALIASED"),
            (True, True, True, False, True, "ALIASED"),
            (True, True, True, True, False, "ALIASED"),
        ],
        ids=["both-allow", "primary-deny", "fallback-deny", "both-deny",
             "primary-only", "deploy-deny", "grant-deny", "no-route"],
    )
    def test_preview_projection(self, copilot_conn, keyring_file, tmp_path, monkeypatch,
                                primary_allowed, fallback_allowed, deploy_allowed,
                                grant_allowed, has_route, expected):
        """X3：真实路由、授权和 build_preview；事务回滚，不覆盖持久场景配置。"""
        from backend.models.copilot import PreviewRequest, CopilotScene
        from backend.services.copilot.authz import CopilotIdentity
        from backend.services.copilot.policy import reset_policy_cache
        from backend.services.copilot.preview_service import build_preview
        from backend.services.copilot.repository import (
            GrantRepo, ProviderRepo, RouteRepo, SessionRepo, SubjectRepo, new_id)

        conn = copilot_conn
        username = "cp_m01_" + uuid.uuid4().hex[:12]
        connection_id = new_id()
        subject_id = SubjectRepo.assign(conn, username, "2026-01-01 00:00:00")
        conn.execute(
            "INSERT INTO tdsql_connections "
            "(id, name, host, port, username, password_encrypted, `database`) "
            "VALUES (?,?,'127.0.0.1',3306,'test','',?)",
            (connection_id, "M01 测试实例", "m01_" + connection_id))
        GrantRepo.upsert_request(conn, subject_id, connection_id, username,
                                 subject_id, "REF-1", grant_allowed, "ID-REF-1")
        assert GrantRepo.approve(conn, subject_id, connection_id,
                                  "test_approver", new_id(), 1)

        endpoints = []
        provider_ids = []
        for index, allowed in enumerate((primary_allowed, fallback_allowed)):
            if allowed is None:
                continue
            endpoint_id = f"ep-m01-{index}"
            endpoints.append({
                "endpoint_id": endpoint_id, "scheme": "https",
                "canonical_host": f"gw-{index}.internal", "port": 443,
                "base_path": "/v1", "data_zone": "INTERNAL",
                "privacy_profile": "INTERNAL_REDACTED",
                "allows_schema_identifiers": allowed,
                "allowed_resolved_cidrs": ["10.0.0.0/8"], "tls_ca_ref": "test-ca",
            })
            provider_id = new_id()
            provider_ids.append(provider_id)
            ProviderRepo.insert(conn, {
                "id": provider_id, "name": "m01_" + provider_id,
                "endpoint_id": endpoint_id, "protocol": "OPENAI_COMPAT_CHAT",
                "model_id": "test-model", "auth_mode": "NETWORK_IDENTITY",
                "capabilities_json": "{}", "secret_envelope": None,
            })
            assert ProviderRepo.mark_tested(conn, provider_id, 1)
            assert ProviderRepo.set_enabled(conn, provider_id, True, 1)
        old_route = RouteRepo.get(conn, "SQL_ADVISE")
        assert RouteRepo.upsert(
            conn, "SQL_ADVISE", provider_ids[0],
            provider_ids[1] if len(provider_ids) > 1 else None,
            "INTERNAL_REDACTED", has_route, "test",
            int(old_route["revision"]) if old_route else 0)
        session_id = new_id()
        SessionRepo.insert(conn, {
            "id": session_id, "owner_subject_id": subject_id, "owner": username,
            "scope_kind": "INSTANCE", "connection_id": connection_id,
            "database_name": "db1", "instance_type": "distributed",
            "initial_page_key": "", "name_snapshot": "M01 测试实例",
            "name_source": "snapshot", "title": "投影回归锁",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).strftime(
                "%Y-%m-%d %H:%M:%S.%f"),
        })
        session = SessionRepo.get(conn, session_id)
        identity = CopilotIdentity(username, "developer", subject_id, "")
        req = PreviewRequest(
            expected_session_revision=1, scene=CopilotScene.SQL_ADVISE,
            question="帮我优化这条 SQL", source_refs=[],
            draft={"kind": "SQL", "text": "SELECT 1", "revision": None})
        path = tmp_path / "endpoints.json"
        path.write_text(json.dumps({"schema_version": 1, "endpoints": endpoints}),
                        encoding="utf-8")
        try:
            with monkeypatch.context() as patch:
                patch.setenv("COPILOT_ENDPOINTS_FILE", str(path))
                patch.setenv("COPILOT_ALLOW_SCHEMA_IDENTIFIERS",
                             "true" if deploy_allowed else "false")
                reset_policy_cache()
                record = build_preview(conn, identity, session, req)
                assert record["projection_mode"] == expected
                snapshot = record["route_snapshot"]
                assert snapshot["mode"] == ("ROUTE" if has_route else "LOCAL_ONLY")
                if has_route:
                    assert snapshot["primary"]["provider_id"] == provider_ids[0]
                    if fallback_allowed is None:
                        assert snapshot["fallback"] is None
                    else:
                        assert snapshot["fallback"]["provider_id"] == provider_ids[1]
        finally:
            reset_policy_cache()


class TestM03SettingsGate:
    def test_settings_503_when_unavailable(self, client, copilot_db):
        from backend.services.auth_service import auth_service, issue_token
        from backend.services.copilot import schema as schema_mod
        from backend.services.database import _get_connection

        # N-02：不先删固定账号；每次新建，并只清理本次 fixture 所有的记录。
        username = "cp_m03_" + uuid.uuid4().hex[:12]
        user, error = auth_service.create_user(username, "Test@12345", "admin",
                                               operator="test")
        assert error is None, error
        try:
            time.sleep(1.1)  # JWT iat 必须严格晚于 subject 创建秒。
            token = issue_token(username, "admin", int(user.get("token_version") or 0))
            headers = {"Authorization": f"Bearer {token}"}
            settings = client.get("/api/v1/copilot-admin/settings", headers=headers)
            assert settings.status_code == 200
            revision = settings.json()["config_revision"]
            conn = _get_connection()
            try:
                schema_mod.mark_unavailable(conn, "m03-test")
            finally:
                conn.close()
            try:
                for method, kwargs in (
                    (client.get, {}),
                    (client.put, {"json": {"expected_revision": revision}}),
                ):
                    response = method("/api/v1/copilot-admin/settings", headers=headers, **kwargs)
                    assert response.status_code == 503
                    assert response.json()["detail"]["code"] == "COPILOT_SCHEMA_UNAVAILABLE"
                assert client.get("/api/v1/copilot-admin/health", headers=headers).status_code == 503
            finally:
                assert schema_mod.apply_business_schema() == 0
        finally:
            # fixture 回收不走“保留最后一个管理员”的产品删除动作，恢复运行前账号集合。
            conn = _get_connection()
            try:
                conn.execute("DELETE FROM users WHERE username = ?", (username,))
                conn.execute("DELETE FROM copilot_subjects WHERE username = ?", (username,))
                conn.commit()
            finally:
                conn.close()
            assert auth_service.get_user(username, use_cache=False) is None
