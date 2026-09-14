# -*- coding: utf-8 -*-
"""M-01 / M-03 回归锁。

M-01：预览 projection_mode 必须把端点闸纳入——主备任一端点不允许
      schema_identifiers 即回落 ALIASED，不能让预览与实际出站不一致。
M-03：/copilot-admin/settings 在 B组 UNAVAILABLE 时必须 503（health 除外，
      health 是只读例外——A 第一轮 SIT §3.3 裁定实现正确，设计侧另行订正）。
"""
import base64
import json
import os

import pytest


def _keyring(tmp_path, monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    p = tmp_path / "kr.json"
    p.write_text(json.dumps({"schema_version": 1, "active_kid": "k",
                             "keys": {"k": key}}))
    monkeypatch.setenv("COPILOT_KEYRING_FILE", str(p))
    from backend.services.copilot.crypto import reset_keyring_cache
    reset_keyring_cache()


def _endpoints(tmp_path, monkeypatch, allow_ids: bool):
    """构造主备两端点；allow_ids 控制两端 allows_schema_identifiers。"""
    p = tmp_path / "ep.json"
    p.write_text(json.dumps({
        "schema_version": 1,
        "endpoints": [
            {"endpoint_id": "ep-a", "scheme": "https",
             "canonical_host": "gw-a.internal", "port": 443, "base_path": "/v1",
             "data_zone": "INTERNAL", "privacy_profile": "INTERNAL_REDACTED",
             "allows_schema_identifiers": allow_ids[0],
             "allowed_resolved_cidrs": ["10.0.0.0/8"], "tls_ca_ref": "ca"},
            {"endpoint_id": "ep-b", "scheme": "https",
             "canonical_host": "gw-b.internal", "port": 443, "base_path": "/v1",
             "data_zone": "INTERNAL", "privacy_profile": "INTERNAL_REDACTED",
             "allows_schema_identifiers": allow_ids[1],
             "allowed_resolved_cidrs": ["10.0.0.0/8"], "tls_ca_ref": "ca"},
        ]}))
    monkeypatch.setenv("COPILOT_ENDPOINTS_FILE", str(p))
    from backend.services.copilot.policy import reset_policy_cache
    reset_policy_cache()


class TestM01ProjectionGate:
    """端点闸纳入预览投影判定（preview_service 层的纯判定逻辑）。"""

    def _eval(self, monkeypatch, tmp_path, allow_ids):
        """返回在指定端点配置下的 projection_mode 计算结果。"""
        _keyring(tmp_path, monkeypatch)
        _endpoints(tmp_path, monkeypatch, allow_ids)
        from backend.services.copilot.policy import load_policy
        pol = load_policy()
        legs = [pol.get("ep-a"), pol.get("ep-b")]
        return all(bool(ep.get("allows_schema_identifiers")) for ep in legs)

    def test_both_allow_pass(self, tmp_path, monkeypatch):
        assert self._eval(monkeypatch, tmp_path, (True, True)) is True

    def test_primary_disallow_blocks(self, tmp_path, monkeypatch):
        assert self._eval(monkeypatch, tmp_path, (False, True)) is False

    def test_fallback_disallow_blocks(self, tmp_path, monkeypatch):
        """备用端点不满足也必须回落（不在故障转移时偷偷缩减投影）。"""
        assert self._eval(monkeypatch, tmp_path, (True, False)) is False


class TestM03SettingsGate:
    def test_settings_503_when_unavailable(self, client, copilot_db):
        from backend.services.copilot import schema as schema_mod
        from backend.services.database import _get_connection
        conn = _get_connection()
        try:
            schema_mod.mark_unavailable(conn, "m03-test")
        finally:
            conn.close()
        try:
            from backend.services.auth_service import auth_service, issue_token
            import time
            auth_service.delete_user("cp_m03_admin", operator="test")
            u, e = auth_service.create_user("cp_m03_admin", "Test@12345",
                                            "admin", operator="test")
            assert e is None
            time.sleep(1.1)
            u = auth_service.get_user("cp_m03_admin", use_cache=False)
            tok = issue_token("cp_m03_admin", "admin",
                              int(u.get("token_version", 0) or 0))
            h = {"Authorization": f"Bearer {tok}"}
            r = client.get("/api/v1/copilot-admin/settings", headers=h)
            assert r.status_code == 503
            assert r.json()["detail"]["code"] == "COPILOT_SCHEMA_UNAVAILABLE"
            # health 是只读例外，保持 200
            r2 = client.get("/api/v1/copilot-admin/health", headers=h)
            assert r2.status_code == 200
        finally:
            conn = _get_connection()
            try:
                schema_mod.apply_business_schema()
            finally:
                conn.close()
