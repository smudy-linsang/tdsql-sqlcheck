"""TDSQL/MySQL 原始慢日志的增量、边界安全解析器。

只接收远端导出器读取的字节块；游标推进到最后一个完整日志块的字节末尾。
不复用 legacy gateway parser 的“EOF 最后一块总是完整”行为。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.services.sql_masking import MaskedSQL, SQLMaskingError, mask_and_fingerprint, statement_type


PARSER_PROFILE = "tdsql_mysql_slowlog_v1"
PARSE_VERSION = "1.5.3.0"
ANCHOR_BYTES = 64

_TIME = re.compile(br"(?m)^# Time:\s*(?P<value>[^\r\n]+)")
_QUERY_TIME = re.compile(
    r"^# Query_time:\s*(?P<query>[0-9.]+)\s+Lock_time:\s*(?P<lock>[0-9.]+)"
    r"\s+Rows_sent:\s*(?P<sent>\d+)\s+Rows_examined:\s*(?P<examined>\d+)",
    re.MULTILINE,
)
_THREAD = re.compile(r"^# Thread_id:\s*(?P<thread>\S+)(?:\s+Schema:\s*(?P<schema>\S+))?", re.MULTILINE)
_USER = re.compile(r"^# User@Host:\s*(?P<user>[^\s\[]+).*?@\s*(?P<host>[^\s\[]+)(?:\s*\[(?P<ip>[^\]]+)\])?", re.MULTILINE)
_BACKEND = re.compile(r"^# Backend_host:\s*(?P<backend>.+)$", re.MULTILINE)
_SET_TIMESTAMP = re.compile(r"(?m)^SET timestamp=\d+;\s*(?:\r?\n)?")


@dataclass(frozen=True)
class ParseError:
    offset_start: int
    code: str
    detail: str


@dataclass(frozen=True)
class ParsedBlock:
    offset_start: int
    offset_end: int
    event_time: datetime
    db_name: str
    client_user: str
    client_host: str
    backend_host: str
    thread_id: str
    query_time_us: int
    lock_time_us: int
    rows_sent: int
    rows_examined: int
    sql: MaskedSQL
    statement_type: str
    extra_json: str


@dataclass(frozen=True)
class ParsedChunk:
    complete_blocks: list[ParsedBlock] = field(default_factory=list)
    next_safe_offset: int = 0
    incomplete_tail_start: int | None = None
    parse_errors: list[ParseError] = field(default_factory=list)


def _seconds_to_us(value: str) -> int:
    # Decimal-free conversion avoids floating point boundary errors.
    whole, _, fraction = value.partition(".")
    return int(whole or "0") * 1_000_000 + int((fraction + "000000")[:6])


def _parse_time(raw: str, timezone: str) -> datetime:
    value = raw.strip()
    formats = (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%y%m%d %H:%M:%S",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            try:
                # MySQL DATETIME stores local wall time, so intentionally
                # return a naive value after validating the configured zone.
                ZoneInfo(timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"invalid IANA timezone: {timezone}") from exc
            return parsed
        except ValueError:
            continue
    raise ValueError(f"unsupported # Time value: {value[:80]}")


def _last_sql(text: str) -> str:
    """取 Query_time 后的 SQL，排除 MySQL 慢日志的 SET timestamp 伪语句。"""
    query_match = _QUERY_TIME.search(text)
    if not query_match:
        return ""
    rest = text[query_match.end():]
    rest = _SET_TIMESTAMP.sub("", rest).strip()
    # 下一条元信息在块边界前不应出现；防御异常格式时不要把其当 SQL。
    return "\n".join(line for line in rest.splitlines() if not line.startswith("#")).strip()


def _is_eof_block_complete(raw_block: bytes) -> bool:
    """无下一条 # Time 边界时，仅在日志记录显式以分号结束时提交。"""
    stripped = raw_block.rstrip()
    return bool(stripped) and stripped.endswith(b";") and b"# Query_time:" in stripped


def _parse_one(raw_block: bytes, offset_start: int, offset_end: int, timezone: str) -> ParsedBlock:
    text = raw_block.decode("utf-8", errors="replace")
    time_match = _TIME.search(raw_block)
    if not time_match:
        raise ValueError("missing # Time")
    event_time = _parse_time(time_match.group("value").decode("utf-8", errors="replace"), timezone)
    query = _QUERY_TIME.search(text)
    if not query:
        raise ValueError("missing or invalid # Query_time")
    sql_text = _last_sql(text)
    if not sql_text:
        raise ValueError("missing SQL after # Query_time")
    masked = mask_and_fingerprint(sql_text)
    thread = _THREAD.search(text)
    user = _USER.search(text)
    backend = _BACKEND.search(text)
    fields = {
        "parser_profile": PARSER_PROFILE,
        "time_raw": time_match.group("value").decode("utf-8", errors="replace").strip(),
    }
    return ParsedBlock(
        offset_start=offset_start,
        offset_end=offset_end,
        event_time=event_time,
        db_name=(thread.group("schema") if thread and thread.group("schema") else ""),
        client_user=(user.group("user") if user else ""),
        client_host=(user.group("ip") if user and user.group("ip") else (user.group("host") if user else "")),
        backend_host=(backend.group("backend").strip() if backend else ""),
        thread_id=(thread.group("thread") if thread else ""),
        query_time_us=_seconds_to_us(query.group("query")),
        lock_time_us=_seconds_to_us(query.group("lock")),
        rows_sent=int(query.group("sent")),
        rows_examined=int(query.group("examined")),
        sql=masked,
        statement_type=statement_type(masked.template),
        extra_json=json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
    )


def parse_incremental_chunk(payload: bytes, start_offset: int, timezone: str = "Asia/Shanghai") -> ParsedChunk:
    """解析从安全游标开始读取的一段原始字节。

    未验证完整的末尾块不会返回为 complete，也不会推进 next_safe_offset；
    下次读取会从该块的开头重读，依赖事件 origin 唯一键幂等。
    """
    matches = list(_TIME.finditer(payload))
    if not matches:
        return ParsedChunk(next_safe_offset=start_offset,
                           incomplete_tail_start=start_offset if payload else None)
    complete: list[ParsedBlock] = []
    errors: list[ParseError] = []
    safe_offset = start_offset
    incomplete: int | None = None
    for index, current in enumerate(matches):
        begin = current.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(payload)
        raw = payload[begin:end]
        is_last = index == len(matches) - 1
        if is_last and not _is_eof_block_complete(raw):
            incomplete = start_offset + begin
            break
        try:
            parsed = _parse_one(raw, start_offset + begin, start_offset + end, timezone)
        except (ValueError, SQLMaskingError) as exc:
            errors.append(ParseError(start_offset + begin, "parse_error", str(exc)[:240]))
            # Even a bounded record may be a newly introduced format variant.
            # Do not skip it or any later bytes: an operator must first admit
            # the format, otherwise the platform would silently lose events.
            incomplete = start_offset + begin
            break
        complete.append(parsed)
        safe_offset = start_offset + end
    return ParsedChunk(complete, safe_offset, incomplete, errors)


def make_anchor(raw_bytes: bytes, safe_offset: int, window: int = ANCHOR_BYTES) -> dict[str, int | str]:
    """生成游标锚点，用于识别 copytruncate 后快速重新增长。"""
    if safe_offset <= 0:
        return {"anchor_start_offset": 0, "anchor_length": 0, "anchor_sha256": ""}
    anchor = raw_bytes[max(0, len(raw_bytes) - window):]
    return {
        "anchor_start_offset": max(0, safe_offset - len(anchor)),
        "anchor_length": len(anchor),
        "anchor_sha256": hashlib.sha256(anchor).hexdigest(),
    }


def verify_anchor(raw_bytes: bytes, expected_sha256: str) -> bool:
    """导出器读回的旧锚点与数据库记录必须一致。"""
    if not expected_sha256:
        return True
    return hashlib.sha256(raw_bytes).hexdigest() == expected_sha256
