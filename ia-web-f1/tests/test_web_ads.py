import re

import pytest
from sqlalchemy import select

from raioxia.db import Analysis

pytestmark = pytest.mark.db

FORM = {"url": "exemplo.com.br", "email": "ana@exemplo.com.br", "accepted_terms": "1", "cf-turnstile-response": "tok"}
CLIENT = "ca-pub-5217885742353376"


def report_location(client, env) -> str:
    client.post("/pedido", data=FORM)
    email = next(e for e in reversed(env.sender.sent) if e.subject.startswith("Confirme"))
    link = re.search(r"https://raiox\.primesolutions\.net\.br(/confirmar/\S+)", email.text).group(1)
    return client.get(link).headers["location"]


def test_without_adsense_the_report_shows_placeholders_and_strict_csp(client, pipeline_env):
    response = client.get(report_location(client, pipeline_env))
    assert response.text.count('data-ad-slot="report-') == 2 and "adsbygoogle" not in response.text
    assert "script-src 'self' https://challenges.cloudflare.com" in response.headers["content-security-policy"]


def test_before_approval_loads_adsense_but_shows_no_units(client, pipeline_env):
    pipeline_env.settings.adsense_client = CLIENT
    response = client.get(report_location(client, pipeline_env))
    html, csp = response.text, response.headers["content-security-policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert f'src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={CLIENT}" crossorigin="anonymous" nonce="{nonce}"' in html
    assert html.index("js/ads-init.js") < html.index("adsbygoogle.js")  # the flag is set before the loader runs
    assert f'js/app.js?v=' in html and f'defer nonce="{nonce}"' in html
    assert "'strict-dynamic'" in csp and "frame-ancestors 'none'" in csp
    assert "<ins" not in html and 'data-ad-slot="report-' not in html  # no empty boxes in production


def test_with_slot_ids_renders_real_units(client, pipeline_env):
    pipeline_env.settings.adsense_client = CLIENT
    pipeline_env.settings.adsense_slot_top = "1111111111"
    pipeline_env.settings.adsense_slot_bottom = "2222222222"
    html = client.get(report_location(client, pipeline_env)).text
    units = re.findall(r'<ins class="adsbygoogle block" data-ad-client="([^"]+)" data-ad-slot="(\d+)"', html)
    assert units == [(CLIENT, "1111111111"), (CLIENT, "2222222222")]
    assert "nonce" not in client.get("/r/desconhecido").text  # unknown report: plain 404, no ads


def test_no_ads_where_there_is_no_content(client, pipeline_env, db):
    pipeline_env.settings.adsense_client = CLIENT
    pipeline_env.settings.adsense_slot_top = "1111111111"
    client.post("/pedido", data=FORM)
    pending = db.scalars(select(Analysis)).one()
    for path in ("/", f"/r/{pending.report_token}", "/privacidade", "/bot"):
        response = client.get(path)
        assert "adsbygoogle" not in response.text, path
        assert "'strict-dynamic'" not in response.headers["content-security-policy"], path


def test_robots_lets_the_adsense_crawler_read_reports(client):
    robots = client.get("/robots.txt").text
    assert "User-agent: Mediapartners-Google\nAllow: /" in robots
    assert "User-agent: *\nAllow: /\nDisallow: /r/" in robots


def test_personalization_consent_notice_only_with_real_units(client, pipeline_env):
    pipeline_env.settings.adsense_client = CLIENT
    location = report_location(client, pipeline_env)
    assert "data-ads-consent" not in client.get(location).text  # before approval: no ads, nothing to consent to

    pipeline_env.settings.adsense_slot_top = "1111111111"
    html = client.get(location).text
    assert '<section data-ads-consent hidden aria-labelledby="anuncios-titulo"' in html  # app.js shows it if no choice yet
    assert 'data-ads-choice="npa"' in html and 'data-ads-choice="personalized"' in html
    assert '<button type="button" data-ads-preferences' in html and 'href="/privacidade#anuncios"' in html


def test_privacy_explains_the_ad_choice(client):
    html = client.get("/privacidade").text
    assert 'id="anuncios"' in html and "anúncios personalizados" in html and "Preferências de anúncios" in html
    assert "só com o seu consentimento (art. 7º, I)" in html


def test_auto_ads_put_adsense_and_the_notice_on_the_landing(client, pipeline_env):
    pipeline_env.settings.adsense_client = CLIENT
    pipeline_env.settings.adsense_auto_ads = True
    response = client.get("/")
    html, csp = response.text, response.headers["content-security-policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert "'strict-dynamic'" in csp
    assert f'adsbygoogle.js?client={CLIENT}" crossorigin="anonymous" nonce="{nonce}"' in html
    assert f'turnstile/v0/api.js" async defer nonce="{nonce}"' in html  # Turnstile still runs under the ads CSP
    assert re.search(rf'js/app\.js\?v=[0-9a-f]+" defer nonce="{nonce}"', html)
    assert "<ins" not in html and "data-ads-consent" in html and "data-ads-preferences" in html
    # form errors re-render the landing with the same setup
    refused = client.post("/pedido", data=FORM | {"email": "x"})
    assert "'strict-dynamic'" in refused.headers["content-security-policy"] and "data-ads-consent" in refused.text
    # pages without content still never load ads
    for path in ("/privacidade", "/bot", "/excluir-dados"):
        assert "adsbygoogle" not in client.get(path).text, path


def test_auto_ads_off_keeps_the_landing_strict(client, pipeline_env):
    pipeline_env.settings.adsense_client = CLIENT
    response = client.get("/")
    assert "adsbygoogle" not in response.text and "data-ads-consent" not in response.text
    assert "'strict-dynamic'" not in response.headers["content-security-policy"]
