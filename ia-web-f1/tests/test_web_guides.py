import json
import re
from datetime import date

import httpx
import pytest

from raioxia import cli
from raioxia.checkers import REGISTRY
from raioxia.web import guides, indexnow, seo
from raioxia.web.content import CHECKS
from tests.test_web_ads import report_location

pytestmark = pytest.mark.db

HOST = "raiox.primesolutions.net.br"


def jsonld(html: str) -> list[dict]:
    return [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]


def test_every_check_has_a_guide():
    assert {g.check_id for g in guides.GUIDES if g.check_id} == set(REGISTRY) == {c["id"] for c in CHECKS}
    assert len({g.slug for g in guides.GUIDES}) == len(guides.GUIDES)
    assert sum(w["points"] for w in guides.score_weights()) == 100


@pytest.mark.parametrize("guide", guides.GUIDES, ids=lambda g: g.slug)
def test_guide_page(client, guide):
    response = client.get(guide.path)
    html = response.text
    assert response.status_code == 200
    assert f"<title>{guide.seo_title} | Raio-X IA</title>" in html and len(guide.seo_title) + 13 <= 70
    assert 70 <= len(guide.description) <= 170, len(guide.description)
    assert html.count("<h1") == 1 and f">{guide.title}</h1>" in html
    assert f'<link rel="canonical" href="https://{HOST}{guide.path}">' in html and '<meta property="og:type" content="article">' in html
    article, crumbs = jsonld(html)
    assert article["@type"] == "Article" and article["dateModified"] == guide.updated.isoformat()
    assert [i["item"] for i in crumbs["itemListElement"]] == [f"https://{HOST}/", f"https://{HOST}/guias", f"https://{HOST}{guide.path}"]
    assert 'href="/#formulario"' in html  # every guide leads to the form
    assert html.count('href="/guias/') >= len(guides.GUIDES) - 1  # links to the other guides
    assert "{{" not in html and "{%" not in html


def test_guides_index_and_unknown_guide(client):
    html = client.get("/guias").text
    for g in guides.GUIDES:
        assert f'href="{g.path}"' in html
    assert "Como a nota do Raio-X é calculada" in html and html.count('<td class="border-b border-slate-400/15 py-2 text-right') == 8
    assert [b["@type"] for b in jsonld(html)] == ["CollectionPage", "BreadcrumbList"]
    missing = client.get("/guias/nao-existe")
    assert missing.status_code == 404 and 'href="/guias"' in missing.text


def test_crawler_list_comes_from_the_config(client):
    html = client.get("/guias/robos-de-ia").text
    for row in guides.crawler_rows():
        assert f"<code>{row['token']}</code>" in html
    assert html.index("<code>OAI-SearchBot</code>") < html.index("<code>GPTBot</code>")  # search crawlers first
    assert 'href="https://developers.openai.com/api/docs/bots"' in html
    assert guides.crawlers_reviewed().strftime("%d/%m/%Y") in html


def test_landing_links_every_check_to_its_guide_and_says_who_we_are(client):
    html = client.get("/").text
    assert "<title>Seu site aparece no ChatGPT? Teste grátis | Raio-X IA</title>" in html
    assert re.search(r"<h1[^>]*>Seu site aparece no ChatGPT, no Gemini e no Claude\?</h1>", html)
    assert "51%" in html and "2025-bad-bot-report" in html and "2026-07-01-ai-traffic-options" in html
    for g in guides.GUIDES:
        if g.check_id:
            assert f'href="{g.path}"' in html
    assert "Quem faz o Raio-X IA" in html and "43.757.807/0001-86" in html
    assert '<link rel="preconnect" href="https://challenges.cloudflare.com">' in html


def test_sharing_metadata(client):
    html = client.get("/").text
    assert re.search(r'<meta property="og:image" content="https://raiox\.primesolutions\.net\.br/static/img/og-raiox\.png\?v=[0-9a-f]+">', html)
    assert '<meta property="og:image:width" content="1200">' in html and '<meta name="twitter:card" content="summary_large_image">' in html
    assert f'<meta property="og:url" content="https://{HOST}/">' in html
    assert "og:url" not in client.get("/excluir-dados").text  # noindex pages declare no canonical URL


def test_report_items_link_to_their_guide(client, pipeline_env):
    html = client.get(report_location(client, pipeline_env)).text
    assert html.count("Entenda este item:") == len(re.findall(r'<article id="', html)) > 0
    assert 'href="/guias/robots-txt-para-ia"' in html


def test_indexnow_key_file_and_submission(client, pipeline_env, monkeypatch):
    assert client.get("/indexnow.txt").status_code == 404
    pipeline_env.settings.indexnow_key = "a1b2c3d4e5f60718"
    assert client.get("/indexnow.txt").text == "a1b2c3d4e5f60718"

    assert indexnow.changed_paths(date(2026, 10, 20)) == ["/privacidade", "/bot", "/ferramentas/robots-txt-ia", "/newsletter"]
    assert indexnow.changed_paths(date(2026, 10, 16)) == list(seo.sitemap_pages())
    assert indexnow.changed_paths(date(2099, 1, 1)) == []
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(202)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert indexnow.submit(f"https://{HOST}", "a1b2c3d4e5f60718", ["/", "/guias"], client=http) == 202
    assert sent == [{"host": HOST, "key": "a1b2c3d4e5f60718", "keyLocation": f"https://{HOST}/indexnow.txt",
                     "urlList": [f"https://{HOST}/", f"https://{HOST}/guias"]}]


def test_indexnow_cli_without_key_sends_nothing(monkeypatch, capsys):
    class NoKey:
        indexnow_key = None

    monkeypatch.setattr("raioxia.settings.get_settings", lambda: NoKey())
    assert cli.main(["indexnow"]) == 0 and "nothing sent" in capsys.readouterr().out
