from datetime import timedelta

import pytest
from pydantic import SecretStr

from raioxia.web import tools
from tests.test_web_guides import jsonld

pytestmark = pytest.mark.db

HOST = "raiox.primesolutions.net.br"
TOKEN = "s" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
HOME = "<html><title>x</title></html>"

CONTENT = {
    "intro": "Abertura da edição.",
    "main_story": {"title": "Google muda as respostas", "paragraphs": ["Primeiro.", "Segundo <b>sem tags</b>."],
                   "source_name": "Google", "source_url": "https://blog.google/x"},
    "number_of_week": None,
    "tip": {"title": "Confira o robots.txt", "body": "Abra o arquivo.", "guide_title": "robots.txt para IA",
            "guide_url": f"https://{HOST}/guias/robots-txt-para-ia"},
    "other_news": [{"title": "Outra", "summary": "Resumo.", "source_url": "https://exemplo.com/n"}],
}


def issue(publish_at, **content):
    return {"subject": "Google põe mais links nas respostas de IA", "preheader": "E mais: llms.txt.",
            "publish_at": publish_at.isoformat(), "content": CONTENT | content}


# --- Newsletter archive -----------------------------------------------------------------------------

def test_publishing_needs_the_token_and_valid_links(client, pipeline_env, clock):
    body = issue(clock() - timedelta(hours=1))
    assert client.put("/api/newsletter/issues/1", json=body).status_code == 401
    assert client.put("/api/newsletter/issues/1", json={}).status_code == 401  # auth before validation
    pipeline_env.settings.newsletter_stats_token = SecretStr(TOKEN)
    assert client.put("/api/newsletter/issues/1", json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
    bad = issue(clock(), tip=CONTENT["tip"] | {"guide_url": "javascript:alert(1)"})
    assert client.put("/api/newsletter/issues/1", json=bad, headers=AUTH).status_code == 422
    ok = client.put("/api/newsletter/issues/1", json=body, headers=AUTH)
    assert ok.status_code == 200 and ok.json()["url"] == f"https://{HOST}/newsletter/1"
    assert client.put("/api/newsletter/issues/1", json=body | {"subject": "Novo assunto"}, headers=AUTH).status_code == 200  # idempotent
    assert "Novo assunto" in client.get("/newsletter/1").text


def test_editions_appear_only_after_their_send_time(client, pipeline_env, clock):
    pipeline_env.settings.newsletter_stats_token = SecretStr(TOKEN)
    client.put("/api/newsletter/issues/1", json=issue(clock() - timedelta(days=1)), headers=AUTH)
    client.put("/api/newsletter/issues/2", json=issue(clock() + timedelta(hours=20)) | {"subject": "Ainda não enviada"}, headers=AUTH)

    index = client.get("/newsletter").text
    assert 'href="/newsletter/1"' in index and "Ainda não enviada" not in index
    assert client.get("/newsletter/2").status_code == 404
    sitemap = client.get("/sitemap.xml").text
    assert f"<loc>https://{HOST}/newsletter/1</loc>" in sitemap and "/newsletter/2<" not in sitemap
    llms = client.get("/llms.txt").text
    assert f"(https://{HOST}/newsletter/1)" in llms and "Ainda não enviada" not in llms

    page = client.get("/newsletter/1")
    html = page.text
    assert page.status_code == 200 and "<h1" in html and "Google muda as respostas" in html
    assert "Segundo &lt;b&gt;sem tags&lt;/b&gt;." in html  # text is escaped
    assert 'href="https://blog.google/x"' in html and f'href="https://{HOST}/guias/robots-txt-para-ia"' in html
    assert "Número da semana" not in html  # null: no box
    assert [b["@type"] for b in jsonld(html)] == ["Article", "BreadcrumbList"]
    assert f'<link rel="canonical" href="https://{HOST}/newsletter/1">' in html

    clock.now += timedelta(days=1)
    assert client.get("/newsletter/2").status_code == 200
    assert client.delete("/api/newsletter/issues/2", headers=AUTH).json() == {"ok": True, "deleted": True}
    assert client.get("/newsletter/2").status_code == 404


def test_empty_archive_page(client):
    html = client.get("/newsletter").text
    assert "A primeira edição ainda não saiu" in html and 'href="/guias"' in html


# --- robots.txt AI tester ---------------------------------------------------------------------------

async def test_robots_report_groups_crawlers(web, make_fetcher):
    web.add("https://site.example/", HOME)
    web.add("https://site.example/robots.txt", "User-agent: GPTBot\nDisallow: /\n\nUser-agent: OAI-SearchBot\nAllow: /$\nDisallow: /\n", content_type="text/plain")
    report = await tools.robots_ai_report("site.example", fetcher=make_fetcher())
    rows = {r["token"]: r for g in report["groups"] for r in g["rows"]}
    assert rows["GPTBot"]["label"] == "Bloqueado" and rows["GPTBot"]["rule"] == "Disallow: /"
    assert rows["OAI-SearchBot"]["label"] == "Só a página inicial"
    assert rows["ClaudeBot"]["label"] == "Liberado"
    assert [g["title"] for g in report["groups"]] == ["Busca e respostas", "A pedido de uma pessoa", "Treino de modelos"]
    assert report["search_open"] == report["search_total"] - 1 and report["host"] == "site.example"


async def test_robots_report_refuses_bad_and_opted_out_sites(monkeypatch):
    with pytest.raises(tools.ToolError, match="não parece ser de um site"):
        await tools.robots_ai_report("isso não é um site")
    monkeypatch.setattr(tools, "is_opted_out", lambda host: host == "naoanalise.com.br")
    with pytest.raises(tools.ToolError, match="pediu para ele não ser analisado"):
        await tools.robots_ai_report("naoanalise.com.br")


def test_tool_page_and_form(client, turnstile, monkeypatch):
    page = client.get("/ferramentas/robots-txt-ia")
    assert page.status_code == 200 and "cf-turnstile" in page.text and '<link rel="canonical"' in page.text
    assert [b["@type"] for b in jsonld(page.text)] == ["WebApplication", "BreadcrumbList"]

    calls = []

    async def fake_report(url, fetcher=None):
        calls.append(url)
        return {"host": "site.example", "robots_url": "https://site.example/robots.txt", "state": "encontrado", "is_html": False,
                "status": "ok", "explicacao": "Tudo certo.", "como_corrigir": "", "search_open": 6, "search_total": 6,
                "groups": [{"title": "Busca e respostas", "note": "n", "rows": [{"token": "OAI-SearchBot", "company": "OpenAI", "label": "Liberado", "tone": "ok", "rule": None}]}]}

    monkeypatch.setattr(tools, "robots_ai_report", fake_report)
    form = {"url": "site.example", "cf-turnstile-response": "tok"}
    result = client.post("/ferramentas/robots-txt-ia", data=form)
    assert result.status_code == 200 and "Resultado para site.example" in result.text and "6 de 6" in result.text
    assert '<meta name="robots" content="noindex, nofollow">' in result.text and calls == ["site.example"]

    assert client.post("/ferramentas/robots-txt-ia", data=form | {"url": "nada"}).status_code == 422
    turnstile.ok = False
    refused = client.post("/ferramentas/robots-txt-ia", data=form)
    assert refused.status_code == 403 and "não é um robô" in refused.text and len(calls) == 1


def test_tool_rate_limits(client, turnstile, monkeypatch):
    async def fake_report(url, fetcher=None):
        return {"host": "x", "robots_url": "", "state": "", "is_html": False, "status": "ok", "explicacao": "", "como_corrigir": "",
                "search_open": 0, "search_total": 0, "groups": []}

    monkeypatch.setattr(tools, "robots_ai_report", fake_report)
    statuses = [client.post("/ferramentas/robots-txt-ia", data={"url": "mesmo.com.br", "cf-turnstile-response": "t"}).status_code for _ in range(11)]
    assert statuses[:10] == [200] * 10 and statuses[10] == 429  # 10 per site per hour


def test_new_pages_are_linked_and_listed(client):
    footer = client.get("/termos").text
    assert 'href="/ferramentas/robots-txt-ia"' in footer and 'href="/newsletter"' in footer
    assert 'href="/ferramentas/robots-txt-ia"' in client.get("/guias/robots-txt-para-ia").text
    assert 'href="/newsletter"' in client.get("/").text
    sitemap = client.get("/sitemap.xml").text
    assert f"<loc>https://{HOST}/ferramentas/robots-txt-ia</loc>" in sitemap and f"<loc>https://{HOST}/newsletter</loc>" in sitemap
    bot = client.get("/bot").text
    assert "teste de robots.txt para IA" in bot
