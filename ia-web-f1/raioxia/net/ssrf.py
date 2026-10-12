"""SSRF guard: every URL we fetch comes (directly or via redirects) from the user.

Rules: http/https only, ports 80/443 only, no credentials in the URL, and every
address the host resolves to must be public. The fetcher then connects to the
exact IP validated here, so a second DNS answer (rebinding) is never used.
"""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Protocol

import httpx

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443}

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Prefixes that embed an IPv4 address the packet may end up at.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")


class UnsafeURLError(Exception):
    """The URL or its resolved address is not allowed. `reason` is a stable code."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Target:
    url: httpx.URL
    scheme: str
    host: str  # ASCII (IDNA-encoded) hostname or IP literal, no brackets
    port: int


class Resolver(Protocol):
    async def resolve(self, host: str, port: int) -> list[str]: ...


class SystemResolver:
    async def resolve(self, host: str, port: int) -> list[str]:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        # Keep resolver order, drop duplicates (one entry per socket family/proto).
        return list(dict.fromkeys(info[4][0] for info in infos))


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped:
        return ip.ipv4_mapped
    if ip.sixtofour:
        return ip.sixtofour
    if ip.teredo:
        return ip.teredo[1]
    if ip in _NAT64 or ip in _NAT64_LOCAL:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def is_public_ip(ip: IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.scope_id:
            return False
        embedded = _embedded_ipv4(ip)
        if embedded is not None and not is_public_ip(embedded):
            return False
    # is_global already excludes private, loopback, link-local (incl. 169.254.169.254),
    # CGNAT, reserved, unspecified, documentation and benchmarking ranges.
    return ip.is_global and not ip.is_multicast


def check_ip(address: str) -> IPAddress:
    try:
        ip = ipaddress.ip_address(address)  # accepts "fe80::1%eth0" (scope_id is set)
    except ValueError as exc:
        raise UnsafeURLError("invalid_ip", address) from exc
    if not is_public_ip(ip):
        raise UnsafeURLError("blocked_ip", str(ip))
    return ip


def validate_url(url: str | httpx.URL) -> Target:
    """Static checks (no DNS): scheme, credentials, host and port."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError) as exc:
        raise UnsafeURLError("invalid_url", str(url)) from exc

    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError("blocked_scheme", scheme or "(none)")
    if parsed.userinfo:
        raise UnsafeURLError("credentials_in_url")
    host = parsed.raw_host.decode("ascii").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("missing_host")
    port = parsed.port or (443 if scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError("blocked_port", str(port))
    return Target(url=parsed, scheme=scheme, host=host, port=port)


def _ip_literal(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


async def resolve_target(target: Target, resolver: Resolver) -> list[str]:
    """Return the public IPs to connect to. Blocks if ANY answer is non-public:
    a host that mixes public and internal records is treated as hostile."""
    literal = _ip_literal(target.host)
    if literal is not None:
        check_ip(str(literal))
        return [str(literal)]

    try:
        addresses = await resolver.resolve(target.host, target.port)
    except (OSError, UnicodeError) as exc:
        raise UnsafeURLError("dns_error", target.host) from exc
    if not addresses:
        raise UnsafeURLError("dns_error", target.host)
    return [str(check_ip(a)) for a in addresses]
