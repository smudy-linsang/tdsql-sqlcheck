# -*- coding: utf-8 -*-
import pytest
from pydantic import ValidationError
from backend.models.copilot import ProviderUpdateRequest, ProviderCreateRequest


class TestProviderUpdateSchema:
    def test_provider_update_request_accepts_protocol(self):
        """验证 ProviderUpdateRequest 允许传入 protocol 且不会触发 422 extra_forbidden"""
        req = ProviderUpdateRequest(
            expected_revision=1,
            name="Qwen-Test",
            protocol="OPENAI_COMPAT_CHAT",
            secret_action="KEEP",
        )
        assert req.name == "Qwen-Test"
        assert req.protocol == "OPENAI_COMPAT_CHAT"
        assert req.secret_action == "KEEP"
        assert req.expected_revision == 1

    def test_provider_update_request_protocol_optional(self):
        """protocol 是可选的，留空时默认为 None"""
        req = ProviderUpdateRequest(
            expected_revision=1,
            name="Qwen-Test2",
        )
        assert req.protocol is None
        assert req.expected_revision == 1

    def test_provider_update_request_rejects_unknown_extra_fields(self):
        """extra='forbid' 依然有效，非法字段依然被拒绝"""
        with pytest.raises(ValidationError) as excinfo:
            ProviderUpdateRequest(
                expected_revision=1,
                name="Qwen-Test3",
                unknown_field="forbidden_value",
            )
        err = excinfo.value.errors()
        assert any(e["loc"] == ("unknown_field",) for e in err)
