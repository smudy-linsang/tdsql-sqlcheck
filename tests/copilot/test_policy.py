# -*- coding: utf-8 -*-
"""CP-W03 policy.py 测试：端点策略校验与出域闸（CP-TST-37/38/39/61）。"""
import json

import pytest

from backend.services.copilot.errors import CopilotError
from backend.services.copilot.policy import (
    EndpointPolicy, Limits, load_policy, reset_policy_cache,
)


def _write(tmp_path, endpoints):
    p = tmp_path / "ep.json"
    p.write_text(json.dumps({"schema_version": 1, "endpoints": endpoints}),
                 encoding="utf-8")
    return str(p)


def _valid():
    return {
        "endpoint_id": "ep-1", "scheme": "https",
        "canonical_host": "llm-gw.internal", "port": 443, "base_path": "/v1",
        "data_zone": "INTERNAL", "privacy_profile": "INTERNAL_REDACTED",
        "allows_schema_identifiers": False,
        "allowed_resolved_cidrs": ["10.0.0.0/8"], "tls_ca_ref": "internal-ca",
    }


class TestEndpointValidation:
    def test_valid_endpoint(self, tmp_path):
        pol = EndpointPolicy(_write(tmp_path, [_valid()]))
        assert pol.get("ep-1") is not None
        assert pol.build_url("ep-1") == "https://llm-gw.internal:443/v1/chat/completions"

    def test_http_scheme_rejected(self, tmp_path):
        ep = _valid(); ep["scheme"] = "http"
        with pytest.raises(CopilotError):
            EndpointPolicy(_write(tmp_path, [ep]))

    def test_wildcard_cidr_rejected(self, tmp_path):
        ep = _valid(); ep["allowed_resolved_cidrs"] = ["0.0.0.0/0"]
        with pytest.raises(CopilotError):
            EndpointPolicy(_write(tmp_path, [ep]))

    def test_loopback_cidr_rejected(self, tmp_path):
        ep = _valid(); ep["allowed_resolved_cidrs"] = ["127.0.0.1/32"]
        with pytest.raises(CopilotError):
            EndpointPolicy(_write(tmp_path, [ep]))

    def test_cloud_metadata_cidr_rejected(self, tmp_path):
        ep = _valid(); ep["allowed_resolved_cidrs"] = ["169.254.169.254/32"]
        with pytest.raises(CopilotError):
            EndpointPolicy(_write(tmp_path, [ep]))

    def test_public_with_identifiers_rejected(self, tmp_path):
        ep = _valid()
        ep["data_zone"] = "PUBLIC"; ep["privacy_profile"] = "PUBLIC_HELP"
        ep["allows_schema_identifiers"] = True
        with pytest.raises(CopilotError):
            EndpointPolicy(_write(tmp_path, [ep]))

    def test_duplicate_endpoint_id_rejected(self, tmp_path):
        with pytest.raises(CopilotError):
            EndpointPolicy(_write(tmp_path, [_valid(), _valid()]))

    def test_userinfo_in_host_rejected(self, tmp_path):
        ep = _valid(); ep["canonical_host"] = "user:pw@llm-gw.internal"
        with pytest.raises(CopilotError):
            EndpointPolicy(_write(tmp_path, [ep]))


class TestEgressGate:
    def test_internal_data_to_public_denied(self, tmp_path):
        pub = _valid()
        pub.update({"endpoint_id": "ep-pub", "data_zone": "PUBLIC",
                    "privacy_profile": "PUBLIC_HELP"})
        pol = EndpointPolicy(_write(tmp_path, [_valid(), pub]))
        with pytest.raises(CopilotError) as ei:
            pol.egress_check("ep-pub", "INTERNAL_REDACTED")
        assert ei.value.code == "EGRESS_DENIED"

    def test_internal_to_internal_ok(self, tmp_path):
        pol = EndpointPolicy(_write(tmp_path, [_valid()]))
        pol.egress_check("ep-1", "INTERNAL_REDACTED")

    def test_identifiers_require_endpoint_flag(self, tmp_path):
        pol = EndpointPolicy(_write(tmp_path, [_valid()]))
        with pytest.raises(CopilotError):
            pol.egress_check("ep-1", "INTERNAL_REDACTED", identifiers=True)

    def test_restricted_never_egress(self, tmp_path):
        pol = EndpointPolicy(_write(tmp_path, [_valid()]))
        with pytest.raises(CopilotError):
            pol.egress_check("ep-1", "RESTRICTED")


class TestLimits:
    def test_out_of_range_clamped_and_validated(self, monkeypatch):
        monkeypatch.setenv("COPILOT_MAX_ACTIVE_TURNS", "999")
        assert Limits.get("COPILOT_MAX_ACTIVE_TURNS") == 8
        problems = Limits.validate()
        assert any("COPILOT_MAX_ACTIVE_TURNS" in p for p in problems)

    def test_queue_vs_deadline_rule(self, monkeypatch):
        monkeypatch.setenv("COPILOT_QUEUE_MAX_SECONDS", "30")
        monkeypatch.setenv("COPILOT_TURN_DEADLINE_SECONDS", "30")
        problems = Limits.validate()
        assert any("QUEUE" in p for p in problems)
