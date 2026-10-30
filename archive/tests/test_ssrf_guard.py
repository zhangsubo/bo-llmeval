"""SSRF 防护工具测试。"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("APP_MASTER_KEY", "test")

from unittest.mock import patch

import pytest

from evalplat.core.ssrf import ResolvedTarget, SSRFGuard, _PinnedSyncBackend


class TestSSRFGuard:
    def setup_method(self):
        self.guard = SSRFGuard()

    def test_loopback_rejected(self):
        allowed, reason = self.guard.check_url("http://127.0.0.1/api")
        assert not allowed
        assert "永久禁止" in reason

    def test_ipv6_loopback_rejected(self):
        allowed, reason = self.guard.check_url("http://[::1]/api")
        assert not allowed

    def test_cloud_metadata_rejected(self):
        allowed, reason = self.guard.check_url("http://169.254.169.254/latest/meta-data/")
        assert not allowed
        assert "永久禁止" in reason

    def test_link_local_rejected(self):
        allowed, reason = self.guard.check_url("http://169.254.1.1/api")
        assert not allowed

    def test_private_ip_default_rejected(self):
        allowed, reason = self.guard.check_url("http://192.168.1.1/api")
        assert not allowed
        assert "私有地址" in reason

    def test_private_ip_allowed_by_whitelist(self):
        guard = SSRFGuard(allowed_cidrs=["192.168.0.0/16"])
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.1.1", 80))]):
            allowed, reason = guard.check_url("http://192.168.1.1/api")
            assert allowed

    @pytest.mark.parametrize("special_ip", ["100.64.0.1", "192.0.2.1", "198.18.0.1", "240.0.0.1"])
    def test_non_global_special_use_ip_rejected(self, special_ip):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", (special_ip, 80))]):
            allowed, reason = self.guard.check_url("http://example.com/api")
        assert not allowed
        assert "白名单" in reason

    def test_non_http_scheme_rejected(self):
        allowed, reason = self.guard.check_url("ftp://example.com/file")
        assert not allowed
        assert "不允许的协议" in reason

    def test_file_scheme_rejected(self):
        allowed, reason = self.guard.check_url("file:///etc/passwd")
        assert not allowed

    def test_userinfo_rejected(self):
        allowed, reason = self.guard.check_url("http://user:pass@example.com/api")
        assert not allowed
        assert "用户名/密码" in reason

    def test_missing_hostname_rejected(self):
        allowed, reason = self.guard.check_url("http:///path")
        assert not allowed
        assert "主机名" in reason

    def test_normal_domain_passes(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 80))]):
            allowed, reason = self.guard.check_url("http://example.com/api")
            assert allowed
            assert reason == "OK"

    def test_dns_failure_rejected(self):
        import socket
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("DNS failure")):
            allowed, reason = self.guard.check_url("http://nonexistent.invalid/api")
            assert not allowed
            assert "DNS 解析失败" in reason

    def test_domain_whitelist(self):
        guard = SSRFGuard(allowed_domains=["supplier.openai.com"])
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 80))]):
            allowed, _ = guard.check_url("http://supplier.openai.com/api")
            assert allowed
            allowed2, _ = guard.check_url("http://evil.com/api")
            assert not allowed2

    def test_pinned_backend_fails_closed_on_target_mismatch(self):
        target = ResolvedTarget(
            url="https://example.com/api",
            hostname="example.com",
            port=443,
            addresses=("93.184.216.34",),
        )
        backend = _PinnedSyncBackend(target)
        with patch("socket.create_connection") as connect:
            with pytest.raises(OSError, match="目标与已校验"):
                backend.connect_tcp("attacker.example", 443)
        connect.assert_not_called()

    def test_pinned_backend_accepts_trailing_dot_without_re_resolving(self):
        target = ResolvedTarget(
            url="https://example.com./api",
            hostname="example.com",
            port=443,
            addresses=("93.184.216.34",),
        )
        backend = _PinnedSyncBackend(target)

        class FakeSocket:
            def setsockopt(self, *_args):
                pass

        with patch("socket.create_connection", return_value=FakeSocket()) as connect:
            with patch("evalplat.core.ssrf.SyncStream", return_value="stream"):
                assert backend.connect_tcp("example.com.", 443) == "stream"
        connect.assert_called_once_with(("93.184.216.34", 443), None, source_address=None)
