import ipaddress

import pytest

from raioxia.net.ssrf import SystemResolver, UnsafeURLError, is_public_ip, resolve_target, validate_url
from tests.conftest import FakeResolver

BLOCKED_IPS = [
    "127.0.0.1", "127.255.255.254", "0.0.0.0", "10.0.0.1", "172.16.0.1", "172.31.255.255",
    "192.168.1.1", "169.254.169.254", "169.254.0.1", "100.64.0.1", "100.127.255.254",
    "192.0.0.1", "192.0.2.1", "198.18.0.1", "240.0.0.1", "255.255.255.255", "224.0.0.1",
    "::", "::1", "fe80::1", "fc00::1", "fd00:ec2::254",
    "::ffff:127.0.0.1", "::ffff:169.254.169.254", "::ffff:10.0.0.1",
    "64:ff9b::a9fe:a9fe", "64:ff9b::7f00:1", "64:ff9b:1::a00:1",
    "2002:7f00:1::", "2002:a9fe:a9fe::", "2001:db8::1", "ff02::1", "ff0e::1",
]
PUBLIC_IPS = ["8.8.8.8", "93.184.216.34", "1.1.1.1", "2606:4700:4700::1111", "64:ff9b::808:808"]


@pytest.mark.parametrize("address", BLOCKED_IPS)
def test_internal_and_special_ips_are_blocked(address):
    assert not is_public_ip(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", PUBLIC_IPS)
def test_public_ips_are_allowed(address):
    assert is_public_ip(ipaddress.ip_address(address))


def test_scoped_ipv6_is_blocked():
    assert not is_public_ip(ipaddress.ip_address("2606:4700:4700::1111%eth0"))


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("ftp://example.com/", "blocked_scheme"),
        ("file:///etc/passwd", "blocked_scheme"),
        ("gopher://example.com/", "blocked_scheme"),
        ("javascript:alert(1)", "blocked_scheme"),
        ("http://example.com:8080/", "blocked_port"),
        ("https://example.com:22/", "blocked_port"),
        ("http://example.com:6379/", "blocked_port"),
        ("http://user:pass@example.com/", "credentials_in_url"),
        ("http://user@example.com/", "credentials_in_url"),
        ("http:///path", "missing_host"),
    ],
)
def test_validate_url_rejects(url, reason):
    with pytest.raises(UnsafeURLError) as exc:
        validate_url(url)
    assert exc.value.reason == reason


@pytest.mark.parametrize(
    ("url", "host", "port"),
    [
        ("http://Example.COM/", "example.com", 80),
        ("https://example.com/", "example.com", 443),
        ("http://example.com:443/", "example.com", 443),
        ("https://example.com.:443/a", "example.com", 443),
        ("https://exämple.com/", "xn--exmple-cua.com", 443),
        ("http://[2606:4700::1]/", "2606:4700::1", 80),
    ],
)
def test_validate_url_accepts(url, host, port):
    target = validate_url(url)
    assert (target.host, target.port) == (host, port)


async def test_ip_literal_checked_without_dns():
    resolver = FakeResolver()
    for url in ["http://169.254.169.254/latest/meta-data/", "http://[::1]/", "http://[::ffff:7f00:1]/"]:
        with pytest.raises(UnsafeURLError) as exc:
            await resolve_target(validate_url(url), resolver)
        assert exc.value.reason == "blocked_ip"
    assert resolver.calls == []


async def test_host_with_any_internal_record_is_blocked():
    resolver = FakeResolver()
    resolver.set("mixed.example", ["93.184.216.34", "10.0.0.5"])
    with pytest.raises(UnsafeURLError) as exc:
        await resolve_target(validate_url("https://mixed.example/"), resolver)
    assert exc.value.reason == "blocked_ip"


async def test_unresolvable_host_is_dns_error():
    with pytest.raises(UnsafeURLError) as exc:
        await resolve_target(validate_url("https://nope.example/"), FakeResolver())
    assert exc.value.reason == "dns_error"


async def test_empty_dns_answer_is_dns_error():
    resolver = FakeResolver()
    resolver.set("empty.example", [])
    with pytest.raises(UnsafeURLError) as exc:
        await resolve_target(validate_url("https://empty.example/"), resolver)
    assert exc.value.reason == "dns_error"


@pytest.mark.parametrize("url", ["http://localhost/", "http://2130706433/", "http://127.1/"])
async def test_system_resolver_tricks_resolve_to_loopback_and_are_blocked(url):
    # Decimal and short IPv4 forms are not IP literals for the URL parser,
    # but getaddrinfo turns them into 127.0.0.1: the post-resolution check catches them.
    with pytest.raises(UnsafeURLError) as exc:
        await resolve_target(validate_url(url), SystemResolver())
    assert exc.value.reason == "blocked_ip"
