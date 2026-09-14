# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：Copilot 严格加密封装（CP-W03，DETAIL §9.3）。

新密钥环独立于既有 security_service（后者存在 base64/明文历史兼容降级路径，
不直接用于新模型密钥）。本模块失败关闭：密钥缺失/非法/解密失败一律抛
CryptoUnavailableError（COPILOT_CRYPTO_UNAVAILABLE），不做任何明文回落。

封套：{"v":1, "kid":..., "nonce_b64":..., "ciphertext_b64":...}
AAD：紧凑 JSON 数组 [table, primary_key, field, owner_subject_id 或固定 SYSTEM,
crypto_revision] 的 UTF-8 字节；12 字节随机 nonce，禁止复用。

keyring 文件形状：
  {"schema_version":1, "active_kid":"key-YYYYMM",
   "keys": {"<kid>": "<base64 的 32 字节 AES 密钥>"}}
校验：kid 格式 [A-Za-z0-9_-]{1,64}、解码长度 32、active 存在、key 总数 ≤4、
文件 ≤8KiB、无重复 JSON 键。文件权限 Linux 0600 / Windows 受限 ACL；
环境只传文件路径（COPILOT_KEYRING_FILE），不在 system_config 存密钥。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("tdsql.copilot.crypto")

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _HAS_AESGCM = True
except ImportError:  # pragma: no cover - cryptography 是既有硬依赖
    _HAS_AESGCM = False

_KEYRING_MAX_BYTES = 8 * 1024
_KID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NONCE_BYTES = 12


class CryptoUnavailableError(RuntimeError):
    """加密组件不可用（密钥缺失/非法/解密失败），失败关闭。"""


def _reject_duplicate_keys(pairs):
    seen = set()
    out = {}
    for k, v in pairs:
        if k in seen:
            raise CryptoUnavailableError("keyring 存在重复 JSON 键")
        seen.add(k)
        out[k] = v
    return out


class Keyring:
    """密钥环：加载即校验，任何非法即失败关闭。"""

    def __init__(self, path: str):
        self.path = path
        self._keys: dict[str, bytes] = {}
        self._active_kid: str = ""
        self._load()

    def _load(self) -> None:
        if not _HAS_AESGCM:
            raise CryptoUnavailableError("cryptography AESGCM 不可用")
        p = Path(self.path)
        if not p.exists():
            raise CryptoUnavailableError(f"keyring 文件不存在: {self.path}")
        raw = p.read_bytes()
        if len(raw) > _KEYRING_MAX_BYTES:
            raise CryptoUnavailableError("keyring 文件超过 8KiB 上限")
        try:
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        except CryptoUnavailableError:
            raise
        except Exception as e:
            raise CryptoUnavailableError(f"keyring 文件解析失败: {type(e).__name__}")
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise CryptoUnavailableError("keyring schema_version 必须为 1")
        active = data.get("active_kid")
        keys = data.get("keys")
        if not isinstance(active, str) or not _KID_RE.match(active):
            raise CryptoUnavailableError("keyring active_kid 非法")
        if not isinstance(keys, dict) or not keys or len(keys) > 4:
            raise CryptoUnavailableError("keyring keys 数量非法（1—4）")
        for kid, b64 in keys.items():
            if not isinstance(kid, str) or not _KID_RE.match(kid):
                raise CryptoUnavailableError(f"keyring kid 非法: {kid!r}")
            try:
                key = base64.b64decode(b64, validate=True)
            except Exception:
                raise CryptoUnavailableError(f"keyring 密钥 base64 非法: {kid}")
            if len(key) != 32:
                raise CryptoUnavailableError(f"keyring 密钥长度必须为 32 字节: {kid}")
            self._keys[kid] = key
        if active not in self._keys:
            raise CryptoUnavailableError("keyring active_kid 不在 keys 中")
        self._active_kid = active

    @property
    def active_kid(self) -> str:
        return self._active_kid

    def key(self, kid: str) -> bytes:
        k = self._keys.get(kid)
        if k is None:
            raise CryptoUnavailableError(f"keyring 中不存在 kid: {kid}")
        return k


_keyring_cache: Optional[Keyring] = None
_keyring_path_cache: str = ""


def reset_keyring_cache() -> None:
    """清空进程内 keyring 缓存（测试/轮换用）。"""
    global _keyring_cache, _keyring_path_cache
    _keyring_cache = None
    _keyring_path_cache = ""


def load_keyring(path: Optional[str] = None) -> Keyring:
    """加载 keyring（进程内缓存；路径变化即重载）。"""
    global _keyring_cache, _keyring_path_cache
    p = path or os.getenv("COPILOT_KEYRING_FILE", "")
    if not p:
        raise CryptoUnavailableError("未配置 COPILOT_KEYRING_FILE")
    if _keyring_cache is not None and _keyring_path_cache == p:
        return _keyring_cache
    kr = Keyring(p)
    _keyring_cache = kr
    _keyring_path_cache = p
    return kr


def crypto_available() -> bool:
    """探测加密组件是否可用（不抛异常）。"""
    try:
        load_keyring()
        return True
    except Exception:
        return False


def make_aad(table: str, primary_key: str, field: str, owner: str,
             crypto_revision: int = 1) -> bytes:
    """AAD：紧凑 JSON 数组 UTF-8 字节（禁止歧义分隔符拼接）。"""
    arr = [table, primary_key, field, owner or "SYSTEM", crypto_revision]
    return json.dumps(arr, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def encrypt(plaintext: str, table: str, primary_key: str, field: str,
            owner: str = "SYSTEM", crypto_revision: int = 1,
            keyring: Optional[Keyring] = None) -> str:
    """AES-256-GCM 加密为 JSON 封套字符串。"""
    kr = keyring or load_keyring()
    nonce = os.urandom(_NONCE_BYTES)
    aad = make_aad(table, primary_key, field, owner, crypto_revision)
    ct = AESGCM(kr.key(kr.active_kid)).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return json.dumps({
        "v": 1, "kid": kr.active_kid,
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(ct).decode("ascii"),
    }, separators=(",", ":"))


def decrypt(envelope: str, table: str, primary_key: str, field: str,
            owner: str = "SYSTEM", crypto_revision: int = 1,
            keyring: Optional[Keyring] = None) -> str:
    """解密封套；AAD 不符/kid 缺失/密文损坏一律失败关闭。"""
    kr = keyring or load_keyring()
    try:
        data = json.loads(envelope)
        if not isinstance(data, dict) or data.get("v") != 1:
            raise ValueError("bad envelope version")
        kid = data["kid"]
        nonce = base64.b64decode(data["nonce_b64"], validate=True)
        ct = base64.b64decode(data["ciphertext_b64"], validate=True)
        if len(nonce) != _NONCE_BYTES:
            raise ValueError("bad nonce length")
    except CryptoUnavailableError:
        raise
    except Exception:
        raise CryptoUnavailableError("封套格式非法")
    aad = make_aad(table, primary_key, field, owner, crypto_revision)
    try:
        pt = AESGCM(kr.key(kid)).decrypt(nonce, ct, aad)
    except CryptoUnavailableError:
        raise
    except Exception:
        # AAD 跨行置换、密文损坏、kid 错误统一失败关闭
        raise CryptoUnavailableError("封套解密失败（AAD/密文/kid 不匹配）")
    return pt.decode("utf-8")


def envelope_bytes(*envelopes: str) -> int:
    """封套总字节数（容量按密文实际字节计）。"""
    return sum(len(e.encode("utf-8")) for e in envelopes)


def hmac_digest(purpose: str, text: str, keyring: Optional[Keyring] = None) -> str:
    """keyring 独立 purpose HMAC（input_hash 等；不记录敏感原文裸 hash）。"""
    kr = keyring or load_keyring()
    key = hmac.new(kr.key(kr.active_kid), purpose.encode("utf-8"),
                   hashlib.sha256).digest()
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()
