"""Network-free test doubles: a fake web (httpx.MockTransport) and a fake DNS resolver."""

import socket
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import pytest

from raioxia.net.fetcher import FetcherSettings, SafeFetcher

# Real public addresses (documentation ranges like 203.0.113.0/24 are not "global").
PUBLIC_IPS = [f"93.184.215.{n}" for n in range(10, 250)]

Handler = Callable[[httpx.Request], httpx.Response]


class FakeResolver:
    def __init__(self) -> None:
        self.records: dict[str, list[str] | Callable[[], list[str]]] = {}
        self.calls: list[str] = []

    def set(self, host: str, ips: list[str] | Callable[[], list[str]]) -> None:
        self.records[host.lower()] = ips

    async def resolve(self, host: str, port: int) -> list[str]:
        self.calls.append(host)
        record = self.records.get(host.lower())
        if record is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return record() if callable(record) else list(record)


@dataclass
class SeenRequest:
    method: str
    host: str  # from the Host header (what the site sees)
    ip: str  # where the connection went (request URL host)
    path: str
    headers: httpx.Headers
    sni: str | None


@dataclass
class FakeWeb:
    """Routes requests by scheme + Host header + path. Unknown paths return 404."""

    resolver: FakeResolver = field(default_factory=FakeResolver)
    routes: dict[tuple[str, str, str], Handler] = field(default_factory=dict)  # (scheme, host, path)
    requests: list[SeenRequest] = field(default_factory=list)
    _next_ip: int = 0

    def add_host(self, host: str, ips: list[str] | None = None) -> list[str]:
        if host.lower() not in self.resolver.records:
            if ips is None:
                ips = [PUBLIC_IPS[self._next_ip]]
                self._next_ip += 1
            self.resolver.set(host, ips)
        return self.resolver.records[host.lower()]

    def route(self, url: str, handler: Handler) -> None:
        parsed = httpx.URL(url)
        self.add_host(parsed.host)
        self.routes[(parsed.scheme, parsed.host.lower(), parsed.raw_path.decode())] = handler

    def add(
        self,
        url: str,
        body: bytes | str = b"",
        *,
        status: int = 200,
        content_type: str = "text/html; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        content = body.encode() if isinstance(body, str) else body
        all_headers = {"content-type": content_type, **(headers or {})}
        self.route(url, lambda request: httpx.Response(status, content=content, headers=all_headers))

    def redirect(self, url: str, location: str, status: int = 301) -> None:
        self.route(url, lambda request: httpx.Response(status, headers={"location": location}))

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        host = httpx.URL(f"http://{request.headers['host']}/").host
        self.requests.append(
            SeenRequest(
                method=request.method,
                host=host,
                ip=request.url.host,
                path=request.url.raw_path.decode(),
                headers=request.headers,
                sni=request.extensions.get("sni_hostname"),
            )
        )
        handler = self.routes.get((request.url.scheme, host.lower(), request.url.raw_path.decode()))
        if handler is None:
            return httpx.Response(404, content=b"not found", headers={"content-type": "text/plain"})
        result = handler(request)
        if hasattr(result, "__await__"):
            result = await result
        return result

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
async def make_fetcher(web: FakeWeb):
    fetchers: list[SafeFetcher] = []

    def factory(**settings) -> SafeFetcher:
        fetcher = SafeFetcher(FetcherSettings(**settings), resolver=web.resolver, transport=web.transport())
        fetchers.append(fetcher)
        return fetcher

    yield factory
    for fetcher in fetchers:
        await fetcher.aclose()


@pytest.fixture
def analyze_site(make_fetcher):
    """Run the real runner against the FakeWeb (no rendering unless asked)."""
    from raioxia.runner import analyze

    async def run(url: str, *, checkers=None, render: bool = False, **settings):
        return await analyze(url, fetcher=make_fetcher(**settings), checkers=checkers, render=render)

    return run
