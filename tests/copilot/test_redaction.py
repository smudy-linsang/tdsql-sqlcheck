# -*- coding: utf-8 -*-
"""CP-W04 redaction.py 测试：敏感检测闭集与标识符校验（CP-TST-36/62）。"""
import pytest

from backend.services.copilot.redaction import (
    AliasMapper, detect_sensitive, is_valid_identifier, mask_sql_for_model,
    truncate_utf8,
)


class TestSensitiveDetection:
    def test_password_assignment(self):
        assert "CREDENTIAL_ASSIGNMENT" in detect_sensitive("password='Pw@12345'")
        assert "CREDENTIAL_ASSIGNMENT" in detect_sensitive('api_key: "xyz12345"')
        assert "CREDENTIAL_ASSIGNMENT" in detect_sensitive("口令 = abcdef")

    def test_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c"
        assert "JWT" in detect_sensitive(f"token: {jwt}")

    def test_pem(self):
        assert "PEM_PRIVATE_KEY" in detect_sensitive(
            "-----BEGIN RSA PRIVATE KEY-----\nMII...")

    def test_uri_userinfo(self):
        assert "URI_USERINFO" in detect_sensitive(
            "mysql://root:secret@10.0.0.1:3306/db")

    def test_auth_header(self):
        assert "AUTH_HEADER" in detect_sensitive("Authorization: Bearer abcdef123")

    def test_known_provider_key(self):
        assert "KNOWN_PROVIDER_KEY" in detect_sensitive(
            "key = sk-abcdefghijklmnop1234567890")

    def test_clean_sql_not_flagged(self):
        assert detect_sensitive("SELECT id, name FROM users WHERE id = 1") == []

    def test_log_does_not_contain_hit_content(self):
        """检测只返回类型，不返回命中内容（长度/内容均不泄漏）。"""
        hits = detect_sensitive("password='Pw@12345'")
        assert hits and not any("Pw@12345" in h for h in hits)


class TestIdentifiers:
    def test_valid(self):
        assert is_valid_identifier("orders_2024")
        assert is_valid_identifier("订单表")

    def test_invalid(self):
        assert not is_valid_identifier("")
        assert not is_valid_identifier("a" * 65)
        assert not is_valid_identifier("bad/name")
        assert not is_valid_identifier("a@b")
        assert not is_valid_identifier("tab\nname")
        assert not is_valid_identifier("semi;colon")  # 分号属非法特征
        assert not is_valid_identifier("user--x")     # 连续横线特征

    def test_alias_mapper_collision_avoidance(self):
        m = AliasMapper()
        a1 = m.alias_for("TABLE", "orders")
        a2 = m.alias_for("TABLE", "users")
        assert a1 != a2
        # 重复同名同别名
        assert m.alias_for("TABLE", "orders") == a1

    def test_alias_avoids_real_name_collision(self):
        m = AliasMapper()
        # 真实名称恰好叫 TABLE_1 时，别名必须跳过它
        m.project_identifier("TABLE", "TABLE_1", identifiers_allowed=True)
        alias = m.alias_for("TABLE", "real_table")
        assert alias != "TABLE_1"

    def test_projection_gating(self):
        m = AliasMapper()
        # 不允许标识符 → 别名
        assert m.project_identifier("TABLE", "orders", False) != "orders"
        # 允许 → 真实名称
        assert m.project_identifier("TABLE", "orders", True) == "orders"
        # 允许但名称非法 → 别名化
        assert m.project_identifier("TABLE", "bad/name", True) != "bad/name"


class TestMasking:
    def test_literals_masked(self):
        out = mask_sql_for_model("SELECT * FROM t WHERE name = '张三' AND id = 42")
        assert "张三" not in out and "42" not in out and "?" in out

    def test_unparseable_returns_none(self):
        assert mask_sql_for_model("") is None

    def test_truncate_utf8(self):
        s = truncate_utf8("中文测试abc", 6)
        assert len(s.encode("utf-8")) <= 6
