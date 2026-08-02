from __future__ import annotations

import hashlib

import pytest

from backend.services.raw_slowlog_parser import make_anchor, parse_incremental_chunk, verify_anchor
from backend.services.sql_masking import SQLMaskingError, mask_and_fingerprint


_FIRST = b"""# Time: 2026-08-02T10:00:01.123456
# User@Host: app[app] @ proxy [10.1.2.3]
# Thread_id: 42  Schema: payment
# Query_time: 1.250000  Lock_time: 0.001000 Rows_sent: 1  Rows_examined: 200
SET timestamp=1785636001;
SELECT * FROM orders WHERE card_no='6222-1234-5678-9999' AND amount=120.50;
"""
_SECOND = b"""# Time: 2026-08-02T10:01:01
# User@Host: app[app] @ proxy [10.1.2.3]
# Thread_id: 43  Schema: payment
# Query_time: 2.000000  Lock_time: 0.000000 Rows_sent: 1  Rows_examined: 10
SET timestamp=1785636061;
SELECT * FROM orders WHERE id=9;
"""


def test_parser_uses_proxy_log_time_offsets_and_masked_template():
    payload = _FIRST + _SECOND
    result = parse_incremental_chunk(payload, 1000)

    assert len(result.complete_blocks) == 2
    first = result.complete_blocks[0]
    assert first.offset_start == 1000
    assert first.offset_end == 1000 + len(_FIRST)
    assert first.event_time.isoformat() == "2026-08-02T10:00:01.123456"
    assert first.query_time_us == 1_250_000
    assert first.lock_time_us == 1_000
    assert first.db_name == "payment"
    assert "6222-1234" not in first.sql.stored_template
    assert "120.50" not in first.sql.stored_template
    assert first.sql.fingerprint == hashlib.sha256(first.sql.template.encode()).hexdigest()
    assert result.next_safe_offset == 1000 + len(payload)
    assert result.incomplete_tail_start is None


def test_parser_accepts_tdsql_compact_time_with_space_microseconds():
    payload = b"""# Time: 260731 13:45:06 303896
# Query_time: 1.000000  Lock_time: 0.000000 Rows_sent: 1  Rows_examined: 1
SELECT id FROM payment_order WHERE card_no='6222-1234-5678-9999';
"""
    result = parse_incremental_chunk(payload, 0)

    assert len(result.complete_blocks) == 1
    assert result.complete_blocks[0].event_time.isoformat() == "2026-07-31T13:45:06.303896"
    assert result.parse_errors == []


def test_parser_does_not_advance_over_unfinished_last_block():
    unfinished = _FIRST + b"# Time: 2026-08-02T10:02:01\n# Query_time: 1.0 Lock_time: 0 Rows_sent: 1 Rows_examined: 1\nSELECT 'secret'"
    result = parse_incremental_chunk(unfinished, 0)

    assert len(result.complete_blocks) == 1
    assert result.next_safe_offset == len(_FIRST)
    assert result.incomplete_tail_start == len(_FIRST)


def test_parser_keeps_multiline_sql_as_one_masked_complete_block():
    multi = b"""# Time: 2026-08-02T10:03:01
# Query_time: 3.000000  Lock_time: 0.000000 Rows_sent: 1  Rows_examined: 12
SELECT id,
       JSON_EXTRACT(payload, '$.account')
FROM payment_order
WHERE card_no='6222-1234-5678-9999'
  AND amount=120.50;
"""
    result = parse_incremental_chunk(multi, 77)
    assert len(result.complete_blocks) == 1
    block = result.complete_blocks[0]
    assert block.offset_start == 77 and block.offset_end == 77 + len(multi)
    assert "payment_order" in block.sql.stored_template
    assert "6222-1234" not in block.sql.stored_template
    assert "120.50" not in block.sql.stored_template


def test_parser_does_not_skip_bounded_unknown_format_block():
    invalid = b"# Time: 2026-08-02T10:02:01\n# Query_time: invalid\nSELECT 'secret';\n"
    result = parse_incremental_chunk(_FIRST + invalid + _SECOND, 0)

    assert len(result.complete_blocks) == 1
    assert result.next_safe_offset == len(_FIRST)
    assert result.parse_errors[0].offset_start == len(_FIRST)


def test_anchor_detects_copytruncate_content_change():
    anchor = make_anchor(b"prefix" + b"x" * 100, 106)
    assert verify_anchor(b"x" * 64, anchor["anchor_sha256"])
    assert not verify_anchor(b"y" * 64, anchor["anchor_sha256"])


def test_masking_fingerprint_is_based_on_full_template_not_display_truncation():
    first = mask_and_fingerprint("SELECT 'secret' " + "x" * 9000)
    second = mask_and_fingerprint("SELECT 'another-secret' " + "x" * 9000)
    assert first.truncated and second.truncated
    assert first.stored_template == second.stored_template
    assert first.fingerprint == second.fingerprint
    with pytest.raises(SQLMaskingError):
        mask_and_fingerprint("SELECT 'unclosed")


def test_masking_removes_comments_json_strings_hex_and_numeric_literals():
    masked = mask_and_fingerprint("""
        /* token=should-not-survive */
        SELECT JSON_EXTRACT(doc, '$.card'), X'ABCD', 0xDEADBEEF
        FROM account -- note=should-not-survive
        WHERE card='6222-1234' AND amount=120.50 AND id=42;
    """).stored_template
    for secret in ("should-not-survive", "$.card", "ABCD", "DEADBEEF", "6222-1234", "120.50", "42"):
        assert secret not in masked
