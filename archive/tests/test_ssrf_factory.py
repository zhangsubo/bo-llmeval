"""统一 SSRF 配置和 DNS pinning 的边界测试。"""

import socket
from unittest.mock import patch

from evalplat.core.ssrf import PinnedAsyncHTTPTransport, PinnedHTTPTransport, SSRFGuard
from evalplat.models.system_setting import SystemSetting
from evalplat.services.security_service import build_ssrf_guard


def test_build_ssrf_guard_reads_system_whitelist(db):
    db.add(
        SystemSetting(
            key="url_whitelist",
            value={"domains": ["*.Example.com"], "cidrs": ["203.0.113.0/24"]},
        )
    )
    db.commit()

    guard = build_ssrf_guard(db)
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 443))],
    ):
        allowed, reason = guard.check_url("https://api.example.com/v1")
    assert allowed is True
    assert reason == "OK"


def test_resolved_target_is_reused_by_pinned_transport(monkeypatch):
    guard = SSRFGuard()
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    ):
        allowed, reason, target = guard.resolve_url("https://example.com/v1")

    assert allowed is True
    assert reason == "OK"
    assert target is not None
    assert target.addresses == ("93.184.216.34",)

    calls = []

    class FakeSocket:
        def setsockopt(self, *_args):
            pass

        def getsockname(self):
            return ("127.0.0.1", 1234)

        def getpeername(self):
            return ("93.184.216.34", 443)

        def settimeout(self, *_args):
            pass

        def send(self, value):
            return len(value)

        def recv(self, *_args):
            return b""

        def close(self):
            pass

    def fake_create_connection(address, timeout, source_address=None):
        calls.append((address, timeout, source_address))
        return FakeSocket()

    monkeypatch.setattr("socket.create_connection", fake_create_connection)
    transport = PinnedHTTPTransport(target, verify=False)
    try:
        transport._pool._network_backend.connect_tcp("example.com", 443, timeout=2)
    finally:
        transport.close()

    assert calls == [(('93.184.216.34', 443), 2, None)]


def test_ipv4_mapped_ipv6_loopback_is_blocked():
    guard = SSRFGuard()
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:127.0.0.1", 80, 0, 0))],
    ):
        allowed, reason = guard.check_url("http://localhost")
    assert allowed is False
    assert "永久禁止" in reason


def test_async_pinned_transport_delegates_only_validated_addresses():
    guard = SSRFGuard()
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    ):
        allowed, reason, target = guard.resolve_url("https://example.com/v1")
    assert allowed and reason == "OK" and target is not None

    class Delegate:
        calls = []

        async def connect_tcp(self, host, port, **kwargs):
            self.calls.append((host, port, kwargs))
            raise OSError("stop after observing destination")

    transport = PinnedAsyncHTTPTransport(target, verify=False)
    try:
        backend = transport._pool._network_backend
        delegate = Delegate()
        backend._delegate = delegate
        import asyncio

        asyncio.run(backend.connect_tcp("example.com", 443, timeout=2))
    except OSError as exc:
        assert "stop" in str(exc)
    finally:
        asyncio.run(transport.aclose())
    assert delegate.calls[0][0] == "93.184.216.34"
