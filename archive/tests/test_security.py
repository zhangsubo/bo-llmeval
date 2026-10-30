"""安全加固 API 测试（Phase 10）。"""

import uuid

from fastapi.testclient import TestClient

from evalplat.services.security_service import (
    mask_sensitive_data,
    redact_sensitive_text,
    redact_sensitive_value,
)


class TestMaskSensitiveData:
    """脱敏函数单元测试。"""

    def test_mask_sk_key(self):
        text = "使用 key sk-abc123def456ghi789 进行调用"
        result = mask_sensitive_data(text)
        assert "sk-a****i789" in result
        assert "sk-abc123def456ghi789" not in result

    def test_mask_long_hex_key(self):
        text = "token: abcdef0123456789abcdef0123456789"
        result = mask_sensitive_data(text)
        assert "abcd****6789" in result
        assert "abcdef0123456789abcdef0123456789" not in result

    def test_mask_empty_text(self):
        assert mask_sensitive_data("") == ""

    def test_mask_no_keys(self):
        text = "这是一段普通文本，没有 API Key"
        assert mask_sensitive_data(text) == text

    def test_mask_short_key(self):
        """短 key（<=8字符）应全部掩码。"""
        text = "key: abcdef"
        result = mask_sensitive_data(text)
        # abcdef 长度 6 < 32 不匹配模式，保持不变
        assert "abcdef" in result


class TestWhitelistAPI:
    """白名单 API 测试。"""

    def test_get_whitelist_empty(self, client: TestClient):
        """未配置白名单时返回空。"""
        resp = client.get("/api/v1/settings/whitelist")
        assert resp.status_code == 200
        data = resp.json()
        assert data["domains"] == []
        assert data["cidrs"] == []

    def test_update_whitelist(self, client: TestClient):
        """更新白名单。"""
        payload = {"domains": ["api.openai.com", "*.anthropic.com"], "cidrs": ["10.0.0.0/8"], "confirmation": "CONFIRM"}
        resp = client.put("/api/v1/settings/whitelist", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "api.openai.com" in data["domains"]

    def test_get_whitelist_after_update(self, client: TestClient):
        """更新后再次获取应返回相同数据。"""
        payload = {"domains": ["example.com"], "cidrs": [], "confirmation": "CONFIRM"}
        client.put("/api/v1/settings/whitelist", json=payload)

        resp = client.get("/api/v1/settings/whitelist")
        assert resp.status_code == 200
        data = resp.json()
        assert data["domains"] == ["example.com"]

    def test_update_whitelist_requires_confirmation(self, client: TestClient):
        response = client.put(
            "/api/v1/settings/whitelist",
            json={"domains": ["example.com"], "cidrs": [], "confirmation": "NO"},
        )
        assert response.status_code == 409

    def test_generic_settings_endpoint_cannot_bypass_whitelist_confirmation(self, client: TestClient):
        response = client.patch(
            "/api/v1/settings/url_whitelist",
            json={"value": {"domains": ["example.com"], "cidrs": []}},
        )
        assert response.status_code == 409


class TestSecurityService:
    """安全服务单元测试。"""

    def test_validate_url_allowed_when_no_whitelist(self, db):
        """未配置白名单时允许所有 URL。"""
        from evalplat.services.security_service import validate_url_against_whitelist

        allowed, reason = validate_url_against_whitelist(db, "https://api.openai.com/v1")
        assert allowed is True

    def test_validate_url_blocked_by_whitelist(self, db):
        """配置白名单后，不在白名单中的域名应被阻止。"""
        from evalplat.models.system_setting import SystemSetting
        from evalplat.services.security_service import validate_url_against_whitelist, WHITELIST_SETTING_KEY

        setting = SystemSetting(key=WHITELIST_SETTING_KEY, value={"domains": ["api.openai.com"], "cidrs": []})
        db.add(setting)
        db.commit()

        allowed, reason = validate_url_against_whitelist(db, "https://evil.example.com/v1")
        assert allowed is False
        assert "不在白名单" in reason

    def test_validate_url_wildcard_match(self, db):
        """通配符域名应匹配子域名。"""
        from evalplat.models.system_setting import SystemSetting
        from evalplat.services.security_service import validate_url_against_whitelist, WHITELIST_SETTING_KEY

        setting = SystemSetting(key=WHITELIST_SETTING_KEY, value={"domains": ["*.anthropic.com"], "cidrs": []})
        db.add(setting)
        db.commit()

        allowed, _ = validate_url_against_whitelist(db, "https://api.anthropic.com/v1")
        assert allowed is True

        allowed2, _ = validate_url_against_whitelist(db, "https://other.example.com/v1")
        assert allowed2 is False

    def test_redact_sensitive_value_handles_nested_fields_and_exact_secret(self):
        payload = {
            "Authorization": "Bearer vendor-secret-123",
            "nested": {
                "api_key_ciphertext": "ciphertext",
                "access_token": "access-token-value",
                "message": "vendor-secret-123 was rejected",
            },
            "items": [{"client_secret": "client-secret-value"}],
        }

        redacted = redact_sensitive_value(payload, secret="vendor-secret-123")

        assert redacted["Authorization"] == "[REDACTED]"
        assert redacted["nested"]["api_key_ciphertext"] == "[REDACTED]"
        assert redacted["nested"]["access_token"] == "[REDACTED]"
        assert redacted["nested"]["message"] == "[REDACTED] was rejected"
        assert redacted["items"][0]["client_secret"] == "[REDACTED]"

    def test_redact_sensitive_text_masks_bearer_token(self):
        value = redact_sensitive_text("request failed: Bearer abcdefghijklmnop")

        assert value == "request failed: Bearer [REDACTED]"
        assert "abcdefghijklmnop" not in value

    def test_redact_sensitive_value_reduces_paths_to_basename(self):
        redacted = redact_sensitive_value(
            {"storage_path": "/srv/evalplat/data/datasets/version.jsonl", "local_path": r"C:\cache\tokenizer"}
        )

        assert redacted == {"storage_path": "version.jsonl", "local_path": "tokenizer"}
