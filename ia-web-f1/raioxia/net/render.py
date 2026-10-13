"""Headless Chromium rendering where the browser never touches the network itself.

Every HTTP request the page makes is intercepted (page.route) and served by
the SafeFetcher, so the SSRF rules apply to scripts, XHR and iframes too.
Defense in depth for anything routing cannot see: Chromium's own DNS is
disabled and its proxy points to a dead local port, and WebSockets are closed
without connecting.
"""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from playwright.async_api import Browser, Route, WebSocketRoute, async_playwright
from playwright.async_api import Error as PlaywrightError

from raioxia.net.fetcher import FetchError, FetchResult

Fetch = Callable[[str], Awaitable[FetchResult]]

SKIPPED_RESOURCES = {"image", "media", "font"}
_DROP_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection", "set-cookie"}
_LAUNCH_ARGS = [
    "--host-resolver-rules=MAP * ~NOTFOUND",  # no DNS from the browser: all traffic goes through route()
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-background-networking",
    "--disable-features=DnsOverHttps,NetworkPrediction",
    "--no-pings",
]
_DEAD_PROXY = {"server": "http://127.0.0.1:9"}  # discard port: anything that escapes routing fails


class RenderError(Exception):
    pass


@dataclass(frozen=True)
class RenderSettings:
    executable_path: str | None = field(default_factory=lambda: os.environ.get("CHROMIUM_EXECUTABLE") or None)
    total_timeout: float = 25.0
    idle_timeout: float = 5.0  # extra wait for "networkidle" after load
    max_requests: int = 80


@dataclass
class RenderResult:
    html: str
    text: str
    elapsed_ms: int
    requests: int
    blocked: list[str] = field(default_factory=list)  # URLs refused by the SSRF guard
    failed: int = 0  # other fetch failures


class Renderer:
    def __init__(self, settings: RenderSettings | None = None):
        self.settings = settings or RenderSettings()
        self._playwright = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> "Renderer":
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                executable_path=self.settings.executable_path,
                args=_LAUNCH_ARGS,
                proxy=_DEAD_PROXY,
            )
        except PlaywrightError as exc:
            await self.__aexit__()
            raise RenderError(f"could not launch Chromium: {exc}") from exc
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def render(self, url: str, fetch: Fetch) -> RenderResult:
        if self._browser is None:
            raise RenderError("renderer not started")
        started = time.perf_counter()
        result = RenderResult(html="", text="", elapsed_ms=0, requests=0)
        try:
            # Navigate straight to where the page really lives, so relative URLs and location are right.
            url = (await fetch(url)).final_url
        except FetchError as exc:
            raise RenderError(f"could not fetch {url}: {exc.kind}") from exc
        context = await self._browser.new_context(service_workers="block", accept_downloads=False)

        async def handle(route: Route) -> None:
            request = route.request
            if request.resource_type in SKIPPED_RESOURCES or request.method not in ("GET", "HEAD"):
                await route.abort()
                return
            if result.requests >= self.settings.max_requests:
                await route.abort()
                return
            result.requests += 1
            try:
                response = await fetch(request.url)
            except FetchError as exc:
                if exc.kind == "blocked":
                    result.blocked.append(request.url)
                else:
                    result.failed += 1
                await route.abort()
                return
            # Redirects were already followed (and validated hop by hop) by the fetcher: serve the final
            # content directly. Synthetic 3xx answers escape routing in Chromium, so we never send them.
            headers = {k: v for k, v in response.headers.items() if k.lower() not in _DROP_HEADERS}
            await route.fulfill(status=response.status_code, headers=headers, body=response.content)

        async def refuse_websocket(ws: WebSocketRoute) -> None:
            await ws.close()

        try:
            await context.route("**/*", handle)
            await context.route_web_socket("**/*", refuse_websocket)
            page = await context.new_page()
            async with asyncio.timeout(self.settings.total_timeout):
                await page.goto(url, wait_until="load", timeout=self.settings.total_timeout * 1000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=self.settings.idle_timeout * 1000)
                except PlaywrightError:
                    pass  # pages that poll forever: use what has rendered so far
                result.html = await page.content()
                result.text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        except TimeoutError as exc:
            raise RenderError("render timed out") from exc
        except PlaywrightError as exc:
            raise RenderError(str(exc).splitlines()[0]) from exc
        finally:
            await context.close()
        result.elapsed_ms = round((time.perf_counter() - started) * 1000)
        return result
