"""加密工具测试。"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("APP_MASTER_KEY", "S23qeuMUp7KWdXsoDV6X9-0T76tfE7JrEuDy8KMafA0=")

from evalplat.core.crypto import CryptoService


class TestCryptoService:
    def setup_method(self):
        self.crypto = CryptoService("S23qeuMUp7KWdXsoDV6X9-0T76tfE7JrEuDy8KMafA0=")

    def test_encrypt_decrypt_roundtrip(self):
        plaintext = "sk-abc123def456ghi789"
        ciphertext = self.crypto.encrypt(plaintext)
        assert isinstance(ciphertext, bytes)
        assert ciphertext != plaintext.encode()
        decrypted = self.crypto.decrypt(ciphertext)
        assert decrypted == plaintext

    def test_encrypt_unicode(self):
        plaintext = "测试密钥-🔑-abc"
        ciphertext = self.crypto.encrypt(plaintext)
        assert self.crypto.decrypt(ciphertext) == plaintext

    def test_mask_normal(self):
        assert self.crypto.mask("sk-abc123def456") == "sk-***456"

    def test_mask_short(self):
        assert self.crypto.mask("short") == "*****"

    def test_mask_empty(self):
        assert self.crypto.mask("") == ""

    def test_mask_exact_boundary(self):
        assert self.crypto.mask("abcdef") == "******"  # len == prefix + suffix → 全掩码

    def test_wrong_key_raises(self):
        from cryptography.fernet import Fernet, InvalidToken
        ciphertext = self.crypto.encrypt("test")
        # 用一个合法但不同的 Fernet 密钥
        wrong_key = Fernet.generate_key().decode()
        wrong_crypto = CryptoService(wrong_key)
        import pytest
        with pytest.raises(InvalidToken):
            wrong_crypto.decrypt(ciphertext)
