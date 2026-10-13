import socket
import threading
from pathlib import Path

import pytest

from raioxia.net.render import Renderer, RenderError, RenderSettings

pytestmark = [pytest.mark.browser, pytest.mark.usefixtures("require_browser")]
SPA = Path(__file__).parent / "fixtures" / "spa"


def memoized(fetcher):
    """Mirrors AnalysisContext.fetch, which is what the renderer gets in production."""
    cache = {}

    async def fetch(url):
        if url not in cache:
            cache[url] = await fetcher.fetch(url)
        return cache[url]

    return fetch


async def test_renders_spa_through_safe_fetcher(web, make_fetcher):
    web.add("https://spa.example/", (SPA / "index.html").read_text(encoding="utf-8"))
    web.add("https://spa.example/static/js/main.js", (SPA / "main.js").read_text(encoding="utf-8"), content_type="application/javascript")
    async with Renderer() as renderer:
        result = await renderer.render("https://spa.example/", memoized(make_fetcher()))
    assert "Padaria Pão Quente" in result.text and "desde 1998" in result.text
    assert result.requests == 2 and result.blocked == []
    assert [r.path for r in web.requests] == ["/", "/static/js/main.js"]


async def test_subresource_to_internal_address_is_blocked(web, make_fetcher):
    web.add(
        "https://spa.example/",
        '<html><body><p>oi</p><script src="http://169.254.169.254/latest/meta-data/"></script>'
        '<iframe src="http://10.0.0.1/admin"></iframe></body></html>',
    )
    async with Renderer() as renderer:
        result = await renderer.render("https://spa.example/", memoized(make_fetcher()))
    assert sorted(result.blocked) == ["http://10.0.0.1/admin", "http://169.254.169.254/latest/meta-data/"]
    assert all(r.host == "spa.example" for r in web.requests)


async def test_nothing_escapes_to_the_network(web, make_fetcher):
    """fetch() is routed to the SSRF guard; WebSocket is refused; neither reaches a local server."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    server.settimeout(0.2)
    port = server.getsockname()[1]
    connections = []
    stop = threading.Event()

    def accept():
        while not stop.is_set():
            try:
                conn, _ = server.accept()
                connections.append(conn)
            except TimeoutError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    web.add(
        "https://spa.example/",
        f"""<html><body><p>oi</p><script>
        fetch("http://127.0.0.1:{port}/x").catch(() => {{}});
        try {{ new WebSocket("ws://127.0.0.1:{port}/ws"); }} catch (e) {{}}
        try {{ new WebSocket("wss://spa.example/ws"); }} catch (e) {{}}
        </script></body></html>""",
    )
    try:
        async with Renderer() as renderer:
            await renderer.render("https://spa.example/", memoized(make_fetcher()))
    finally:
        stop.set()
        thread.join()
        server.close()
    assert connections == []


async def test_post_requests_are_not_sent(web, make_fetcher):
    web.add("https://spa.example/", '<html><body><script>fetch("/track", {method: "POST", body: "x"})</script></body></html>')
    async with Renderer() as renderer:
        await renderer.render("https://spa.example/", memoized(make_fetcher()))
    assert [r.method for r in web.requests] == ["GET"]


async def test_redirect_lands_on_final_url(web, make_fetcher):
    web.redirect("https://spa.example/", "https://www.spa.example/")
    web.add("https://www.spa.example/", '<html><body><p id="u"></p><script>u.textContent = location.href</script></body></html>')
    async with Renderer() as renderer:
        result = await renderer.render("https://spa.example/", memoized(make_fetcher()))
    assert result.text == "https://www.spa.example/"


async def test_redirecting_script_is_served_without_3xx(web, make_fetcher):
    web.add("https://spa.example/", '<html><body><p id="t"></p><script src="/old.js"></script></body></html>')
    web.redirect("https://spa.example/old.js", "https://cdn.spa.example/new.js")
    web.add("https://cdn.spa.example/new.js", 't.textContent = "carregado"', content_type="application/javascript")
    async with Renderer() as renderer:
        result = await renderer.render("https://spa.example/", memoized(make_fetcher()))
    assert result.text == "carregado"


async def test_request_budget(web, make_fetcher):
    scripts = "".join(f'<script src="/s{i}.js"></script>' for i in range(10))
    web.add("https://spa.example/", f"<html><body>{scripts}</body></html>")
    async with Renderer(RenderSettings(max_requests=3)) as renderer:
        result = await renderer.render("https://spa.example/", memoized(make_fetcher()))
    assert result.requests == 3


async def test_timeout_raises_render_error(web, make_fetcher):
    import asyncio

    import httpx

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200)

    web.route("https://slow.example/", slow)
    with pytest.raises(RenderError):
        async with Renderer(RenderSettings(total_timeout=0.5)) as renderer:
            await renderer.render("https://slow.example/", memoized(make_fetcher()))


async def test_bad_executable_raises_render_error():
    with pytest.raises(RenderError):
        async with Renderer(RenderSettings(executable_path="/nonexistent/chrome")):
            pass
