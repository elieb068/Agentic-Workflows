"""SafeFetcher: the only way the analysis touches the network.

Each hop is validated by the SSRF guard, resolved once, and the connection is
pinned to the validated IP (Host header and TLS SNI keep the original name, so
certificates are still checked against the hostname). Redirects are followed
manually so every Location goes through the same checks.
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from functools import cached_property
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx

from raioxia.net.ssrf import Resolver, SystemResolver, Target, UnsafeURLError, resolve_target, validate_url

DEFAULT_USER_AGENT = "RaioXIA-Bot/0.1 (+https://raioxia.com.br/bot)"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


@dataclass(frozen=True)
class FetcherSettings:
    user_agent: str = field(default_factory=lambda: os.environ.get("FETCHER_USER_AGENT", DEFAULT_USER_AGENT))
    max_redirects: int = 5
    max_bytes: int = 5 * 1024 * 1024
    connect_timeout: float = 5.0
    read_timeout: float = 10.0
    total_timeout: float = 20.0  # whole fetch, all hops included
    concurrency: int = 8


class FetchError(Exception):
    """kind: blocked | dns | timeout | connect | tls | too_many_redirects | protocol"""

    def __init__(self, kind: str, url: str, detail: str = ""):
        super().__init__(f"{kind} fetching {url}" + (f": {detail}" if detail else ""))
        self.kind = kind
        self.url = url
        self.detail = detail


@dataclass(frozen=True)
class Hop:
    url: str
    status_code: int
    ip: str


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    headers: httpx.Headers
    content: bytes
    truncated: bool
    ip: str
    elapsed_ms: int
    hops: list[Hop] = field(default_factory=list)  # redirects followed before final_url

    @property
    def redirected(self) -> bool:
        return bool(self.hops)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    @cached_property
    def text(self) -> str:
        charset = None
        for param in self.headers.get("content-type", "").split(";")[1:]:
            key, _, value = param.strip().partition("=")
            if key.lower() == "charset":
                charset = value.strip("\"' ")
        if not charset:
            charset = "utf-8-sig"
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


def _host_header(target: Target) -> str:
    host = f"[{target.host}]" if ":" in target.host else target.host
    default_port = 443 if target.scheme == "https" else 80
    return host if target.port == default_port else f"{host}:{target.port}"


def _classify_connect_error(exc: Exception) -> str:
    message = str(exc).lower()
    return "tls" if "ssl" in message or "certificate" in message or "tls" in message else "connect"


class SafeFetcher:
    def __init__(
        self,
        settings: FetcherSettings | None = None,
        resolver: Resolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings or FetcherSettings()
        self.resolver = resolver or SystemResolver()
        self._semaphore = asyncio.Semaphore(self.settings.concurrency)
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,  # a proxy would resolve DNS itself and bypass the IP pinning
            timeout=httpx.Timeout(
                connect=self.settings.connect_timeout,
                read=self.settings.read_timeout,
                write=self.settings.read_timeout,
                pool=self.settings.connect_timeout,
            ),
            # Never store cookies: every request must look like a first visit (check 2 compares them).
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        )

    async def __aenter__(self) -> "SafeFetcher":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        user_agent: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> FetchResult:
        async with self._semaphore:
            try:
                async with asyncio.timeout(self.settings.total_timeout):
                    return await self._fetch(url, method, user_agent, headers or {})
            except TimeoutError as exc:
                raise FetchError("timeout", url) from exc

    async def _fetch(self, url: str, method: str, user_agent: str | None, extra_headers: dict[str, str]) -> FetchResult:
        started = time.perf_counter()
        hops: list[Hop] = []
        current = url
        for _ in range(self.settings.max_redirects + 1):
            try:
                target = validate_url(current)
                ips = await resolve_target(target, self.resolver)
            except UnsafeURLError as exc:
                kind = "dns" if exc.reason == "dns_error" else "blocked"
                raise FetchError(kind, current, exc.reason) from exc

            response, ip = await self._send_pinned(target, ips, method, user_agent, extra_headers)
            location = response.headers.get("location")
            if response.status_code in REDIRECT_STATUSES and location:
                await response.aclose()
                hops.append(Hop(url=str(target.url), status_code=response.status_code, ip=ip))
                try:
                    current = str(target.url.join(location))
                except httpx.InvalidURL as exc:
                    raise FetchError("protocol", current, "invalid redirect location") from exc
                if response.status_code == 303 and method != "HEAD":
                    method = "GET"
                continue

            content, truncated = await self._read_body(response, str(target.url))
            return FetchResult(
                url=url,
                final_url=str(target.url),
                status_code=response.status_code,
                headers=response.headers,
                content=content,
                truncated=truncated,
                ip=ip,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                hops=hops,
            )
        raise FetchError("too_many_redirects", url, f"more than {self.settings.max_redirects}")

    async def _send_pinned(
        self,
        target: Target,
        ips: list[str],
        method: str,
        user_agent: str | None,
        extra_headers: dict[str, str],
    ) -> tuple[httpx.Response, str]:
        headers = {
            **DEFAULT_HEADERS,
            **extra_headers,
            "Host": _host_header(target),
            "User-Agent": user_agent or self.settings.user_agent,
        }
        extensions = {"sni_hostname": target.host} if target.scheme == "https" else {}
        last_error: Exception | None = None
        for ip in ips:
            request = self._client.build_request(
                method, target.url.copy_with(host=ip), headers=headers, extensions=extensions
            )
            try:
                return await self._client.send(request, stream=True), ip
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc  # try the next validated address
            except httpx.TimeoutException as exc:
                raise FetchError("timeout", str(target.url)) from exc
            except httpx.HTTPError as exc:
                raise FetchError("protocol", str(target.url), type(exc).__name__) from exc
        assert last_error is not None
        raise FetchError(_classify_connect_error(last_error), str(target.url), str(last_error)) from last_error

    async def _read_body(self, response: httpx.Response, url: str) -> tuple[bytes, bool]:
        limit = self.settings.max_bytes
        buffer = bytearray()
        truncated = False
        try:
            # aiter_bytes yields decompressed data, so the cap also stops compression bombs.
            async for chunk in response.aiter_bytes():
                buffer.extend(chunk)
                if len(buffer) > limit:
                    truncated = True
                    del buffer[limit:]
                    break
        except httpx.TimeoutException as exc:
            raise FetchError("timeout", url) from exc
        except httpx.HTTPError as exc:
            raise FetchError("protocol", url, type(exc).__name__) from exc
        finally:
            await response.aclose()
        return bytes(buffer), truncated
