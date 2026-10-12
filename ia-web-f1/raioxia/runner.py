"""Runs every registered checker against one site and builds the report."""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from raioxia import __version__
from raioxia.checkers import AnalysisContext, Checker, all_checkers
from raioxia.config import Config, get_config
from raioxia.models import AnalysisReport, Band, CheckResult, Status
from raioxia.net.fetcher import FetchError, SafeFetcher
from raioxia.net.ssrf import UnsafeURLError, validate_url
from raioxia.scoring import compute_score

log = logging.getLogger(__name__)

CHECK_TIMEOUT = 45.0  # seconds per checker; the queue job has its own 60s limit


class AnalysisError(Exception):
    """The analysis cannot run at all. `message` is user-facing (PT-BR)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class NormalizedURL:
    url: str  # scheme://host[:port]/ (Phase 1 only analyzes the home page)
    scheme_was_implicit: bool


def normalize_url(raw: str) -> NormalizedURL:
    text = raw.strip()
    implicit = "://" not in text
    if implicit:
        text = f"https://{text.lstrip('/')}"
    try:
        target = validate_url(text)
    except UnsafeURLError as exc:
        raise AnalysisError("invalid_url", "Esse endereço não parece ser de um site. Confira e tente de novo.") from exc
    if "." not in target.host and ":" not in target.host:
        raise AnalysisError("invalid_url", "Esse endereço não parece ser de um site. Confira e tente de novo.")
    url = httpx.URL(scheme=target.scheme, host=target.host, port=target.url.port, path="/")
    return NormalizedURL(url=str(url), scheme_was_implicit=implicit)


async def _run_one(checker: Checker, ctx: AnalysisContext, timeout: float) -> CheckResult:
    try:
        async with asyncio.timeout(timeout):
            return await checker.run(ctx)
    except Exception as exc:  # one broken check must not sink the whole report
        if not isinstance(exc, TimeoutError):
            log.exception("checker %s failed", checker.id)
        return CheckResult(
            id=checker.id,
            status=Status.UNKNOWN,
            titulo=getattr(checker, "titulo", checker.id),
            explicacao="Não conseguimos concluir esta verificação agora. Ela não conta na sua nota.",
            evidencia={"internal_error": "timeout" if isinstance(exc, TimeoutError) else type(exc).__name__},
        )


async def analyze(
    raw_url: str,
    *,
    fetcher: SafeFetcher | None = None,
    config: Config | None = None,
    checkers: list[Checker] | None = None,
    render: bool = True,
    check_timeout: float = CHECK_TIMEOUT,
) -> AnalysisReport:
    config = config or get_config()
    checkers = all_checkers() if checkers is None else checkers
    missing = [c.id for c in checkers if c.id not in config.scoring.weights]
    if missing:
        raise ValueError(f"checkers without a weight in scoring.yaml: {missing}")

    normalized = normalize_url(raw_url)
    owns_fetcher = fetcher is None
    fetcher = fetcher or SafeFetcher()
    started = time.perf_counter()
    try:
        ctx = AnalysisContext(
            normalized.url,
            fetcher,
            config,
            scheme_was_implicit=normalized.scheme_was_implicit,
            render=render,
        )
        final_url = None
        try:
            final_url = (await ctx.home()).final_url
        except FetchError as exc:
            if exc.kind == "blocked":
                raise AnalysisError("blocked", "Não podemos analisar esse endereço.") from exc
            if exc.kind == "dns":
                raise AnalysisError("dns", "Não encontramos esse domínio. Confira se o endereço está certo.") from exc
            # Unreachable for other reasons: still report, checks explain what failed.

        results = await asyncio.gather(*(_run_one(c, ctx, check_timeout) for c in checkers))
    finally:
        if owns_fetcher:
            await fetcher.aclose()

    for result in results:
        result.peso = config.scoring.weights[result.id]
    score = compute_score(results, config.scoring)
    band = config.scoring.band_for(score) if score is not None else None
    return AnalysisReport(
        url=normalized.url,
        final_url=final_url,
        score=score,
        band=Band(id=band.id, label=band.label) if band else None,
        checks=list(results),
        analyzed_at=datetime.now(UTC),
        duration_ms=round((time.perf_counter() - started) * 1000),
        version=__version__,
    )
