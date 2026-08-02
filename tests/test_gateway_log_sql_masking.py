"""网关日志聚合必须先完成脱敏，再截断展示文本。"""
from backend.services.gateway_log_analysis.analyze_gateway_log import normalize_sql as gateway_normalize
from backend.services.gateway_log_analysis.interf_deep_analysis import normalize_sql as deep_normalize


def test_gateway_normalize_masks_literal_that_crosses_legacy_truncation_boundary():
    sql = "SELECT * FROM audit_log WHERE payload='" + ("x" * 240) + "' AND id=7"
    assert gateway_normalize(sql) == "SELECT * FROM audit_log WHERE payload=? AND id=?"


def test_deep_normalize_masks_literal_that_crosses_legacy_truncation_boundary():
    sql = "SELECT * FROM audit_log WHERE payload='" + ("x" * 840) + "' AND id=7"
    assert deep_normalize(sql) == "SELECT * FROM audit_log WHERE payload=? AND id=?"
