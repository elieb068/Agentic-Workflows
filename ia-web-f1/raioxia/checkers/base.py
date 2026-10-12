"""Checker interface and the shared per-analysis context."""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, ClassVar

import httpx

from raioxia.config import Config
from raioxia.models import CheckResult, Status
from raioxia.net.fetcher import FetchError, FetchResult, SafeFetcher

_SEVERITY = {Status.OK: 0, Status.UNKNOWN: 1, Status.WARNING: 2, Status.ERROR: 3}


def worst(statuses: Iterable[Status]) -> Status:
    """Most severe status; indeterminado only wins over ok."""
    return max(statuses, key=_SEVERITY.__getitem__, default=Status.OK)


class AnalysisContext:
    """State shared by every checker in one analysis.

    Fetches are memoized (in-flight ones too), so checks that need the same
    resource (home, robots.txt) trigger a single request.
    """

    def __init__(
        self,
        url: str,
        fetcher: SafeFetcher,
        config: Config,
        *,
        scheme_was_implicit: bool = False,
        render: bool = True,
    ):
        self.url = url
        self.fetcher = fetcher
        self.config = config
        self.scheme_was_implicit = scheme_was_implicit
        self.render = render
        self._memo: dict[tuple, asyncio.Task] = {}

    def memo(self, key: tuple, factory: Callable[[], Awaitable[Any]]) -> asyncio.Task:
        task = self._memo.get(key)
        if task is None:
            task = self._memo[key] = asyncio.ensure_future(factory())
        return task

    async def fetch(self, url: str, *, method: str = "GET", user_agent: str | None = None) -> FetchResult:
        """Memoized SafeFetcher.fetch. Raises FetchError (also memoized)."""
        key = ("fetch", url, method, user_agent)
        return await self.memo(key, lambda: self.fetcher.fetch(url, method=method, user_agent=user_agent))

    async def home(self) -> FetchResult:
        """The home page fetched with our own UA. Raises FetchError."""
        return await self.memo(("home",), self._fetch_home)

    async def _fetch_home(self) -> FetchResult:
        try:
            return await self.fetch(self.url)
        except FetchError as exc:
            # User typed "example.com" and the site has no working HTTPS: try plain HTTP.
            if self.scheme_was_implicit and exc.kind in {"connect", "tls", "timeout"}:
                return await self.fetch(str(httpx.URL(self.url).copy_with(scheme="http")))
            raise

    async def site_url(self, path: str) -> str:
        """Absolute URL of a root file (robots.txt, llms.txt...) on the host the home ended up on."""
        try:
            base = (await self.home()).final_url
        except FetchError:
            base = self.url
        return str(httpx.URL(base).join(path))


class Checker(ABC):
    id: ClassVar[str]
    titulo: ClassVar[str]

    @abstractmethod
    async def run(self, ctx: AnalysisContext) -> CheckResult: ...

    def result(
        self,
        status: Status,
        explicacao: str,
        como_corrigir: str = "",
        evidencia: dict[str, Any] | None = None,
        titulo: str | None = None,
    ) -> CheckResult:
        # peso is filled in by the runner from config/scoring.yaml.
        return CheckResult(
            id=self.id,
            status=status,
            titulo=titulo or self.titulo,
            explicacao=explicacao,
            como_corrigir=como_corrigir,
            evidencia=evidencia or {},
        )
