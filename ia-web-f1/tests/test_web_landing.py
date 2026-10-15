import re

import pytest
from sqlalchemy import select

from raioxia.db import Analysis, Consent

pytestmark = pytest.mark.db

FORM = {"url": "exemplo.com.br", "email": "Ana@Exemplo.com.br", "accepted_terms": "1", "cf-turnstile-response": "tok"}


def test_landing_page(client):
    response = client.get("/")
    html = response.text
    assert response.status_code == 200 and 'lang="pt-BR"' in html
    assert "Sua porta está" in html and "aberta para a IA?" in html
    assert 'action="/pedido#formulario"' in html and 'name="url"' in html and 'name="email"' in html
    assert 'class="cf-turnstile' in html and 'data-sitekey="1x00000000000000000000AA"' in html
    assert "https://challenges.cloudflare.com/turnstile/v0/api.js" in html
    assert html.count('<h3 class="font-display text-lg') == 8  # the 8 checks
    assert html.count("<details") == 5
    assert '<link rel="canonical" href="https://raiox.primesolutions.net.br/">' in html
    assert '<meta name="robots"' not in html


def test_form_submission_shows_check_email_page(client, pipeline_env, db, turnstile):
    response = client.post("/pedido", data=FORM | {"newsletter": "1"}, headers={"User-Agent": "Firefox"})
    assert response.status_code == 200
    assert "Agora, confirme seu e-mail" in response.text
    assert "ana@exemplo.com.br" in response.text and "exemplo.com.br" in response.text
    assert turnstile.calls == [("tok", "203.0.113.9")]
    analysis = db.scalars(select(Analysis)).one()
    assert analysis.newsletter_opt_in and str(analysis.requested_ip) == "203.0.113.9"
    assert {c.kind for c in db.scalars(select(Consent))} == {"report_terms", "newsletter"}
    assert pipeline_env.sender.sent[0].to == "ana@exemplo.com.br"


@pytest.mark.parametrize(
    ("change", "field", "text"),
    [
        ({"url": "minha empresa"}, "url", "não parece ser de um site"),
        ({"url": "http://10.0.0.1"}, "url", "Não podemos analisar"),
        ({"email": "ana"}, "email", "não parece válido"),
        ({"accepted_terms": None}, "accepted_terms", "aceitar os termos"),
    ],
)
def test_field_errors_keep_the_values(client, pipeline_env, change, field, text):
    data = {k: v for k, v in (FORM | change).items() if v is not None}
    response = client.post("/pedido", data=data)
    html = response.text
    assert response.status_code == 422 and "Confira os campos marcados" in html and text in html
    assert 'aria-invalid="true"' in html
    if field != "url":
        assert f'value="{FORM["url"]}"' in html
    if field != "email":
        assert 'value="Ana@Exemplo.com.br"' in html
    assert pipeline_env.sender.sent == []


def test_turnstile_failure_and_rate_limit_are_form_level_messages(client, turnstile):
    turnstile.ok = False
    refused = client.post("/pedido", data=FORM)
    assert refused.status_code == 403 and "não é um robô" in refused.text
    assert 'aria-invalid="true"' not in refused.text
    turnstile.ok = True
    for i in range(3):
        client.post("/pedido", data=FORM | {"email": f"p{i}@exemplo.com.br", "url": f"s{i}.com.br"})
    limited = client.post("/pedido", data=FORM | {"email": "p9@exemplo.com.br", "url": "s9.com.br"})
    assert limited.status_code == 429 and "muitos pedidos" in limited.text


def test_html_escaping_of_echoed_values(client):
    html = client.post("/pedido", data=FORM | {"url": '"><script>alert(1)</script>'}).text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_landing_assets_are_csp_compatible(client):
    html = client.get("/").text
    for src in re.findall(r'<script[^>]*src="([^"]+)"', html):
        assert src.startswith(("/static/", "https://challenges.cloudflare.com/"))
