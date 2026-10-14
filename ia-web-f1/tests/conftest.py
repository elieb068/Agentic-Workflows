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


@pytest.fixture(scope="session")
def browser_available() -> bool:
    """Chromium for Playwright: set CHROMIUM_EXECUTABLE if Playwright's own browser is not installed."""
    import os

    from playwright.sync_api import Error, sync_playwright

    try:
        with sync_playwright() as p:
            p.chromium.launch(executable_path=os.environ.get("CHROMIUM_EXECUTABLE") or None).close()
        return True
    except Error:
        return False


@pytest.fixture
def require_browser(browser_available):
    if not browser_available:
        pytest.skip("Chromium for Playwright not available (set CHROMIUM_EXECUTABLE)")


# --- Database (M2) -------------------------------------------------------------
# Tests marked `db` run against the dev PostgreSQL from compose.dev.yaml, in the separate
# raioxia_test database, and are skipped when it is unreachable (like the browser tests).

TEST_DATABASE_URL = "postgresql+psycopg://raioxia:raioxia@127.0.0.1:15432/raioxia_test"


@pytest.fixture(scope="session")
def db_url() -> str:
    import os

    import psycopg
    from sqlalchemy.engine import make_url

    url = os.environ.get("TEST_DATABASE_URL", TEST_DATABASE_URL)
    parsed = make_url(url)
    try:
        psycopg.connect(
            host=parsed.host, port=parsed.port, user=parsed.username, password=parsed.password,
            dbname=parsed.database, connect_timeout=2,
        ).close()
    except psycopg.OperationalError:
        pytest.skip("test PostgreSQL not reachable (docker compose -f compose.dev.yaml up -d)")
    return url


@pytest.fixture(scope="session")
def db_engine(db_url):
    """Engine on a freshly migrated test database (schema rebuilt once per test session)."""
    from raioxia.db.engine import make_engine
    from raioxia.db.migrate import downgrade, upgrade

    downgrade(db_url)
    upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_sessionmaker(db_engine):
    """Sessions inside one outer transaction that is rolled back after the test.

    Code under test may call commit(): with create_savepoint it only releases a savepoint.
    """
    from sqlalchemy.orm import sessionmaker

    connection = db_engine.connect()
    outer = connection.begin()
    factory = sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
    yield factory
    outer.rollback()
    connection.close()


@pytest.fixture
def db(db_sessionmaker):
    session = db_sessionmaker()
    yield session
    session.close()


# --- Pipeline environment (M2) ------------------------------------------------------------------
# Everything the API and the jobs reach for, swapped for test doubles: DB sessions in the test
# transaction, synchronous fakeredis queues, a ConsoleSender, and a fake runner (never the network).


@dataclass
class PipelineEnv:
    settings: object
    sender: object
    redis: object
    queues: dict
    analyze_calls: list[str] = field(default_factory=list)
    analyze_result: object = None  # an AnalysisReport, or an exception to raise


def sample_report(score: int | None = 71):
    from datetime import UTC, datetime

    from raioxia.models import AnalysisReport, Band, CheckResult, Status

    return AnalysisReport(
        url="https://exemplo.com.br/",
        final_url="https://exemplo.com.br/",
        score=score,
        band=Band(id="ajar", label="Entreaberta") if score is not None else None,
        checks=[
            CheckResult(id="robots_ai", status=Status.OK, titulo="Robôs de IA liberados", explicacao="ok", peso=25),
            CheckResult(id="js_dependency", status=Status.ERROR, titulo="Conteúdo só aparece com JavaScript", explicacao="x", peso=20),
            CheckResult(id="llms_txt", status=Status.WARNING, titulo="llms.txt incompleto", explicacao="x", peso=10),
        ],
        analyzed_at=datetime(2026, 10, 14, 12, 0, tzinfo=UTC),
        duration_ms=1946,
        version="0.0.1",
    )


@pytest.fixture
def pipeline_env(db_sessionmaker, monkeypatch) -> PipelineEnv:
    from contextlib import contextmanager

    import fakeredis
    from rq import Queue

    import raioxia.email.jobs
    import raioxia.jobs
    from raioxia.email.base import ConsoleSender
    from raioxia.settings import Settings

    redis = fakeredis.FakeStrictRedis()
    env = PipelineEnv(
        settings=Settings(_env_file=None, secret_key="k" * 40, app_base_url="https://raiox.primesolutions.net.br"),
        sender=ConsoleSender(),
        redis=redis,
        queues={name: Queue(name, connection=redis, is_async=False) for name in ("analysis", "email")},
        analyze_result=sample_report(),
    )

    @contextmanager
    def scope(factory=None):
        session = db_sessionmaker()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    async def fake_analyze(url, **kwargs):
        env.analyze_calls.append(url)
        if isinstance(env.analyze_result, BaseException):
            raise env.analyze_result
        return env.analyze_result

    for module in (raioxia.jobs, raioxia.email.jobs):
        monkeypatch.setattr(module, "session_scope", scope)
        monkeypatch.setattr(module, "get_settings", lambda: env.settings)
    monkeypatch.setattr(raioxia.jobs, "get_queue", lambda name, connection=None: env.queues[name])
    monkeypatch.setattr(raioxia.jobs, "analyze", fake_analyze)
    monkeypatch.setattr(raioxia.email.jobs, "get_sender", lambda: env.sender)
    return env
