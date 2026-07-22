"""
TDSQL SQL审核工具 - 安全服务 (V2.0)

密码AES-256-Fernet加密/解密，密钥管理。

V2.0 密钥管理策略（银行生产要求）:
1. 首选: 环境变量 TDSQL_ENCRYPTION_KEY（由行内KMS/配置中心注入）
2. 次选: data/encryption.key 密钥文件（首次启动自动生成随机密钥，权限0600）
3. 兼容: V1.0 硬编码种子派生密钥仅用于解密历史数据（解密成功后应触发重新加密），
   不再用于任何新数据加密
"""
import base64
import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("tdsql.security")

# 尝试导入cryptography，不可用时降级为简单加密
try:
    from cryptography.fernet import Fernet, InvalidToken
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False
    logger.warning("cryptography库未安装，密码加密降级为简单模式")

# 密钥文件路径
_KEY_FILE = Path(__file__).parent.parent.parent / "data" / "encryption.key"

# V1.0 遗留种子（仅用于解密历史数据，禁止用于加密）
_LEGACY_SEED = "TDSQL-SQLCheck-2026-SecretKey"

# 进程内密钥缓存
_cached_key: Optional[bytes] = None


def _load_or_create_key() -> bytes:
    """
    获取加密密钥。

    优先级: TDSQL_ENCRYPTION_KEY 环境变量 > data/encryption.key 文件（自动生成）。
    """
    global _cached_key
    if _cached_key:
        return _cached_key

    env_key = os.getenv("TDSQL_ENCRYPTION_KEY", "").strip()
    if env_key:
        _cached_key = env_key.encode()
        return _cached_key

    # 密钥文件
    if _KEY_FILE.exists():
        key = _KEY_FILE.read_bytes().strip()
        if key:
            _cached_key = key
            return _cached_key

    # 首次启动：生成随机密钥并落盘（权限0600）
    key = Fernet.generate_key() if _HAS_CRYPTO else base64.urlsafe_b64encode(os.urandom(32))
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _KEY_FILE.write_bytes(key)
    try:
        os.chmod(_KEY_FILE, 0o600)
    except OSError:
        pass
    logger.warning(
        "未配置 TDSQL_ENCRYPTION_KEY，已自动生成密钥文件 %s。"
        "生产环境建议通过环境变量从KMS/配置中心注入密钥，并妥善备份该文件（丢失将无法解密已存连接密码）。",
        _KEY_FILE,
    )
    _cached_key = key
    return _cached_key


def _legacy_key() -> bytes:
    """V1.0 遗留密钥（仅用于解密历史数据）"""
    return base64.urlsafe_b64encode(hashlib.sha256(_LEGACY_SEED.encode()).digest())


def reset_key_cache():
    """清空进程内密钥缓存（供测试和密钥轮换使用）"""
    global _cached_key
    _cached_key = None


def encrypt_password(password: str) -> str:
    """加密密码"""
    if not password:
        return ""
    if _HAS_CRYPTO:
        try:
            f = Fernet(_load_or_create_key())
            return f.encrypt(password.encode()).decode()
        except Exception as e:
            logger.warning(f"Fernet加密失败，降级: {e}")
    # 降级：base64编码
    return base64.b64encode(password.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    """解密密码（优先当前密钥，回退V1.0遗留密钥，若非密文或解密失败则认为本身即为明文密码原样返回）"""
    if not encrypted:
        return ""
    if _HAS_CRYPTO:
        try:
            f = Fernet(_load_or_create_key())
            return f.decrypt(encrypted.encode()).decode()
        except (InvalidToken, Exception):
            # 兼容: 尝试V1.0遗留密钥解密历史数据
            try:
                f_legacy = Fernet(_legacy_key())
                plain = f_legacy.decrypt(encrypted.encode()).decode()
                logger.warning("检测到V1.0遗留密钥加密的数据，建议重新保存该连接配置以完成密钥升级")
                return plain
            except Exception:
                pass

    # 尝试 base64 解码（只对确实是 base64 且解码后为可打印字符的串生效）
    try:
        raw_bytes = base64.b64decode(encrypted.encode(), validate=True)
        decoded_str = raw_bytes.decode('utf-8')
        if decoded_str and all(c.isprintable() or c in '\r\n\t' for c in decoded_str):
            return decoded_str
    except Exception:
        pass

    # 兜底：若既不是 Fernet 密文也不是 Base64 密文，说明其本身就是明文密码！原样返回！
    return encrypted


class SecurityService:
    """安全服务"""

    @staticmethod
    def encrypt(password: str) -> str:
        return encrypt_password(password)

    @staticmethod
    def decrypt(encrypted: str) -> str:
        return decrypt_password(encrypted)

    @staticmethod
    def mask_password(password: str) -> str:
        """脱敏密码"""
        if not password:
            return ""
        if len(password) == 1:
            return "*"
        if len(password) == 2:
            return password[0] + "*"
        return password[0] + "*" * (len(password) - 2) + password[-1]
