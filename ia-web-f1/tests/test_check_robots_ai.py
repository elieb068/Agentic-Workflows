from pathlib import Path

import httpx
import pytest

from raioxia.checkers.robots_ai import RobotsAIChecker
from raioxia.models import Status

FIXTURES = Path(__file__).parent / "fixtures" / "robots"
HOME = "<html><title>x</title></html>"


async def run(web, analyze_site, robots: str | None = None, *, status: int = 200, url="https://site.example"):
    web.add(f"{url}/", HOME)
    if robots is not None:
        web.add(f"{url}/robots.txt", robots, status=status, content_type="text/plain")
    report = await analyze_site(f"{url}/", checkers=[RobotsAIChecker()])
    return report.checks[0]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def verdicts(result) -> dict[str, str]:
    return {token: a["verdict"] for token, a in result.evidencia["agents"].items()}


async def test_missing_robots_allows_everything(web, analyze_site):
    result = await run(web, analyze_site)  # FakeWeb answers 404
    assert result.status == Status.OK
    assert result.evidencia["state"] == "missing"
    assert set(verdicts(result).values()) == {"permitido"}
    assert "não tem robots.txt" in result.explicacao


async def test_allow_all(web, analyze_site):
    result = await run(web, analyze_site, fixture("allow_all.txt"))
    assert result.status == Status.OK
    assert "15 robôs" in result.explicacao


@pytest.mark.parametrize("status", [500, 503, 429])
async def test_server_error_means_full_block(web, analyze_site, status):
    result = await run(web, analyze_site, "oops", status=status)
    assert result.status == Status.ERROR
    assert result.evidencia["state"] == "unreachable"
    assert set(verdicts(result).values()) == {"bloqueado"}
    assert str(status) in result.explicacao


async def test_unreachable_robots_is_error(web, analyze_site):
    web.add("https://site.example/", HOME)

    def stall(request):
        raise httpx.ReadTimeout("slow", request=request)

    web.route("https://site.example/robots.txt", stall)
    report = await analyze_site("https://site.example/", checkers=[RobotsAIChecker()])
    result = report.checks[0]
    assert result.status == Status.ERROR
    assert result.evidencia["fetch_error"] == "timeout"
    assert "não respondeu" in result.explicacao


async def test_block_all_is_error_and_names_search_bots(web, analyze_site):
    result = await run(web, analyze_site, fixture("block_all.txt"))
    assert result.status == Status.ERROR
    assert verdicts(result)["Googlebot"] == "bloqueado"
    assert "OAI-SearchBot (OpenAI)" in result.explicacao
    agent = result.evidencia["agents"]["Googlebot"]
    assert agent["matched_by"] == "*" and agent["rule"] == "Disallow: /" and agent["rule_line"] == 3


async def test_cloudflare_managed_blocks_training_only(web, analyze_site):
    result = await run(web, analyze_site, fixture("cloudflare_managed.txt"))
    assert result.status == Status.OK  # training block is informational, not penalized
    v = verdicts(result)
    for token in ["GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended", "CCBot", "Meta-ExternalAgent", "Bytespider"]:
        assert v[token] == "bloqueado", token
    for token in ["OAI-SearchBot", "ChatGPT-User", "Claude-SearchBot", "Claude-User", "PerplexityBot", "Googlebot", "Bingbot"]:
        assert v[token] == "permitido", token
    assert "escolha legítima" in result.explicacao
    assert "estão liberados" in result.explicacao


async def test_search_bots_blocked(web, analyze_site):
    result = await run(web, analyze_site, fixture("block_search.txt"))
    assert result.status == Status.ERROR
    assert {t for t, v in verdicts(result).items() if v == "bloqueado"} == {"OAI-SearchBot", "PerplexityBot"}
    assert "User-agent: OAI-SearchBot" in result.como_corrigir


async def test_home_only_is_partial_warning(web, analyze_site):
    result = await run(web, analyze_site, fixture("home_only.txt"))
    assert result.status == Status.WARNING
    assert verdicts(result)["Claude-SearchBot"] == "parcial"
    assert "só podem ler a sua página inicial" in result.explicacao


async def test_retired_tokens_have_no_effect(web, analyze_site):
    result = await run(web, analyze_site, fixture("retired_tokens.txt"))
    assert result.status == Status.WARNING
    assert verdicts(result)["ClaudeBot"] == "permitido"
    assert [r["token"] for r in result.evidencia["retired_rules"]] == ["anthropic-ai", "Claude-Web"]
    assert "nome antigo" in result.explicacao
    assert 'por "User-agent: ClaudeBot" e "User-agent: Claude-User".' in result.como_corrigir


async def test_html_instead_of_robots(web, analyze_site):
    web.add("https://site.example/", HOME)
    web.add("https://site.example/robots.txt", "<!doctype html><html><body>Home</body></html>")
    report = await analyze_site("https://site.example/", checkers=[RobotsAIChecker()])
    result = report.checks[0]
    assert result.status == Status.WARNING
    assert result.evidencia["is_html"] is True
    assert set(verdicts(result).values()) == {"permitido"}


async def test_robots_read_from_host_after_redirect(web, analyze_site):
    web.redirect("https://site.example/", "https://www.site.example/")
    web.add("https://www.site.example/", HOME)
    web.add("https://www.site.example/robots.txt", fixture("block_all.txt"), content_type="text/plain")
    report = await analyze_site("site.example", checkers=[RobotsAIChecker()])
    assert report.checks[0].evidencia["robots_url"] == "https://www.site.example/robots.txt"
    assert report.checks[0].status == Status.ERROR


async def test_training_block_does_not_hide_other_warnings(web, analyze_site):
    robots = fixture("cloudflare_managed.txt") + "\nUser-agent: anthropic-ai\nDisallow: /\n"
    result = await run(web, analyze_site, robots)
    assert result.status == Status.WARNING  # from the retired token, not from training
    assert "escolha legítima" in result.explicacao and "nome antigo" in result.explicacao
