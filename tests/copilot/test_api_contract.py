# -*- coding: utf-8 -*-
"""CP-W07 API 合同测试：FastAPI TestClient 全链路（CP-TST-32/33/59/79 部分）。

- 未认证/匿名 → 401 AUTH_REQUIRED（即使 AUTH_ENABLED=false 也拒绝匿名）；
- 无 copilot 菜单角色 → 403；
- B组 UNAVAILABLE → 非 capabilities/help 一律 503；
- 会话/预览/提交幂等（同键重放返回原 turn）；
- 跨用户读他人会话/turn → 404（不暴露存在性）。
"""
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.services.copilot.repository import SubjectRepo
from backend.services.database import _get_connection, ensure_db


def _uid() -> str:
    return uuid.uuid4().hex


def _make_user_with_token(username: str, role: str = "developer"):
    """创建用户并签发有效 token（N-02：iat 须晚于 subject.created_at）"""
    import time
    from backend.services.auth_service import auth_service, issue_token
    auth_service.delete_user(username, operator="test")
    user, err = auth_service.create_user(username, "Test@12345", role,
                                         operator="test")
    assert err is None
    # 等待越过 subject 创建的同秒边界（iat 精度秒级）
    time.sleep(1.1)
    user = auth_service.get_user(username, use_cache=False)
    return issue_token(username, role, int(user.get("token_version", 0) or 0))


class TestAuthGate:
    def test_anonymous_rejected(self, client):
        """AUTH_ENABLED=false 时匿名也拒绝（INV-04/独立身份依赖）；
        未带令牌由认证中间件以统一 401 拦截。"""
        r = client.get("/api/v1/copilot/capabilities")
        assert r.status_code == 401

    def test_capabilities_with_auth(self, client, copilot_db):
        tok = _make_user_with_token("cp_cap_user", "developer")
        r = client.get("/api/v1/copilot/capabilities",
                       headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        body = r.json()
        assert "subject_id" in body and "mode" in body

    def test_admin_requires_copilot_admin_menu(self, client, copilot_db):
        tok = _make_user_with_token("cp_admin_user", "developer")
        r = client.get("/api/v1/copilot-admin/providers",
                       headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403


class TestSessionTurnFlow:
    def test_session_create_and_idempotent_turn(self, client, copilot_db,
                                                copilot_accepting):
        tok = _make_user_with_token("cp_flow_user", "developer")
        h = {"Authorization": f"Bearer {tok}"}
        # 建会话
        r = client.post("/api/v1/copilot/sessions",
                        headers=h, json={"scope_kind": "GLOBAL_HELP",
                                         "instance_type": "unknown",
                                         "page_key": "dashboard"})
        assert r.status_code == 201
        sid = r.json()["session_id"]
        # 预览
        r = client.post(f"/api/v1/copilot/sessions/{sid}/previews",
                        headers=h, json={
                            "expected_session_revision": 1,
                            "scene": "USAGE_HELP", "page_key": "dashboard",
                            "question": "任务失败怎么排查", "source_refs": []})
        assert r.status_code == 201
        pv = r.json()
        # 提交（幂等键）
        crid = _uid()
        body = {"client_request_id": crid, "preview_id": pv["preview_id"],
                "snapshot_hash": pv["snapshot_hash"],
                "expected_session_revision": pv["expected_session_revision"],
                "confirm_data_use": True}
        r1 = client.post(f"/api/v1/copilot/sessions/{sid}/turns",
                         headers=h, json=body)
        assert r1.status_code == 202
        t1 = r1.json()["turn_id"]
        # 同键重放 → 200 返回原 turn
        r2 = client.post(f"/api/v1/copilot/sessions/{sid}/turns",
                         headers=h, json=body)
        assert r2.status_code == 200
        assert r2.json()["turn_id"] == t1
        assert r2.json()["reused"] is True

    def test_cross_user_cannot_read(self, client, copilot_db):
        tok_a = _make_user_with_token("cp_user_a", "developer")
        tok_b = _make_user_with_token("cp_user_b", "developer")
        ha = {"Authorization": f"Bearer {tok_a}"}
        hb = {"Authorization": f"Bearer {tok_b}"}
        r = client.post("/api/v1/copilot/sessions",
                        headers=ha, json={"scope_kind": "GLOBAL_HELP",
                                          "instance_type": "unknown",
                                          "page_key": ""})
        sid = r.json()["session_id"]
        # B 读 A 的会话 → 404（不暴露存在性）
        r2 = client.get(f"/api/v1/copilot/sessions/{sid}", headers=hb)
        assert r2.status_code == 404
        # B 的会话列表不含 A 的
        r3 = client.get("/api/v1/copilot/sessions", headers=hb)
        assert r3.status_code == 200
        ids = [s["session_id"] for s in r3.json()["items"]]
        assert sid not in ids


class TestSchemaGate:
    def test_503_when_b_group_unavailable(self, client, copilot_db):
        """B组标记 UNAVAILABLE 时，业务端点 503；capabilities/help 仍 200。"""
        conn = _get_connection()
        try:
            schema = __import__(
                "backend.services.copilot.schema", fromlist=["mark_unavailable"])
            schema.mark_unavailable(conn, "test-gate")
        finally:
            conn.close()
        tok = _make_user_with_token("cp_gate_user", "developer")
        h = {"Authorization": f"Bearer {tok}"}
        try:
            r = client.get("/api/v1/copilot/capabilities", headers=h)
            assert r.status_code == 200
            assert r.json()["mode"] == "UNAVAILABLE"
            r2 = client.get("/api/v1/copilot/help?query=规则", headers=h)
            assert r2.status_code == 200
            r3 = client.get("/api/v1/copilot/sessions", headers=h)
            assert r3.status_code == 503
            assert r3.json()["detail"]["code"] == "COPILOT_SCHEMA_UNAVAILABLE"
        finally:
            # 恢复 READY
            conn = _get_connection()
            try:
                schema_mod = __import__(
                    "backend.services.copilot.schema",
                    fromlist=["apply_business_schema"])
                schema_mod.apply_business_schema()
            finally:
                conn.close()


class TestInputSensitive:
    def test_sensitive_question_rejected(self, client, copilot_db):
        tok = _make_user_with_token("cp_sens_user", "developer")
        h = {"Authorization": f"Bearer {tok}"}
        r = client.post("/api/v1/copilot/sessions",
                        headers=h, json={"scope_kind": "GLOBAL_HELP",
                                         "instance_type": "unknown",
                                         "page_key": ""})
        sid = r.json()["session_id"]
        r = client.post(f"/api/v1/copilot/sessions/{sid}/previews",
                        headers=h, json={
                            "expected_session_revision": 1,
                            "scene": "USAGE_HELP", "page_key": "",
                            "question": "我的 password='Pw@12345' 怎么用",
                            "source_refs": []})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "INPUT_SENSITIVE"
