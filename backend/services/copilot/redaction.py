# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：敏感检测与脱敏投影（CP-W04，DETAIL §4.3/§4.4）。

- 敏感类型闭集（版本化）：AUTH_HEADER / PEM_PRIVATE_KEY / JWT / URI_USERINFO /
  CREDENTIAL_ASSIGNMENT / KNOWN_PROVIDER_KEY；命中即 422 INPUT_SENSITIVE；
  日志只记类型/长度，不记命中内容。
- 结构标识符别名化：默认投影模式 ALIASED（INSTANCE_1/TABLE_1/COLUMN_1）；
  §4.4 三闸全开才允许 SCHEMA_IDENTIFIERS 投影闭集内真实名称。
- 名称校验：1—64 个 Unicode 字符、无控制/格式控制字符、无路径/凭据特征；
  不合格名称一致别名化；别名表与真实名称避碰。

敏感检测是纵深防御，不宣称能发现所有秘密。
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Optional

logger = logging.getLogger("tdsql.copilot.redaction")

# ══════════════════════════════════════════════════════════════════
# 敏感类型闭集检测（版本化 SENSITIVE_RULES_V1）
# ══════════════════════════════════════════════════════════════════

SENSITIVE_RULES_VERSION = 1

_PEM_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b")
_AUTH_HEADER_RE = re.compile(
    r"(?i)(?:authorization|proxy-authorization)\s*[:=]\s*(?:bearer|basic)\s+\S+")
_URI_USERINFO_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]*@")
# 赋值结构：password/passwd/pwd/secret/api_key/access_token/refresh_token/私钥/口令
_CRED_ASSIGN_RE = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|私钥|口令)\s*[:=]\s*['\"]?[^\s'\"]{4,}")
_KNOWN_PROVIDER_KEY_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})\b")


def detect_sensitive(text: str) -> list[str]:
    """检测敏感特征，返回命中的类型清单（不返回命中内容）。"""
    if not text:
        return []
    hits = []
    if _PEM_RE.search(text):
        hits.append("PEM_PRIVATE_KEY")
    if _JWT_RE.search(text):
        hits.append("JWT")
    if _AUTH_HEADER_RE.search(text):
        hits.append("AUTH_HEADER")
    if _URI_USERINFO_RE.search(text):
        hits.append("URI_USERINFO")
    if _CRED_ASSIGN_RE.search(text):
        hits.append("CREDENTIAL_ASSIGNMENT")
    if _KNOWN_PROVIDER_KEY_RE.search(text):
        hits.append("KNOWN_PROVIDER_KEY")
    return hits


def contains_sensitive(text: str) -> bool:
    return bool(detect_sensitive(text))


# ══════════════════════════════════════════════════════════════════
# 结构标识符校验与别名化（§4.4）
# ══════════════════════════════════════════════════════════════════

_IDENT_MAX_CHARS = 64
_CONTROL_RE = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_PATH_CRED_RE = re.compile(r"[/\\@;`]|\.\.|-")


def is_valid_identifier(name: str) -> bool:
    """保守投影上限：1—64 Unicode 字符、无控制/格式控制字符、无路径/凭据特征。"""
    if not name or len(name) > _IDENT_MAX_CHARS:
        return False
    if _CONTROL_RE.search(name):
        return False
    if _PATH_CRED_RE.search(name):
        return False
    return True


class AliasMapper:
    """服务端别名映射：实例/库/表/列 → INSTANCE_1/DB_1/TABLE_1/COLUMN_1。

    别名表与已有真实名称避碰（真实名恰好叫 TABLE_1 时跳过该别名）；
    以映射身份判断模板，不以字符串恰好叫 TABLE_1 判假。
    """

    def __init__(self):
        self._maps: dict[str, dict[str, str]] = {
            "INSTANCE": {}, "DB": {}, "TABLE": {}, "COLUMN": {},
        }
        self._used: set[str] = set()

    def _alloc(self, prefix: str, real_name: str) -> str:
        m = self._maps[prefix]
        if real_name in m:
            return m[real_name]
        i = len(m) + 1
        alias = f"{prefix}_{i}"
        while alias in self._used:
            i += 1
            alias = f"{prefix}_{i}"
        m[real_name] = alias
        self._used.add(alias)
        return alias

    def alias_for(self, kind: str, real_name: str) -> str:
        """kind ∈ INSTANCE/DB/TABLE/COLUMN；非法名称也一致别名化。"""
        return self._alloc(kind, real_name)

    def project_identifier(self, kind: str, real_name: str,
                           identifiers_allowed: bool) -> str:
        """按投影模式返回真实名称或别名；非法名称始终别名化。"""
        if identifiers_allowed and is_valid_identifier(real_name):
            self._used.add(real_name)  # 真实名称占坑，别名避碰
            return real_name
        return self._alloc(kind, real_name)

    @property
    def mapping(self) -> dict[str, dict[str, str]]:
        return {k: dict(v) for k, v in self._maps.items()}


def mask_sql_for_model(sql: str):
    """SQL 字面量脱敏：复用既有 sql_masking 作为基础。

    不能拿被替换的 VARCHAR(?) 模板做 DDL 正确性证明；解析失败时失败关闭
    （返回 None 由调用方降级为结构特征 + 规则证据）。
    """
    from backend.services.sql_masking import SQLMaskingError, mask_sql_literals
    try:
        return mask_sql_literals(sql)
    except SQLMaskingError:
        return None
    except Exception:
        return None

def utf8_len(s: str) -> int:
    return len(s.encode("utf-8"))


def truncate_utf8(s: str, max_bytes: int) -> str:
    """按 UTF-8 字节截断（不切断多字节字符）。"""
    b = s.encode("utf-8")
    if len(b) <= max_bytes:
        return s
    return b[:max_bytes].decode("utf-8", errors="ignore")
