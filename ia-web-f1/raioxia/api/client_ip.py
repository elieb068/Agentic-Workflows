"""The real client IP behind Caddy (and, if the DNS record is proxied, Cloudflare via Caddy).

X-Forwarded-For is only believed when the direct peer is a trusted proxy. The header is
read right to left, skipping trusted proxies; the first other address is the client.
Anything a client prepends on the left is never reached.
"""

from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address

from starlette.requests import Request

Network = IPv4Network | IPv6Network


def _parse(value: str) -> IPv4Address | IPv6Address | None:
    try:
        ip = ip_address(value.strip())
    except ValueError:
        return None
    return (ip.ipv4_mapped or ip) if isinstance(ip, IPv6Address) else ip  # ::ffff:1.2.3.4 -> 1.2.3.4


def _trusted(ip: IPv4Address | IPv6Address, proxies: list[Network]) -> bool:
    return any(ip.version == net.version and ip in net for net in proxies)


def client_ip(request: Request, trusted_proxies: list[Network]) -> str | None:
    peer = _parse(request.client.host) if request.client else None
    if peer is None:
        return None
    if not _trusted(peer, trusted_proxies):
        return str(peer)
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    for hop in reversed([h for h in forwarded.split(",") if h.strip()]):
        ip = _parse(hop)
        if ip is None:  # garbage: stop trusting the header here
            break
        if not _trusted(ip, trusted_proxies):
            return str(ip)
    return str(peer)
