"""SSRF 防护工具（PRD §8.16.1）。

阻止向内网、云元数据服务和其他禁止地址发送请求。
校验时机：Base URL 保存、连接测试、任务预检和实际请求前（PRD §8.16.1）。
"""

import ipaddress
import socket
from dataclasses import dataclass
from collections.abc import Iterable
from urllib.parse import urlparse

import httpcore
import httpx
from httpcore._backends.base import (
    AsyncNetworkBackend,
    AsyncNetworkStream,
    SOCKET_OPTION,
)
from httpcore._backends.auto import AutoBackend
from httpcore._backends.sync import SyncBackend, SyncStream


# 永久禁止网段（不可通过白名单放行）
PERMANENTLY_BLOCKED: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("127.0.0.0/8"),        # loopback
    ipaddress.ip_network("::1/128"),              # loopback IPv6
    ipaddress.ip_network("169.254.0.0/16"),       # link-local
    ipaddress.ip_network("fe80::/10"),            # link-local IPv6
    ipaddress.ip_network("169.254.169.254/32"),   # cloud metadata (AWS/GCP/Azure)
    ipaddress.ip_network("fd00:ec2::254/128"),    # cloud metadata AWS IPv6
    ipaddress.ip_network("0.0.0.0/8"),           # unspecified
    ipaddress.ip_network("::/128"),               # unspecified IPv6
    ipaddress.ip_network("224.0.0.0/4"),          # multicast
    ipaddress.ip_network("ff00::/8"),             # multicast IPv6
    ipaddress.ip_network("255.255.255.255/32"),   # broadcast
]

# 默认阻止的私有地址（白名单可放行）
PRIVATE_DEFAULT_BLOCKED: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
]


@dataclass(frozen=True)
class ResolvedTarget:
    """通过 SSRF 校验后可复用的 DNS 解析结果。

    ``addresses`` 只包含已经检查过的 IP。请求传输层复用该集合，避免
    校验阶段和连接阶段发生两次 DNS 解析而被 DNS rebinding 绕过。
    """

    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


class _PinnedSyncBackend(SyncBackend):
    """httpcore backend that dials only the addresses from one DNS result."""

    def __init__(self, target: ResolvedTarget) -> None:
        self._target = target

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> SyncStream:
        # httpcore keeps the original host for TLS SNI and HTTP Host. Only the
        # socket destination is replaced with a previously validated address.
        if host.lower().rstrip(".") != self._target.hostname or port != self._target.port:
            # Never fall back to resolving an unexpected host here: that would
            # undo the DNS pinning performed by SSRFGuard.
            raise OSError("请求目标与已校验的 SSRF 目标不一致")
        addresses = self._target.addresses
        last_error: OSError | None = None
        for address in addresses:
            try:
                sock = socket.create_connection(
                    (address, port),
                    timeout,
                    source_address=None if local_address is None else (local_address, 0),
                )
                for option in socket_options or ():
                    sock.setsockopt(*option)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                return SyncStream(sock)
            except OSError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError(f"无法连接到 {self._target.hostname}")


class _PinnedAsyncBackend(AsyncNetworkBackend):
    """Async counterpart of ``_PinnedSyncBackend``.

    The delegate still performs TLS and socket setup, but receives a validated
    literal IP instead of the original hostname. HTTPCore keeps the original
    request host for HTTP Host/SNI, so certificate and virtual-host semantics
    remain intact while DNS cannot be re-resolved after the SSRF check.
    """

    def __init__(self, target: ResolvedTarget, delegate: AsyncNetworkBackend | None = None) -> None:
        self._target = target
        self._delegate = delegate or AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        if host.lower().rstrip(".") != self._target.hostname or port != self._target.port:
            raise OSError("请求目标与已校验的 SSRF 目标不一致")
        last_error: Exception | None = None
        for address in self._target.addresses:
            try:
                return await self._delegate.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:  # connection backends normalize their own error types
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError(f"无法连接到 {self._target.hostname}")


class PinnedHTTPTransport(httpx.HTTPTransport):
    """httpx transport that preserves hostname semantics while pinning IP."""

    def __init__(self, target: ResolvedTarget, *, verify: bool = True) -> None:
        # trust_env=False prevents an ambient proxy from changing the network
        # boundary after SSRF validation.
        super().__init__(verify=verify, trust_env=False, retries=0)
        pool = self._pool
        if not isinstance(pool, httpcore.ConnectionPool):
            raise RuntimeError("DNS pinned transport does not support HTTP proxy pools")
        pool._network_backend = _PinnedSyncBackend(target)


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """Async httpx transport pinned to one already validated DNS result."""

    def __init__(self, target: ResolvedTarget, *, verify: bool = True) -> None:
        super().__init__(verify=verify, trust_env=False, retries=0)
        pool = self._pool
        if not isinstance(pool, httpcore.AsyncConnectionPool):
            raise RuntimeError("DNS pinned async transport does not support HTTP proxy pools")
        delegate = pool._network_backend
        pool._network_backend = _PinnedAsyncBackend(target, delegate)


class SSRFGuard:
    """URL 安全校验器：域名解析 + CIDR 匹配 + 永久禁止地址检测。"""

    def __init__(self, allowed_domains: list[str] | None = None, allowed_cidrs: list[str] | None = None):
        self._allowed_domains = {
            str(domain).strip().lower().lstrip("*.")
            for domain in (allowed_domains or [])
            if str(domain).strip()
        }
        self._allowed_nets = [ipaddress.ip_network(c, strict=False) for c in (allowed_cidrs or [])]

    def check_url(self, url: str) -> tuple[bool, str]:
        """校验 URL 是否安全可访问。

        Returns:
            (allowed, reason): allowed=True 表示安全，reason 为拒绝原因或 "OK"
        """
        allowed, reason, _target = self.resolve_url(url)
        return allowed, reason

    def resolve_url(self, url: str) -> tuple[bool, str, ResolvedTarget | None]:
        """校验 URL 并返回可供请求层复用的固定解析结果。"""

        # 1. 解析 URL
        try:
            parsed = urlparse(url)
        except Exception:
            return False, "URL 解析失败", None

        # 2. scheme 校验
        if parsed.scheme not in ("http", "https"):
            return False, f"不允许的协议: {parsed.scheme}，只允许 http/https", None

        # 3. userinfo 校验（PRD §8.2.2：禁止嵌入用户名/密码）
        if parsed.username or parsed.password:
            return False, "URL 中不允许嵌入用户名/密码", None

        # 4. 主机名不能为空
        hostname = parsed.hostname
        if not hostname:
            return False, "URL 缺少主机名", None
        hostname = hostname.lower().rstrip(".")

        # 5. 域名白名单（如果配置了域名白名单）
        if self._allowed_domains and hostname not in self._allowed_domains:
            # 检查是否为严格子域名，避免 evil-example.com 匹配 example.com。
            if not any(hostname.endswith(f".{domain}") for domain in self._allowed_domains):
                return False, f"域名 {hostname} 不在白名单中", None

        # 6. DNS 解析
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            ips = tuple(dict.fromkeys(info[4][0] for info in infos))
        except socket.gaierror:
            return False, f"DNS 解析失败: {hostname}", None
        except (TypeError, ValueError):
            return False, "URL 端口无效", None

        if not ips:
            return False, f"DNS 解析无地址: {hostname}", None

        # 7. 校验每个 IP
        for ip_str in ips:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                return False, f"无效的 IP 地址: {ip_str}", None

            # Treat IPv4-mapped IPv6 literals as their IPv4 address. Otherwise
            # ::ffff:127.0.0.1 would bypass the IPv4 loopback/private ranges.
            effective_ip = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip

            # 永久禁止检查
            for blocked in PERMANENTLY_BLOCKED:
                if effective_ip.version == blocked.version and effective_ip in blocked:
                    return False, f"IP {ip_str} 在永久禁止范围 {blocked}", None

            in_allowed_cidr = any(
                effective_ip.version == net.version and effective_ip in net
                for net in self._allowed_nets
            )
            # ``is_private`` does not cover every non-routable/special-use
            # range (CGNAT, TEST-NET, benchmarking, reserved, etc.).  The
            # platform has no reason to contact those ranges by default, so
            # reject every non-global address unless an explicit CIDR allows it.
            if not effective_ip.is_global and not in_allowed_cidr:
                if effective_ip.is_private:
                    return False, f"IP {ip_str} 是私有地址且不在白名单中", None
                return False, f"IP {ip_str} 是非公网特殊地址且不在白名单中", None

            # When a CIDR allowlist is configured, public addresses must also
            # be explicitly listed.  Permanent blocks above remain absolute.
            if self._allowed_nets and not in_allowed_cidr:
                return False, f"IP {ip_str} 不在白名单中", None

        return True, "OK", ResolvedTarget(
            url=url,
            hostname=hostname,
            port=port,
            addresses=tuple(ips),
        )

    def transport(self, url: str, *, verify: bool = True) -> PinnedHTTPTransport:
        """为已校验 URL 创建一次性 DNS pinned transport。"""

        allowed, reason, target = self.resolve_url(url)
        if not allowed or target is None:
            raise ValueError(f"SSRF 校验失败: {reason}")
        return self.transport_for(target, verify=verify)

    @staticmethod
    def transport_for(target: ResolvedTarget, *, verify: bool = True) -> PinnedHTTPTransport:
        """使用既有解析结果创建 transport，不触发第二次 DNS 查询。"""

        return PinnedHTTPTransport(target, verify=verify)

    @staticmethod
    def async_transport_for(target: ResolvedTarget, *, verify: bool = True) -> PinnedAsyncHTTPTransport:
        """Create an async transport from an existing validated target."""

        return PinnedAsyncHTTPTransport(target, verify=verify)
