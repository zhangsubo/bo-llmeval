"""API Key 加密工具（PRD §11.2）。

使用 cryptography 库的 Fernet 对称加密。Fernet 是 AES-128-CBC + HMAC-SHA256
的组合，提供加密 + 完整性校验（RESEARCH.md Pattern 2）。

主密钥来自 Settings.app_master_key（.env 注入），base64 编码的 32 字节随机密钥。
生成方式：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


class CryptoService:
    """Fernet 加密/解密/掩码封装。"""

    def __init__(self, master_key: str):
        self._fernet = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)

    def encrypt(self, plaintext: str) -> bytes:
        """加密明文，返回 bytes（含 timestamp + HMAC）。"""
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        """解密密文，返回明文。InvalidToken 表示密钥错误或数据损坏。"""
        return self._fernet.decrypt(ciphertext).decode("utf-8")

    @staticmethod
    def mask(key: str, prefix_len: int = 3, suffix_len: int = 3) -> str:
        """生成 API Key 掩码：保留前 N 后 M 字符，中间用 *** 替代。

        >>> CryptoService.mask("sk-abc123def456")
        'sk-***456'
        >>> CryptoService.mask("short")
        '*****'
        """
        if not key:
            return ""
        if len(key) <= prefix_len + suffix_len:
            return "*" * len(key)
        return key[:prefix_len] + "***" + key[-suffix_len:]


@lru_cache
def get_crypto_service() -> CryptoService:
    """从 Settings 读取主密钥，返回 CryptoService 单例。"""
    from evalplat.config import get_settings
    return CryptoService(get_settings().app_master_key)
