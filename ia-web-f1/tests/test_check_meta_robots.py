import pytest

from raioxia.checkers.meta_robots import ALL, HEADER, META, MetaRobotsChecker, collect
from raioxia.models import Status

TOKENS = {"googlebot", "gptbot", "bingbot"}


def page(head: str = "") -> str:
    return f'<html lang="pt-BR"><head><title>x</title>{head}</head><body>oi</body></html>'


async def run(web, analyze_site, html: str, headers: dict | None = None, status: int = 200):
    web.add("https://site.example/", html, headers=headers, status=status)
    report = await analyze_site("https://site.example/", checkers=[MetaRobotsChecker()])
    return report.checks[0]


def test_collect_meta_and_prefixed_headers():
    found = collect(
        {"robots": ["NoFollow, NOAI"], "googlebot": ["noindex"]},
        ["googlebot: nosnippet", "max-snippet: 0", "unavailable_after: 25 Jun 2030 15:00:00 PST"],
        TOKENS,
    )
    assert set(found[ALL]) == {"nofollow", "noai", "max-snippet:0", "unavailable_after:25jun203015:00:00pst"}
    assert found["googlebot"]["noindex"] == {META}
    assert found["googlebot"]["nosnippet"] == {HEADER}


def test_collect_ignores_unrelated_meta_names():
    assert collect({"viewport": ["noindex"]}, [], TOKENS) == {}


async def test_clean_page_is_ok(web, analyze_site):
    result = await run(web, analyze_site, page('<meta name="robots" content="index, follow, max-image-preview:large">'))
    assert result.status == Status.OK


async def test_noindex_meta_is_error(web, analyze_site):
    result = await run(web, analyze_site, page('<meta name="robots" content="noindex">'))
    assert result.status == Status.ERROR
    assert "todos os robôs" in result.explicacao
    assert "Configurações > Leitura" in result.como_corrigir


async def test_none_counts_as_noindex(web, analyze_site):
    result = await run(web, analyze_site, page('<meta name="robots" content="none">'))
    assert result.status == Status.ERROR
    assert "nofollow" not in result.explicacao  # reported once, as noindex


async def test_noindex_header_is_error(web, analyze_site):
    result = await run(web, analyze_site, page(), headers={"x-robots-tag": "noindex, nofollow"})
    assert result.status == Status.ERROR
    assert "X-Robots-Tag" in result.explicacao
    assert result.evidencia["x_robots_tag"] == ["noindex, nofollow"]


async def test_noindex_for_googlebot_only_is_error(web, analyze_site):
    result = await run(web, analyze_site, page('<meta name="googlebot" content="noindex">'))
    assert result.status == Status.ERROR
    assert "Googlebot" in result.explicacao


async def test_noindex_for_training_bot_only_is_not_penalized(web, analyze_site):
    result = await run(web, analyze_site, page('<meta name="gptbot" content="noindex">'))
    assert result.status == Status.OK
    assert result.evidencia["directives"] == {"gptbot": ["noindex"]}


async def test_nofollow_is_warning(web, analyze_site):
    result = await run(web, analyze_site, page('<meta name="robots" content="nofollow">'))
    assert result.status == Status.WARNING
    assert "não seguirem os links" in result.explicacao


@pytest.mark.parametrize(("directive", "word"), [("noai", "o conteúdo"), ("noimageai", "as imagens")])
async def test_noai_family_is_warning(web, analyze_site, directive, word):
    result = await run(web, analyze_site, page(f'<meta name="robots" content="{directive}">'))
    assert result.status == Status.WARNING
    assert word in result.explicacao
    assert "robots.txt" in result.como_corrigir


@pytest.mark.parametrize("content", ["nosnippet", "max-snippet:0", "max-snippet: 0"])
async def test_nosnippet_is_warning(web, analyze_site, content):
    result = await run(web, analyze_site, page(f'<meta name="robots" content="{content}">'))
    assert result.status == Status.WARNING
    assert "AI Overviews" in result.explicacao


async def test_multiple_findings_listed(web, analyze_site):
    result = await run(web, analyze_site, page('<meta name="robots" content="noindex, noai">'))
    assert result.status == Status.ERROR
    assert result.explicacao.count("•") == 2


async def test_error_page_is_indeterminado(web, analyze_site):
    result = await run(web, analyze_site, page('<meta name="robots" content="noindex">'), status=500)
    assert result.status == Status.UNKNOWN
