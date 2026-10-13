from pathlib import Path

import httpx
import pytest

from raioxia.checkers.edge_block import EdgeBlockChecker, detect_cdn
from raioxia.models import Status

FIXTURES = Path(__file__).parent / "fixtures" / "edge"
PAGE = (FIXTURES / "normal_cloudflare_page.html").read_text()
CHALLENGE = (FIXTURES / "cloudflare_challenge.html").read_text()
CF_HEADERS = {"server": "cloudflare", "cf-ray": "8f1e2d3c4b5a6978-GRU", "content-type": "text/html; charset=utf-8"}
SEARCH_TOKENS = ["OAI-SearchBot", "ChatGPT-User", "Claude-SearchBot", "Claude-User", "PerplexityBot",
                 "Perplexity-User", "Googlebot", "bingbot"]
TRAINING_TOKENS = ["GPTBot", "ClaudeBot", "CCBot", "meta-externalagent", "Bytespider"]


def page(status=200, body=PAGE, headers=None):
    return lambda request: httpx.Response(status, content=body.encode(), headers=headers or CF_HEADERS)


def by_user_agent(default, **rules):
    """rules: UA substring -> handler; first match wins, else default."""

    def handler(request):
        ua = request.headers["user-agent"]
        for needle, rule in rules.items():
            if needle in ua:
                return rule(request)
        return default(request)

    return handler


async def run(web, analyze_site, handler):
    web.route("https://site.example/", handler)
    report = await analyze_site("https://site.example/", checkers=[EdgeBlockChecker()])
    return report.checks[0]


def verdicts(result):
    return {token: a["verdict"] for token, a in result.evidencia["agents"].items()}


async def test_everyone_gets_the_same_page(web, analyze_site):
    result = await run(web, analyze_site, page())
    assert result.status == Status.OK
    assert set(verdicts(result).values()) == {"same"}
    assert "13 robôs" in result.explicacao
    assert "indicativo" in result.explicacao
    assert result.evidencia["cdn"] == ["Cloudflare"]
    assert "dash.cloudflare.com" in result.como_corrigir  # Cloudflare steps are always shown
    assert result.evidencia["skipped_robots_only"] == ["Google-Extended", "Applebot-Extended"]


async def test_injected_challenge_script_is_not_a_challenge(web, analyze_site):
    assert "challenge-platform" in PAGE
    result = await run(web, analyze_site, page())
    assert result.evidencia["baseline"]["signals"] == []


async def test_search_bots_get_403(web, analyze_site):
    blocked = page(403, "<html><body>Forbidden</body></html>")
    rules = {token: blocked for token in ["OAI-SearchBot", "ChatGPT-User"]}
    result = await run(web, analyze_site, by_user_agent(page(), **rules))
    assert result.status == Status.ERROR
    assert "OAI-SearchBot (erro 403)" in result.explicacao
    assert "(Cloudflare)" in result.explicacao
    assert "dash.cloudflare.com" in result.como_corrigir
    assert result.explicacao.endswith(
        "alguns firewalls tratam o robô verdadeiro de forma diferente."
    )


async def test_cloudflare_challenge_with_200_is_detected(web, analyze_site):
    result = await run(web, analyze_site, by_user_agent(page(), PerplexityBot=page(200, CHALLENGE)))
    assert result.status == Status.ERROR
    agent = result.evidencia["agents"]["PerplexityBot"]
    assert agent["verdict"] == "blocked" and agent["reason"] == "challenge"
    assert "cloudflare_challenge" in agent["signals"]
    assert "PerplexityBot (desafio anti-robô)" in result.explicacao


async def test_cf_mitigated_header(web, analyze_site):
    challenged = page(200, "<html>hi</html>", {**CF_HEADERS, "cf-mitigated": "challenge"})
    result = await run(web, analyze_site, by_user_agent(page(), Googlebot=challenged))
    assert result.evidencia["agents"]["Googlebot"]["signals"] == ["cf_mitigated"]
    assert result.status == Status.ERROR


async def test_training_only_block_is_informational(web, analyze_site):
    blocked = page(403, "<html>no</html>")
    result = await run(web, analyze_site, by_user_agent(page(), **{t: blocked for t in TRAINING_TOKENS}))
    assert result.status == Status.OK
    assert "treino" in result.explicacao
    assert "mesma página que um navegador" in result.explicacao


async def test_much_smaller_page_is_warning(web, analyze_site):
    tiny = page(200, "<html><body>Acesso restrito</body></html>")
    result = await run(web, analyze_site, by_user_agent(page(), **{"Claude-User": tiny}))
    assert result.status == Status.WARNING
    assert result.evidencia["agents"]["Claude-User"]["reason"] == "smaller_page"


async def test_redirect_to_other_page_is_warning(web, analyze_site):
    web.add("https://site.example/bloqueado", PAGE)
    to_block_page = lambda request: httpx.Response(302, headers={"location": "/bloqueado"})  # noqa: E731
    result = await run(web, analyze_site, by_user_agent(page(), bingbot=to_block_page))
    assert result.status == Status.WARNING
    assert result.evidencia["agents"]["Bingbot"]["reason"] == "other_url"


async def test_connection_dropped_for_bot_is_blocked(web, analyze_site):
    def reset(request):
        raise httpx.ReadError("connection reset", request=request)

    result = await run(web, analyze_site, by_user_agent(page(), **{"OAI-SearchBot": reset}))
    assert result.status == Status.ERROR
    assert "OAI-SearchBot (sem resposta)" in result.explicacao


@pytest.mark.parametrize(
    "baseline",
    [page(403, "<html>no</html>"), page(200, CHALLENGE)],
    ids=["403", "challenge"],
)
async def test_blocked_browser_baseline_is_indeterminado(web, analyze_site, baseline):
    result = await run(web, analyze_site, by_user_agent(page(), Chrome=baseline))
    assert result.status == Status.UNKNOWN
    assert "até o nosso acesso simulando um navegador" in result.explicacao
    assert "dash.cloudflare.com" in result.como_corrigir


async def test_without_cloudflare_shows_generic_steps(web, analyze_site):
    plain = {"server": "nginx", "content-type": "text/html"}
    blocked = page(403, "<html>no</html>", plain)
    result = await run(web, analyze_site, by_user_agent(page(headers=plain), Googlebot=blocked))
    assert result.status == Status.ERROR
    assert result.evidencia["cdn"] == []
    assert "firewall (WAF)" in result.como_corrigir
    assert "cloudflare" not in result.como_corrigir.lower()


async def test_uses_each_agent_user_agent(web, analyze_site):
    await run(web, analyze_site, page())
    uas = [r.headers["user-agent"] for r in web.requests]
    for token in SEARCH_TOKENS + TRAINING_TOKENS:
        assert any(token in ua for ua in uas), token
    assert not any("Google-Extended" in ua for ua in uas)


@pytest.mark.parametrize(
    ("headers", "cdn"),
    [
        ({"server": "AkamaiGHost"}, ["Akamai"]),
        ({"x-served-by": "cache-gru-sbgr1930027-GRU"}, ["Fastly"]),
        ({"x-amz-cf-id": "abc", "via": "1.1 x.cloudfront.net (CloudFront)"}, ["Amazon CloudFront"]),
        ({"x-vercel-id": "gru1::abc"}, ["Vercel"]),
        ({"X-Sucuri-ID": "1"}, ["Sucuri"]),
        ({"server": "Apache"}, []),
    ],
)
def test_detect_cdn(headers, cdn):
    assert detect_cdn(httpx.Headers(headers)) == cdn
