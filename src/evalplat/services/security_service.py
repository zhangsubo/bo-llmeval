"""安全加固业务逻辑（Phase 10）。

提供 URL 白名单校验和敏感数据脱敏。
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalplat.models.system_setting import SystemSetting

# API Key 常见模式：sk-xxx, key-xxx, Bearer xxx 等
_API_KEY_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9]{8,}|"
    r"key-[a-zA-Z0-9]{8,}|"
    r"[a-zA-Z0-9]{32,})",
    re.IGNORECASE,
)

# URL 白名单设置 key
WHITELIST_SETTING_KEY = "url_whitelist"


def validate_url_against_whitelist(db: Session, url: str) -> tuple[bool, str]:
    """从 system_settings 读取域名白名单并校验 URL。

    白名单存储格式：{"domains": ["api.openai.com", "*.anthropic.com"], "cidrs": ["10.0.0.0/8"]}

    Returns:
        (allowed, reason)
    """
    setting = db.scalar(
        select(SystemSetting).where(SystemSetting.key == WHITELIST_SETTING_KEY)
    )

    if not setting:
        # 未配置白名单 = 允许所有（但 SSRF 防护仍生效）
        return True, "未配置 URL 白名单，允许所有"

    whitelist = setting.value
    domains = whitelist.get("domains", [])

    if not domains:
        return True, "白名单为空，允许所有"

    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "URL 解析失败"

    hostname = parsed.hostname
    if not hostname:
        return False, "URL 缺少主机名"

    # 检查域名匹配（支持通配符 *.example.com）
    for allowed_domain in domains:
        if allowed_domain.startswith("*."):
            suffix = allowed_domain[1:]  # .example.com
            if hostname.endswith(suffix):
                return True, "OK"
        elif hostname == allowed_domain:
            return True, "OK"

    return False, f"域名 {hostname} 不在白名单中"


def mask_sensitive_data(text: str) -> str:
    """脱敏文本中的 API Key 模式。

    将匹配到的 API Key 替换为 ****掩码****。
    """
    if not text:
        return text

    def _replacer(match: re.Match) -> str:
        key = match.group(0)
        if len(key) <= 8:
            return "****"
        return key[:4] + "****" + key[-4:]

    return _API_KEY_PATTERN.sub(_replacer, text)
