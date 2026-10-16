"""Our own site must pass our own checks: the analyzer runs against the app in-process (no network)."""

import json
import re

import httpx
import pytest

from raioxia.net.fetcher import FetcherSettings, SafeFetcher
from raioxia.runner import analyze
from tests.conftest import FakeResolver

pytestmark = pytest.mark.db

HOST = "raiox.primesolutions.net.br"


def test_robots_sitemap_llms(client):
    robots = client.get("/robots.txt")
    assert robots.headers["content-type"].startswith("text/plain")
    assert "User-agent: *\nAllow: /\nDisallow: /r/" in robots.text and "Disallow: /api/" in robots.text
    assert f"Sitemap: https://{HOST}/sitemap.xml" in robots.text
    sitemap = client.get("/sitemap.xml")
    assert sitemap.headers["content-type"].startswith("application/xml")
    locs = re.findall(r"<url><loc>([^<]+)</loc><lastmod>(\d{4}-\d{2}-\d{2})</lastmod></url>", sitemap.text)
    paths = [loc.removeprefix(f"https://{HOST}") for loc, _ in locs]
    assert paths[:5] == ["/", "/guias", "/privacidade", "/termos", "/bot"] and "/excluir-dados" not in paths
    assert len(paths) == 5 + 9 and all(p.startswith("/guias/") for p in paths[5:])
    assert len(locs) == sitemap.text.count("<url>")  # every URL has a <lastmod>
    llms = client.get("/llms.txt").text
    assert llms.startswith("# Raio-X IA\n\n> ") and "## Páginas" in llms and "## Guias" in llms
    assert f"(https://{HOST}/guias/o-que-e-llms-txt)" in llms


def test_landing_jsonld(client):
    html = client.get("/").text
    blocks = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    assert [b["@type"] for b in blocks] == ["Organization", "WebApplication", "FAQPage"]
    assert blocks[0]["taxID"] == "43.757.807/0001-86" and blocks[1]["offers"]["price"] == "0"
    assert len(blocks[2]["mainEntity"]) == 5


async def _analyze_ourselves(client, render: bool):
    resolver = FakeResolver()
    resolver.set(HOST, ["93.184.215.10"])
    # llms.txt links to the company site; in production it's live, here it is answered in-process too.
    resolver.set("www.primesolutions.net.br", ["93.184.215.11"])
    fetcher = SafeFetcher(FetcherSettings(), resolver=resolver, transport=httpx.ASGITransport(app=client.app))
    try:
        return await analyze(f"https://{HOST}/", fetcher=fetcher, render=render)
    finally:
        await fetcher.aclose()


async def test_our_landing_scores_100_on_our_own_checks(client):
    report = await _analyze_ourselves(client, render=False)
    statuses = {c.id: c.status.value for c in report.checks}
    assert statuses == {
        "robots_ai": "ok", "edge_block": "ok", "llms_txt": "ok", "meta_robots": "ok",
        "js_dependency": "indeterminado", "structured_data": "ok", "sitemap": "ok", "basics": "ok",
    }, [(c.id, c.explicacao) for c in report.checks if c.status.value != "ok"]
    assert report.score == 100 and report.band.label == "Porta aberta"


@pytest.mark.browser
@pytest.mark.usefixtures("require_browser")
async def test_our_landing_needs_no_javascript(client):
    report = await _analyze_ourselves(client, render=True)
    js = next(c for c in report.checks if c.id == "js_dependency")
    assert js.status.value == "ok", js.explicacao
    assert report.score == 100
