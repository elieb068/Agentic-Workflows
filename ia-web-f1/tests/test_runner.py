import asyncio

import pytest

from raioxia.checkers import REGISTRY, AnalysisContext, Checker
from raioxia.models import Status
from raioxia.runner import AnalysisError, analyze, normalize_url

OK_HOME = '<html lang="pt-BR"><head><title>Loja</title><meta name="description" content="d"></head></html>'


@pytest.mark.parametrize(
    ("raw", "url", "implicit"),
    [
        ("example.com", "https://example.com/", True),
        ("  Example.com/contato?x=1 ", "https://example.com/", True),
        ("http://example.com/a/b", "http://example.com/", False),
        ("https://www.exemplo.com.br:443/", "https://www.exemplo.com.br/", False),
        ("http://example.com:443/", "http://example.com:443/", False),
        ("exämple.com", "https://xn--exmple-cua.com/", True),
    ],
)
def test_normalize_url(raw, url, implicit):
    normalized = normalize_url(raw)
    assert (normalized.url, normalized.scheme_was_implicit) == (url, implicit)


@pytest.mark.parametrize("raw", ["", "   ", "ftp://example.com", "example.com:8080", "intranet", "user:pw@example.com"])
def test_normalize_url_rejects(raw):
    with pytest.raises(AnalysisError) as exc:
        normalize_url(raw)
    assert exc.value.code == "invalid_url"


class FakeCheck(Checker):
    id = "basics"  # reuse a configured weight
    titulo = "Fake"

    def __init__(self, behavior):
        self.behavior = behavior

    async def run(self, ctx: AnalysisContext):
        return await self.behavior(self, ctx)


async def test_report_fills_weight_score_and_band(web, analyze_site):
    web.add("https://site.example/", OK_HOME)

    async def ok(self, ctx):
        return self.result(Status.OK, "tudo certo")

    report = await analyze_site("site.example", checkers=[FakeCheck(ok)])
    assert report.url == "https://site.example/"
    assert report.final_url == "https://site.example/"
    assert report.checks[0].peso == 6
    assert report.score == 100 and report.band.label == "Porta aberta"


async def test_crashing_checker_becomes_indeterminado(web, analyze_site):
    web.add("https://site.example/", OK_HOME)

    async def boom(self, ctx):
        raise RuntimeError("bug")

    report = await analyze_site("site.example", checkers=[FakeCheck(boom)])
    check = report.checks[0]
    assert check.status == Status.UNKNOWN
    assert check.evidencia == {"internal_error": "RuntimeError"}
    assert report.score is None and report.band is None


async def test_slow_checker_times_out(web, make_fetcher):
    web.add("https://site.example/", OK_HOME)

    async def slow(self, ctx):
        await asyncio.sleep(5)

    report = await analyze("site.example", fetcher=make_fetcher(), checkers=[FakeCheck(slow)], check_timeout=0.05)
    assert report.checks[0].status == Status.UNKNOWN
    assert report.checks[0].evidencia == {"internal_error": "timeout"}


async def test_fetches_are_shared_between_checks(web, analyze_site):
    web.add("https://site.example/", OK_HOME)
    web.add("https://site.example/robots.txt", "User-agent: *", content_type="text/plain")

    async def uses_robots(self, ctx):
        await ctx.fetch(await ctx.site_url("/robots.txt"))
        return self.result(Status.OK, "ok")

    await analyze_site("site.example", checkers=[FakeCheck(uses_robots), FakeCheck(uses_robots)])
    assert [r.path for r in web.requests] == ["/", "/robots.txt"]


async def test_root_files_use_host_after_redirect(web, analyze_site):
    web.redirect("https://site.example/", "https://www.site.example/")
    web.add("https://www.site.example/", OK_HOME)
    seen = []

    async def record(self, ctx):
        seen.append(await ctx.site_url("/robots.txt"))
        return self.result(Status.OK, "ok")

    report = await analyze_site("site.example", checkers=[FakeCheck(record)])
    assert seen == ["https://www.site.example/robots.txt"]
    assert report.final_url == "https://www.site.example/"


async def test_implicit_scheme_falls_back_to_http(web, analyze_site):
    import httpx

    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    web.route("https://old.example/", refuse)
    web.add("http://old.example/", OK_HOME)

    async def ok(self, ctx):
        return self.result(Status.OK, "ok")

    report = await analyze_site("old.example", checkers=[FakeCheck(ok)])
    assert report.final_url == "http://old.example/"


async def test_explicit_https_does_not_fall_back(web, analyze_site):
    import httpx

    web.route("https://old.example/", lambda request: (_ for _ in ()).throw(httpx.ConnectError("x", request=request)))
    web.add("http://old.example/", OK_HOME)

    async def ok(self, ctx):
        return self.result(Status.OK, "ok")

    report = await analyze_site("https://old.example/", checkers=[FakeCheck(ok)])
    assert report.final_url is None
    assert [r.path for r in web.requests] == ["/"]  # no plain-HTTP retry


async def test_internal_target_refused(web, analyze_site):
    web.add_host("intranet.example", ["10.0.0.8"])
    with pytest.raises(AnalysisError) as exc:
        await analyze_site("intranet.example", checkers=[])
    assert exc.value.code == "blocked"


async def test_unknown_domain_refused(analyze_site):
    with pytest.raises(AnalysisError) as exc:
        await analyze_site("nao-existe.example", checkers=[])
    assert exc.value.code == "dns"


async def test_checker_without_weight_is_a_config_error(web, analyze_site):
    class Unweighted(Checker):
        id = "not_in_config"
        titulo = "x"

        async def run(self, ctx):
            raise AssertionError

    with pytest.raises(ValueError, match="not_in_config"):
        await analyze_site("site.example", checkers=[Unweighted()])


def test_registry_ids_all_have_weights():
    from raioxia.config import load_config

    assert set(REGISTRY) <= set(load_config().scoring.weights)
