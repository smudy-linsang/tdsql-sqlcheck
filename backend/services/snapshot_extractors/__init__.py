"""
扫描快照 — 各模块问题项抽取器

设计依据：docs/DETAIL-v1.3-扫描结果对比.md §5.3
"""
from .base import (
    IssueItem,
    FINGERPRINT_ALGO,
    fp,
    stable_text,
    sha1_16,
    parse_object,
)

__all__ = [
    "IssueItem",
    "FINGERPRINT_ALGO",
    "fp",
    "stable_text",
    "sha1_16",
    "parse_object",
]
