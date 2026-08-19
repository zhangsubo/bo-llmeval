"""SSRF 防护工具（PRD §8.16.1）。

阻止向内网、云元数据服务和其他禁止地址发送请求。
校验时机：Base URL 保存、连接测试、任务预检和实际请求前（PRD §8.16.1）。
"""

import ipaddress
import socket
from urllib.parse import urlparse


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


class SSRFGuard:
    """URL 安全校验器：域名解析 + CIDR 匹配 + 永久禁止地址检测。"""

    def __init__(self, allowed_domains: list[str] | None = None, allowed_cidrs: list[str] | None = None):
        self._allowed_domains = set(allowed_domains or [])
        self._allowed_nets = [ipaddress.ip_network(c, strict=False) for c in (allowed_cidrs or [])]

    def check_url(self, url: str) -> tuple[bool, str]:
        """校验 URL 是否安全可访问。

        Returns:
            (allowed, reason): allowed=True 表示安全，reason 为拒绝原因或 "OK"
        """
        # 1. 解析 URL
        try:
            parsed = urlparse(url)
        except Exception:
            return False, "URL 解析失败"

        # 2. scheme 校验
        if parsed.scheme not in ("http", "https"):
            return False, f"不允许的协议: {parsed.scheme}，只允许 http/https"

        # 3. userinfo 校验（PRD §8.2.2：禁止嵌入用户名/密码）
        if parsed.username or parsed.password:
            return False, "URL 中不允许嵌入用户名/密码"

        # 4. 主机名不能为空
        hostname = parsed.hostname
        if not hostname:
            return False, "URL 缺少主机名"

        # 5. 域名白名单（如果配置了域名白名单）
        if self._allowed_domains and hostname not in self._allowed_domains:
            # 检查是否为子域名
            if not any(hostname.endswith(f".{d}") for d in self._allowed_domains):
                return False, f"域名 {hostname} 不在白名单中"

        # 6. DNS 解析
        try:
            infos = socket.getaddrinfo(hostname, None)
            ips = {info[4][0] for info in infos}
        except socket.gaierror:
            return False, f"DNS 解析失败: {hostname}"

        # 7. 校验每个 IP
        for ip_str in ips:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                return False, f"无效的 IP 地址: {ip_str}"

            # 永久禁止检查
            for blocked in PERMANENTLY_BLOCKED:
                if ip in blocked:
                    return False, f"IP {ip_str} 在永久禁止范围 {blocked}"

            # 私有地址检查（默认阻止，白名单可放行）
            is_private = any(ip in net for net in PRIVATE_DEFAULT_BLOCKED)
            if is_private:
                # 检查是否在 CIDR 白名单中
                if not any(ip in net for net in self._allowed_nets):
                    return False, f"IP {ip_str} 是私有地址且不在白名单中"

            # 非私有地址也需要在白名单中（如果配置了白名单）
            if self._allowed_nets and not is_private:
                if not any(ip in net for net in self._allowed_nets):
                    return False, f"IP {ip_str} 不在白名单中"

        return True, "OK"
