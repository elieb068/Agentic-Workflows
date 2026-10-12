import asyncio

import httpx
import pytest

from raioxia.net.fetcher import DEFAULT_USER_AGENT, FetchError


async def test_get_pins_ip_and_keeps_host_and_sni(web, make_fetcher):
    web.add("https://site.example/", "<h1>Olá</h1>")
    ip = web.add_host("site.example")[0]

    result = await make_fetcher().fetch("https://site.example/")

    assert result.status_code == 200
    assert result.text == "<h1>Olá</h1>"
    assert result.final_url == "https://site.example/"
    assert result.ip == ip
    assert not result.redirected and not result.truncated
    seen = web.requests[0]
    assert (seen.ip, seen.host, seen.sni) == (ip, "site.example", "site.example")
    assert seen.headers["user-agent"] == DEFAULT_USER_AGENT


async def test_plain_http_sends_no_sni(web, make_fetcher):
    web.add("http://site.example/", "ok")
    await make_fetcher().fetch("http://site.example/")
    assert web.requests[0].sni is None


async def test_user_agent_override(web, make_fetcher):
    web.add("https://site.example/", "ok")
    await make_fetcher().fetch("https://site.example/", user_agent="GPTBot/1.1")
    assert web.requests[0].headers["user-agent"] == "GPTBot/1.1"


async def test_follows_redirects_and_records_hops(web, make_fetcher):
    web.redirect("http://site.example/", "https://site.example/")
    web.redirect("https://site.example/", "/home", status=302)
    web.redirect("https://site.example/home", "https://www.site.example/home", status=308)
    web.add("https://www.site.example/home", "final")

    result = await make_fetcher().fetch("http://site.example/")

    assert result.final_url == "https://www.site.example/home"
    assert [h.status_code for h in result.hops] == [301, 302, 308]
    assert result.hops[1].url == "https://site.example/"
    assert result.text == "final"


def _chain(web, length: int) -> None:
    for i in range(length):
        web.redirect(f"https://site.example/{i}", f"/{i + 1}")
    web.add(f"https://site.example/{length}", "end")


async def test_five_redirects_allowed(web, make_fetcher):
    _chain(web, 5)
    result = await make_fetcher().fetch("https://site.example/0")
    assert result.text == "end" and len(result.hops) == 5


async def test_sixth_redirect_rejected(web, make_fetcher):
    _chain(web, 6)
    with pytest.raises(FetchError) as exc:
        await make_fetcher().fetch("https://site.example/0")
    assert exc.value.kind == "too_many_redirects"
    assert len(web.requests) == 6


@pytest.mark.parametrize(
    "location",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/admin",
        "http://[::1]/",
        "http://[::ffff:a9fe:a9fe]/",
        "http://10.0.0.1/",
        "http://site.example:8080/",
        "ftp://site.example/",
        "file:///etc/passwd",
    ],
)
async def test_redirect_to_forbidden_target_is_blocked(web, make_fetcher, location):
    web.redirect("https://site.example/", location)
    with pytest.raises(FetchError) as exc:
        await make_fetcher().fetch("https://site.example/")
    assert exc.value.kind == "blocked"
    assert len(web.requests) == 1  # nothing was sent to the forbidden target


async def test_redirect_to_host_resolving_internal_is_blocked(web, make_fetcher):
    web.redirect("https://site.example/", "https://internal.example/")
    web.add_host("internal.example", ["192.168.0.10"])
    with pytest.raises(FetchError) as exc:
        await make_fetcher().fetch("https://site.example/")
    assert exc.value.kind == "blocked"
    assert [r.host for r in web.requests] == ["site.example"]


async def test_dns_rebinding_connection_stays_on_validated_ip(web, make_fetcher):
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    web.add("https://rebind.example/", "public content")
    web.resolver.set("rebind.example", lambda: next(answers))

    result = await make_fetcher().fetch("https://rebind.example/")

    assert result.ip == "93.184.216.34"
    assert [r.ip for r in web.requests] == ["93.184.216.34"]
    assert web.resolver.calls == ["rebind.example"]  # resolved once; no second lookup to rebind


async def test_dns_rebinding_on_redirect_hop_is_revalidated(web, make_fetcher):
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    web.redirect("https://rebind.example/", "/next")
    web.resolver.set("rebind.example", lambda: next(answers))

    with pytest.raises(FetchError) as exc:
        await make_fetcher().fetch("https://rebind.example/")
    assert exc.value.kind == "blocked"
    assert len(web.requests) == 1


async def test_initial_internal_url_is_blocked(web, make_fetcher):
    web.add_host("intranet.example", ["10.1.2.3"])
    with pytest.raises(FetchError) as exc:
        await make_fetcher().fetch("http://intranet.example/")
    assert exc.value.kind == "blocked"
    assert web.requests == []


async def test_unknown_host_is_dns_error(make_fetcher):
    with pytest.raises(FetchError) as exc:
        await make_fetcher().fetch("https://nope.example/")
    assert exc.value.kind == "dns"


async def test_body_capped_at_five_megabytes(web, make_fetcher):
    web.add("https://big.example/", b"a" * (6 * 1024 * 1024))
    result = await make_fetcher().fetch("https://big.example/")
    assert result.truncated
    assert len(result.content) == 5 * 1024 * 1024


async def test_body_exactly_at_limit_is_not_truncated(web, make_fetcher):
    web.add("https://site.example/", b"a" * 1000)
    result = await make_fetcher(max_bytes=1000).fetch("https://site.example/")
    assert not result.truncated and len(result.content) == 1000


async def test_endless_stream_stops_at_cap(web, make_fetcher):
    async def endless():
        while True:
            yield b"x" * 65536

    web.route("https://endless.example/", lambda request: httpx.Response(200, content=endless()))
    result = await make_fetcher(max_bytes=1024 * 1024).fetch("https://endless.example/")
    assert result.truncated and len(result.content) == 1024 * 1024


async def test_total_timeout(web, make_fetcher):
    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200)

    web.route("https://slow.example/", slow)
    with pytest.raises(FetchError) as exc:
        await make_fetcher(total_timeout=0.05).fetch("https://slow.example/")
    assert exc.value.kind == "timeout"


async def test_read_timeout_from_transport(web, make_fetcher):
    def stall(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    web.route("https://stall.example/", stall)
    with pytest.raises(FetchError) as exc:
        await make_fetcher().fetch("https://stall.example/")
    assert exc.value.kind == "timeout"


async def test_falls_back_to_next_validated_ip(web, make_fetcher):
    web.add_host("multi.example", ["93.184.216.1", "93.184.216.2"])

    def handler(request):
        if request.url.host == "93.184.216.1":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, content=b"ok")

    web.route("https://multi.example/", handler)
    result = await make_fetcher().fetch("https://multi.example/")
    assert result.ip == "93.184.216.2"


@pytest.mark.parametrize(
    ("message", "kind"),
    [("connection refused", "connect"), ("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", "tls")],
)
async def test_connect_failures_are_classified(web, make_fetcher, message, kind):
    def handler(request):
        raise httpx.ConnectError(message, request=request)

    web.route("https://down.example/", handler)
    with pytest.raises(FetchError) as exc:
        await make_fetcher().fetch("https://down.example/")
    assert exc.value.kind == kind


async def test_cookies_are_never_sent_back(web, make_fetcher):
    web.add("https://site.example/a", "a", headers={"set-cookie": "__cf_bm=abc; Path=/"})
    web.add("https://site.example/b", "b")
    fetcher = make_fetcher()
    await fetcher.fetch("https://site.example/a")
    await fetcher.fetch("https://site.example/b")
    assert "cookie" not in web.requests[1].headers


async def test_head_survives_redirect(web, make_fetcher):
    web.redirect("https://site.example/", "/x")
    web.add("https://site.example/x", "")
    await make_fetcher().fetch("https://site.example/", method="HEAD")
    assert [r.method for r in web.requests] == ["HEAD", "HEAD"]


async def test_text_uses_declared_charset(web, make_fetcher):
    web.add("https://site.example/", "ação".encode("latin-1"), content_type="text/html; charset=ISO-8859-1")
    result = await make_fetcher().fetch("https://site.example/")
    assert result.text == "ação"
    assert result.content_type == "text/html"


async def test_text_with_unknown_charset_falls_back_to_utf8(web, make_fetcher):
    web.add("https://site.example/", "ação".encode(), content_type="text/html; charset=bogus")
    result = await make_fetcher().fetch("https://site.example/")
    assert result.text == "ação"
