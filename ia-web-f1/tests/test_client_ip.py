from ipaddress import IPv4Network, IPv6Network

import pytest
from starlette.requests import Request

from raioxia.api.client_ip import client_ip

PROXIES = [IPv4Network("172.16.0.0/12"), IPv4Network("127.0.0.0/8"), IPv6Network("::1/128")]


def request(peer: str | None, *forwarded: str) -> Request:
    headers = [(b"x-forwarded-for", v.encode()) for v in forwarded]
    return Request({"type": "http", "client": (peer, 1234) if peer else None, "headers": headers})


@pytest.mark.parametrize(
    ("peer", "forwarded", "expected"),
    [
        ("203.0.113.9", (), "203.0.113.9"),  # direct client
        ("203.0.113.9", ("198.51.100.1",), "203.0.113.9"),  # untrusted peer can't claim another IP
        ("172.18.0.5", ("198.51.100.1",), "198.51.100.1"),  # Caddy on the Docker network
        ("172.18.0.5", ("6.6.6.6, 198.51.100.1",), "198.51.100.1"),  # client-spoofed left part ignored
        ("172.18.0.5", ("198.51.100.1, 172.18.0.9",), "198.51.100.1"),  # chain of trusted proxies
        ("172.18.0.5", ("6.6.6.6", "198.51.100.1"), "198.51.100.1"),  # repeated headers
        ("172.18.0.5", ("garbage, 198.51.100.1",), "198.51.100.1"),
        ("172.18.0.5", ("198.51.100.1, garbage",), "172.18.0.5"),  # stop at garbage
        ("172.18.0.5", (), "172.18.0.5"),
        ("::ffff:203.0.113.9", (), "203.0.113.9"),  # IPv4-mapped IPv6
        ("::1", ("2001:db8::7",), "2001:db8::7"),
        ("testclient", (), None),
        (None, (), None),
    ],
)
def test_client_ip(peer, forwarded, expected):
    assert client_ip(request(peer, *forwarded), PROXIES) == expected
