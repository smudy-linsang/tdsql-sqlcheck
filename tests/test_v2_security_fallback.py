"""
测试密码解密明文降级与 API 400 友好容错防御
"""
import pytest
from backend.services.security_service import decrypt_password, encrypt_password

def test_decrypt_plaintext_password_fallback():
    # 测试明文密码（非 Fernet 密文格式）
    plain_pw = "checksql123"
    decrypted = decrypt_password(plain_pw)
    assert decrypted == plain_pw, "针对明文密码解密时不能抹空，必须原样返回"

    # 测试标准加密与解密
    encrypted = encrypt_password("Admin@1234")
    assert decrypt_password(encrypted) == "Admin@1234", "标准加密后解密必须成功还原"

def test_empty_password_handling():
    assert decrypt_password("") == ""
    assert decrypt_password(None) == ""
