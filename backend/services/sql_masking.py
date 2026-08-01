"""统一的 SQL 脱敏与指纹服务。

原始慢日志、网关离线分析和既有慢 SQL 展示必须使用同一套字面量处理。
本模块只处理展示/存储模板，绝不返回原始 SQL，也不记录原始 SQL 到日志。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


MAX_TEMPLATE_BYTES = 8 * 1024


class SQLMaskingError(ValueError):
    """无法确认字面量边界时拒绝落库，避免误存敏感内容。"""


@dataclass(frozen=True)
class MaskedSQL:
    """脱敏结果。fingerprint 永远基于未截断的完整模板。"""

    template: str
    fingerprint: str
    stored_template: str
    truncated: bool
    original_bytes: int


_HEX_LITERAL = re.compile(r"(?<![A-Za-z0-9_])0x[0-9A-Fa-f]+(?![A-Za-z0-9_])")
_NUMERIC_LITERAL = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d+\.\d+|\d+\.?(?:[eE][-+]?\d+)?)(?![A-Za-z0-9_])"
)
_SPACE = re.compile(r"\s+")
_FIRST_KEYWORD = re.compile(r"^\s*(?:/\*.*?\*/\s*)*([A-Za-z]+)", re.DOTALL)


def _strip_comments_and_mask_strings(sql: str) -> str:
    """逐字符处理注释/引号；无法闭合的引号或注释属于安全失败。"""
    output: list[str] = []
    i = 0
    length = len(sql)
    while i < length:
        char = sql[i]
        following = sql[i + 1] if i + 1 < length else ""

        if char == "-" and following == "-" and (i + 2 == length or sql[i + 2].isspace()):
            end = sql.find("\n", i + 2)
            i = length if end < 0 else end + 1
            output.append(" ")
            continue
        if char == "#":
            end = sql.find("\n", i + 1)
            i = length if end < 0 else end + 1
            output.append(" ")
            continue
        if char == "/" and following == "*":
            end = sql.find("*/", i + 2)
            if end < 0:
                raise SQLMaskingError("unterminated SQL comment")
            i = end + 2
            output.append(" ")
            continue

        # MySQL also accepts X'ABCD' binary literal. Keep its prefix but mask
        # its contents together with the following quoted literal.
        if char in ("'", '"'):
            quote = char
            i += 1
            while i < length:
                current = sql[i]
                if current == "\\":
                    i += 2
                    continue
                if current == quote:
                    if i + 1 < length and sql[i + 1] == quote:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            else:
                raise SQLMaskingError("unterminated SQL string literal")
            output.append("?")
            continue

        output.append(char)
        i += 1
    return "".join(output)


def mask_sql_literals(sql: str) -> str:
    """返回不含注释和字面量值的规范展示模板。

    该函数不是完整 SQL parser；它故意仅处理可无歧义确认的 MySQL 常见
    字面量形式。遇到未闭合的字符串/注释抛出 SQLMaskingError，由调用方拒绝
    保存该记录，而不是猜测并泄漏原文。
    """
    if not isinstance(sql, str) or not sql.strip():
        raise SQLMaskingError("empty SQL text")
    masked = _strip_comments_and_mask_strings(sql)
    masked = _HEX_LITERAL.sub("?", masked)
    masked = _NUMERIC_LITERAL.sub("?", masked)
    masked = _SPACE.sub(" ", masked).strip().rstrip(";").strip()
    if not masked:
        raise SQLMaskingError("SQL text contains no statement after masking")
    return masked


def truncate_utf8(value: str, max_bytes: int = MAX_TEMPLATE_BYTES) -> tuple[str, bool, int]:
    """按 UTF-8 字节截断但永不切断字符，返回文本、截断标识、原始字节数。"""
    encoded = value.encode("utf-8")
    original_bytes = len(encoded)
    if original_bytes <= max_bytes:
        return value, False, original_bytes
    clipped = encoded[:max_bytes]
    while clipped:
        try:
            return clipped.decode("utf-8"), True, original_bytes
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    return "", True, original_bytes


def mask_and_fingerprint(sql: str, max_template_bytes: int = MAX_TEMPLATE_BYTES) -> MaskedSQL:
    """脱敏并以完整模板计算 SHA-256，截断仅影响展示字段。"""
    template = mask_sql_literals(sql)
    fingerprint = hashlib.sha256(template.encode("utf-8")).hexdigest()
    stored, truncated, original_bytes = truncate_utf8(template, max_template_bytes)
    return MaskedSQL(template, fingerprint, stored, truncated, original_bytes)


def statement_type(sql_template: str) -> str:
    """提取有限的语句类型，供检索和统计使用。"""
    match = _FIRST_KEYWORD.match(sql_template or "")
    word = match.group(1).upper() if match else "OTHER"
    return word if word in {"SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER", "CREATE", "DROP"} else "OTHER"
