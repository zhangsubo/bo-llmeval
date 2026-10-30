"""安全加固业务逻辑（Phase 10）。

提供 URL 白名单校验和敏感数据脱敏。
"""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalplat.core.ssrf import SSRFGuard
from evalplat.core.paths import public_basename
from evalplat.models.system_setting import SystemSetting

# API Key 常见模式：sk-xxx, key-xxx, Bearer xxx 等
_API_KEY_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9._-]{8,}|"
    r"key-[a-zA-Z0-9._-]{8,}|"
    r"[a-zA-Z0-9]{32,})",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"\bBearer\s+[^\s,;\"']{4,}", re.IGNORECASE)
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "api_key_ciphertext",
        "x_api_key",
        "authorization",
        "proxy_authorization",
        "access_token",
        "refresh_token",
        "auth_token",
        "bearer_token",
        "token",
        "password",
        "secret",
        "client_secret",
        "cookie",
        "set_cookie",
    }
)
_PATH_FIELD_NAMES = frozenset(
    {
        "path",
        "local_path",
        "storage_path",
        "report_path",
        "summary_path",
        "details_path",
        "report_files",
        "page_paths",
    }
)

# URL 白名单设置 key
WHITELIST_SETTING_KEY = "url_whitelist"


def build_ssrf_guard(db: Session) -> SSRFGuard:
    """从系统设置构造统一 SSRF 校验器。

    所有会访问供应商地址的边界都应调用此工厂，避免管理 API、连接测试
    和 Worker 各自持有不同的白名单快照。设置不存在时仍保留默认私网阻断。
    """

    setting = db.scalar(
        select(SystemSetting).where(SystemSetting.key == WHITELIST_SETTING_KEY)
    )
    value = setting.value if setting and isinstance(setting.value, dict) else {}
    domains = value.get("domains", [])
    cidrs = value.get("cidrs", [])
    if not isinstance(domains, list) or not all(isinstance(item, str) for item in domains):
        raise ValueError("URL 白名单 domains 必须为字符串数组")
    if not isinstance(cidrs, list) or not all(isinstance(item, str) for item in cidrs):
        raise ValueError("URL 白名单 cidrs 必须为字符串数组")
    return SSRFGuard(allowed_domains=domains, allowed_cidrs=cidrs)


def validate_url_against_whitelist(db: Session, url: str) -> tuple[bool, str]:
    """从 system_settings 读取域名白名单并校验 URL。

    白名单存储格式：{"domains": ["api.openai.com", "*.anthropic.com"], "cidrs": ["10.0.0.0/8"]}

    Returns:
        (allowed, reason)
    """
    try:
        return build_ssrf_guard(db).check_url(url)
    except ValueError as exc:
        return False, f"URL 白名单配置无效: {exc}"


def mask_sensitive_data(text: str) -> str:
    """脱敏文本中的 API Key 模式。

    将匹配到的 API Key 替换为 ****掩码****。
    """
    if not text:
        return text

    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)

    def _replacer(match: re.Match) -> str:
        key = match.group(0)
        if len(key) <= 8:
            return "****"
        return key[:4] + "****" + key[-4:]

    return _API_KEY_PATTERN.sub(_replacer, text)


def redact_sensitive_text(value: object, secret: str | None = None) -> str:
    """Redact one untrusted value, including a known non-standard secret."""

    text = "" if value is None else str(value)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    return mask_sensitive_data(text)


def redact_sensitive_value(value: Any, secret: str | None = None) -> Any:
    """Recursively redact credentials from JSON-compatible evidence."""

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            output_key = str(key)
            normalized = re.sub(r"[^a-z0-9]+", "_", output_key.lower()).strip("_")
            redacted[output_key] = (
                "[REDACTED]"
                if normalized in _SENSITIVE_FIELD_NAMES
                else _redact_path(item, secret)
                if normalized in _PATH_FIELD_NAMES
                else redact_sensitive_value(item, secret)
            )
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_sensitive_value(item, secret) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value, secret)
    return value


def _redact_path(value: Any, secret: str | None = None) -> Any:
    """Expose only a safe basename for filesystem path fields."""

    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [
            public_basename(item) if isinstance(item, str) else redact_sensitive_value(item, secret)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            str(key): public_basename(item) if isinstance(item, str) else redact_sensitive_value(item, secret)
            for key, item in value.items()
        }
    if not isinstance(value, str):
        return redact_sensitive_value(value, secret)
    text = redact_sensitive_text(value, secret)
    return public_basename(text)
