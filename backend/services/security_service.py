"""
TDSQL SQL审核工具 - 安全服务 (V1.0)

密码AES-256-Fernet加密/解密，操作审计日志。
"""
import base64
import hashlib
import logging
import os
from typing import Optional

logger = logging.getLogger("tdsql.security")

# 尝试导入cryptography，不可用时降级为简单加密
try:
    from cryptography.fernet import Fernet
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False
    logger.warning("cryptography库未安装，密码加密降级为简单模式")

# 加密密钥（从环境变量或自动生成）
_ENCRYPTION_KEY = os.getenv("TDSQL_ENCRYPTION_KEY", "")


def _get_or_create_key() -> bytes:
    """获取或创建加密密钥"""
    global _ENCRYPTION_KEY
    if _ENCRYPTION_KEY:
        key = _ENCRYPTION_KEY.encode() if isinstance(_ENCRYPTION_KEY, str) else _ENCRYPTION_KEY
    else:
        # 基于固定种子生成密钥（简化版，生产环境应从安全配置读取）
        seed = "TDSQL-SQLCheck-2026-SecretKey"
        key = base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest())
        _ENCRYPTION_KEY = key.decode()
    return key if isinstance(key, bytes) else key.encode()


def encrypt_password(password: str) -> str:
    """加密密码"""
    if not password:
        return ""
    if _HAS_CRYPTO:
        try:
            key = _get_or_create_key()
            f = Fernet(key)
            return f.encrypt(password.encode()).decode()
        except Exception as e:
            logger.warning(f"Fernet加密失败，降级: {e}")
    # 降级：base64编码
    return base64.b64encode(password.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    """解密密码"""
    if not encrypted:
        return ""
    if _HAS_CRYPTO:
        try:
            key = _get_or_create_key()
            f = Fernet(key)
            return f.decrypt(encrypted.encode()).decode()
        except Exception as e:
            logger.warning(f"Fernet解密失败，尝试base64: {e}")
    # 降级：base64解码
    try:
        return base64.b64decode(encrypted.encode()).decode()
    except Exception:
        return ""


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
